#!/usr/bin/env python3
"""CLIP-assisted motif pre-tagging: score untagged works against the controlled
visual vocabulary and emit ranked, confidence-banded suggestions for human review.

Runs INSIDE the clip-api container (has torch / open_clip / numpy + Qdrant access):

    docker compose exec clip-api python -m clip_api.motif_pretag [--targets ...] [--sample N]

Signals per (item, motif), all leakage-safe:
  - centroid : cosine to the mean image embedding of genuine exemplars
               (sparse motifs cold-start from the box prior, pruned by coherence)
  - knn      : weighted vote of the visually nearest genuine-tagged works
  - text     : zero-shot cosine to a prompt ensemble (fallback / cold-start)

Combined via per-motif z-scores, then calibrated against the genuine-tagged set
(leave-one-out) so each motif's confidence bands hit a precision target. Nothing
is written to the catalog: outputs are staging artifacts under motif_out/.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from clip_api import embeddings
from clip_api.config import load_settings
from clip_api.qdrant import request_json

HERE = Path(__file__).resolve().parent
VOCAB_PATH = HERE / "motif_vocab.json"
OUT_DIR = HERE / "motif_out"
DATA_PATH = OUT_DIR / "motif_data.json"

# defaults (CLI-overridable)
KNN_K = 25
MIN_GENUINE = 8          # below this, augment centroid with box-prior weak positives
MIN_CALIB_POS = 5        # below this, a motif is "uncalibrated" (no high band)
MIN_SUPPORT = 5          # min items at/above a threshold to trust its precision
W_CENTROID, W_KNN, W_TEXT = 0.5, 0.3, 0.2


# ── Qdrant ───────────────────────────────────────────────────────────────────
def load_vectors(settings) -> tuple[dict[int, np.ndarray], dict[int, str]]:
    """Scroll the whole collection, returning {id: visual_vec} and {id: thumb_url}."""
    vecs: dict[int, np.ndarray] = {}
    thumbs: dict[int, str] = {}
    url = f"{settings.qdrant_url}/collections/{settings.qdrant_collection}/points/scroll"
    offset = None
    while True:
        body = {
            "limit": 1000,
            "with_vector": [settings.vector_name],
            "with_payload": ["thumb_url"],
        }
        if offset is not None:
            body["offset"] = offset
        resp = request_json("POST", url, json=body, timeout=60.0)
        if resp.status_code != 200:
            sys.exit(f"Qdrant scroll failed {resp.status_code}: {resp.text[:300]}")
        result = resp.json().get("result", {})
        for p in result.get("points", []):
            try:
                pid = int(p["id"])
            except (KeyError, ValueError, TypeError):
                continue
            vec = (p.get("vector") or {}).get(settings.vector_name)
            if not vec:
                continue
            v = np.asarray(vec, dtype=np.float32)
            n = np.linalg.norm(v)
            if n > 0:
                v = v / n
            vecs[pid] = v
            thumbs[pid] = (p.get("payload") or {}).get("thumb_url", "")
        offset = result.get("next_page_offset")
        if offset is None:
            break
    return vecs, thumbs


# ── centroids ────────────────────────────────────────────────────────────────
def build_centroids(motifs, genuine_pos, box_pos, vecs):
    """Per motif: (unit_centroid|None, sum_vec, count) over genuine (+pruned box prior)."""
    cents: dict[str, Optional[np.ndarray]] = {}
    sums: dict[str, np.ndarray] = {}
    counts: dict[str, int] = {}
    for m in motifs:
        gen = [vecs[i] for i in genuine_pos.get(m, []) if i in vecs]
        gsum = np.sum(gen, axis=0) if gen else np.zeros(512, dtype=np.float32)
        gcount = len(gen)

        members_sum, members_count = gsum.copy(), gcount
        if gcount < MIN_GENUINE:
            weak = [vecs[i] for i in box_pos.get(m, []) if i in vecs]
            if weak:
                prov = (gsum + np.sum(weak, axis=0)) / (gcount + len(weak))
                pn = np.linalg.norm(prov)
                if pn > 0:
                    prov = prov / pn
                    sims = np.array([float(w @ prov) for w in weak])
                    keep = sims >= np.quantile(sims, 0.4)  # drop the least-coherent 40%
                    for w, k in zip(weak, keep):
                        if k:
                            members_sum = members_sum + w
                            members_count += 1

        counts[m] = gcount  # genuine count (for LOO bookkeeping)
        sums[m] = members_sum
        if members_count > 0:
            unit = members_sum / members_count
            un = np.linalg.norm(unit)
            cents[m] = unit / un if un > 0 else None
            sums[m] = members_sum
            counts[m] = members_count
        else:
            cents[m] = None
    return cents, sums, counts


def find_threshold(scores: np.ndarray, labels: np.ndarray, bar: float):
    """Most-inclusive score threshold whose cumulative precision (scanning high->low)
    meets `bar` with adequate support. Returns (threshold|None, support, precision)."""
    order = np.argsort(-scores)
    tp = n = 0
    best = None
    for idx in order:
        n += 1
        tp += int(labels[idx])
        prec = tp / n
        if n >= MIN_SUPPORT and prec >= bar:
            best = (float(scores[idx]), n, prec)
    return best if best else (None, 0, 0.0)


def precision_at(x: float, scores: np.ndarray, labels: np.ndarray) -> float:
    """Laplace-smoothed precision among calib items scoring >= x."""
    mask = scores >= x
    n = int(mask.sum())
    tp = int(labels[mask].sum())
    return (tp + 1.0) / (n + 2.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default="untagged", help="untagged | all | comma,ids")
    ap.add_argument("--sample", type=int, default=0, help="cap number of targets (0 = all)")
    ap.add_argument("--top-n", type=int, default=5, help="max suggestions per item")
    ap.add_argument("--min-band", choices=["high", "medium", "low"], default="medium")
    ap.add_argument("--knn-k", type=int, default=KNN_K)
    ap.add_argument("--high-prec", type=float, default=0.90)
    ap.add_argument("--med-prec", type=float, default=0.60)
    args = ap.parse_args()

    t0 = time.time()
    vocab = json.loads(VOCAB_PATH.read_text())
    data = json.loads(DATA_PATH.read_text())
    motifs = list(vocab["canonical_visual"])
    prompts = vocab.get("prompts", {})
    default_prompts = prompts.get("_default", ["{m}"])
    items = data["items"]
    settings = load_settings()

    print("loading vectors from Qdrant ...", flush=True)
    vecs, thumbs = load_vectors(settings)
    print(f"  {len(vecs)} points with {settings.vector_name}", flush=True)

    # ── label sets (only items that have a vector) ──────────────────────────
    genuine_pos: dict[str, list[int]] = defaultdict(list)
    box_pos: dict[str, list[int]] = defaultdict(list)
    tagged_ids: list[int] = []
    item_genuine: dict[int, set[str]] = {}
    for sid, rec in items.items():
        iid = int(sid)
        if iid not in vecs:
            continue
        gen = set(rec.get("genuine") or [])
        item_genuine[iid] = gen
        if gen:
            tagged_ids.append(iid)
            for m in gen:
                genuine_pos[m].append(iid)
        prior = rec.get("box_prior")
        if prior in motifs:
            box_pos[prior].append(iid)

    # ── target selection ────────────────────────────────────────────────────
    if args.targets == "untagged":
        target_ids = [int(s) for s, r in items.items()
                      if int(s) in vecs and not (r.get("genuine"))]
    elif args.targets == "all":
        target_ids = [int(s) for s in items if int(s) in vecs]
    else:
        want = {int(x) for x in args.targets.split(",") if x.strip()}
        target_ids = [i for i in want if i in vecs]
    target_ids.sort()
    no_vec_targets = sum(
        1 for s, r in items.items()
        if not (r.get("genuine")) and int(s) not in vecs
    )
    if args.sample and len(target_ids) > args.sample:
        # stratify by era so the sample spans both Haiku and Opus items
        opus = [i for i in target_ids if not items[str(i)]["haiku"]]
        haiku = [i for i in target_ids if items[str(i)]["haiku"]]
        half = args.sample // 2
        target_ids = sorted(opus[:half] + haiku[: args.sample - min(half, len(opus))])

    print(f"tagged exemplars: {len(tagged_ids)} | targets: {len(target_ids)} "
          f"| targets w/o embedding: {no_vec_targets}", flush=True)

    # ── centroids + text vectors ────────────────────────────────────────────
    cents, cent_sums, cent_counts = build_centroids(motifs, genuine_pos, box_pos, vecs)

    print("embedding zero-shot prompts ...", flush=True)
    textvec: dict[str, np.ndarray] = {}
    for m in motifs:
        tmpl = prompts.get(m, [p.replace("{m}", m.lower()) for p in default_prompts])
        embs = [np.asarray(embeddings.embed_text(t), dtype=np.float32) for t in tmpl]
        tv = np.mean(embs, axis=0)
        n = np.linalg.norm(tv)
        textvec[m] = tv / n if n > 0 else tv

    Cunit = np.stack([cents[m] if cents[m] is not None else np.zeros(512, np.float32)
                      for m in motifs])
    has_cent = np.array([cents[m] is not None for m in motifs])
    Tx = np.stack([textvec[m] for m in motifs])

    # ── score everyone we might threshold: calib (tagged) + targets ─────────
    score_ids = sorted(set(tagged_ids) | set(target_ids))
    V = np.stack([vecs[i] for i in score_ids])           # (N, 512)
    idx_of = {i: k for k, i in enumerate(score_ids)}

    s_cent = V @ Cunit.T                                  # (N, M)
    s_text = V @ Tx.T

    # leave-one-out centroid for genuine positives (remove self from the mean)
    for mj, m in enumerate(motifs):
        if cents[m] is None:
            continue
        cnt = cent_counts[m]
        if cnt <= 1:
            continue
        ssum = cent_sums[m]
        for i in genuine_pos.get(m, []):
            if i not in idx_of:
                continue
            loo = (ssum - vecs[i]) / (cnt - 1)
            ln = np.linalg.norm(loo)
            if ln > 0:
                s_cent[idx_of[i], mj] = float(vecs[i] @ (loo / ln))

    # knn vote: neighbours among tagged (exclude self)
    Tg = np.stack([vecs[i] for i in tagged_ids])         # (n_tg, 512)
    L = np.zeros((len(tagged_ids), len(motifs)), dtype=np.float32)
    for r, i in enumerate(tagged_ids):
        for m in item_genuine[i]:
            L[r, motifs.index(m)] = 1.0
    tg_pos = {i: r for r, i in enumerate(tagged_ids)}
    s_knn = np.zeros((len(score_ids), len(motifs)), dtype=np.float32)
    K = args.knn_k
    for k, i in enumerate(score_ids):
        sims = Tg @ V[k]                                  # (n_tg,)
        self_r = tg_pos.get(i)
        if self_r is not None:
            sims[self_r] = -1.0
        top = np.argpartition(-sims, min(K, len(sims) - 1))[:K]
        ts = sims[top].clip(min=0.0)
        denom = float(ts.sum())
        if denom > 0:
            s_knn[k] = (ts @ L[top]) / denom

    # ── per-motif z-scores -> combined ──────────────────────────────────────
    def zcols(mat):
        mu = mat.mean(axis=0)
        sd = mat.std(axis=0)
        sd[sd < 1e-6] = 1e-6
        return (mat - mu) / sd

    z_cent, z_text, z_knn = zcols(s_cent), zcols(s_text), zcols(s_knn)
    combined = np.zeros_like(z_cent)
    for mj in range(len(motifs)):
        parts, weights = [], []
        if has_cent[mj]:
            parts.append(z_cent[:, mj]); weights.append(W_CENTROID)
        parts.append(z_knn[:, mj]); weights.append(W_KNN)
        parts.append(z_text[:, mj]); weights.append(W_TEXT)
        w = np.array(weights) / sum(weights)
        combined[:, mj] = np.sum([p * wi for p, wi in zip(parts, w)], axis=0)

    # ── calibrate per motif on the tagged set ───────────────────────────────
    calib_rows = np.array([idx_of[i] for i in tagged_ids])
    calib = {}
    for mj, m in enumerate(motifs):
        labels = L[:, mj]                                  # aligned to tagged_ids
        scores = combined[calib_rows, mj]
        pos = int(labels.sum())
        order = np.argsort(-scores)
        pk = {f"p@{k}": round(float(labels[order[:k]].mean()), 3)
              for k in (10, 25, 50)} if pos else {}
        if pos < MIN_CALIB_POS:
            calib[m] = {"uncalibrated": True, "genuine": len(genuine_pos.get(m, [])),
                        "calib_pos": pos, "high_thr": None, "med_thr": None,
                        "centroid": bool(has_cent[mj]), **pk}
            continue
        hi, hi_n, hi_p = find_threshold(scores, labels, args.high_prec)
        md, md_n, md_p = find_threshold(scores, labels, args.med_prec)
        calib[m] = {
            "uncalibrated": False, "genuine": len(genuine_pos.get(m, [])),
            "calib_pos": pos, "centroid": bool(has_cent[mj]),
            "high_thr": hi, "high_support": hi_n, "high_prec": round(hi_p, 3),
            "med_thr": md, "med_support": md_n, "med_prec": round(md_p, 3),
            **pk, "_scores": scores, "_labels": labels,
        }

    # ── emit suggestions for targets ────────────────────────────────────────
    band_rank = {"high": 3, "medium": 2, "low": 1}
    min_rank = band_rank[args.min_band]
    rows, jsonl = [], []
    per_band = {"high": 0, "medium": 0, "low": 0}
    items_with_high = items_with_med = items_with_none = 0

    for i in target_ids:
        k = idx_of[i]
        cand = []
        for mj, m in enumerate(motifs):
            c = calib[m]
            score = float(combined[k, mj])
            if c.get("uncalibrated"):
                band = "low"
                conf = 0.0
            else:
                if c["high_thr"] is not None and score >= c["high_thr"]:
                    band = "high"
                elif c["med_thr"] is not None and score >= c["med_thr"]:
                    band = "medium"
                else:
                    band = "low"
                conf = precision_at(score, c["_scores"], c["_labels"])
            # dominant method for display
            zvals = {"centroid": float(z_cent[k, mj]) if has_cent[mj] else -9,
                     "knn": float(z_knn[k, mj]), "text": float(z_text[k, mj])}
            method = max(zvals, key=zvals.get)
            cand.append((m, band, conf, method,
                         float(s_cent[k, mj]), float(s_knn[k, mj]), float(s_text[k, mj])))

        cand.sort(key=lambda r: (band_rank[r[1]], r[2]), reverse=True)
        kept = [c for c in cand if band_rank[c[1]] >= min_rank][: args.top_n]

        if any(c[1] == "high" for c in kept):
            items_with_high += 1
        elif any(c[1] == "medium" for c in kept):
            items_with_med += 1
        else:
            items_with_none += 1

        for (m, band, conf, method, sc, sk, st) in kept:
            per_band[band] += 1
            rows.append([i, m, round(conf, 4), method, band])
        jsonl.append({"item_id": i, "thumb": thumbs.get(i, ""),
                      "suggestions": [{"motif": m, "band": band, "confidence": round(conf, 4),
                                       "method": method, "s_centroid": round(sc, 4),
                                       "s_knn": round(sk, 4), "s_text": round(st, 4)}
                                      for (m, band, conf, method, sc, sk, st) in kept]})

    # ── write artifacts ─────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with (OUT_DIR / "motif_suggestions.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "motif", "score", "method", "band"])
        w.writerows(rows)
    with (OUT_DIR / "motif_suggestions.jsonl").open("w") as f:
        for obj in jsonl:
            f.write(json.dumps(obj) + "\n")

    calib_out = {}
    for m, c in calib.items():
        c2 = {k: v for k, v in c.items() if not k.startswith("_")}
        calib_out[m] = c2
    (OUT_DIR / "motif_calibration.json").write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {"knn_k": args.knn_k, "high_prec": args.high_prec,
                   "med_prec": args.med_prec, "min_genuine": MIN_GENUINE},
        "motifs": calib_out,
    }, indent=2))

    write_report(args, motifs, calib, genuine_pos, target_ids, no_vec_targets,
                 per_band, items_with_high, items_with_med, items_with_none, len(vecs))

    print(f"\nwrote {len(rows)} suggestions across {len(target_ids)} targets "
          f"in {time.time()-t0:.0f}s", flush=True)
    print(f"  band totals: {per_band}")
    print(f"  items with >=1 high: {items_with_high} | medium-only: {items_with_med} "
          f"| none: {items_with_none}")


def write_report(args, motifs, calib, genuine_pos, target_ids, no_vec_targets,
                 per_band, n_high, n_med, n_none, n_vecs):
    lines = ["# Motif pre-tagging: coverage & confidence report", ""]
    lines.append(f"_generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_")
    lines += ["", "## Run", "",
              f"- targets: `{args.targets}`  (scored: {len(target_ids)})",
              f"- targets without a CLIP embedding (need `make ingest`): {no_vec_targets}",
              f"- embedded points in Qdrant: {n_vecs}",
              f"- precision bars: high >= {args.high_prec}, medium >= {args.med_prec}",
              "", "## Confidence breakdown (per item)", "",
              f"- items with >=1 HIGH suggestion (fast review): {n_high}",
              f"- items with only MEDIUM suggestions: {n_med}",
              f"- items with no high/medium suggestion (needs Mark / manual): {n_none}",
              f"- suggestion rows by band: {per_band}",
              "", "> Precision is a conservative LOWER BOUND: the genuine-tagged set is "
              "incomplete, so some correct detections are scored as false positives in calibration.",
              "", "## Per-motif calibration", "",
              "| motif | genuine | calib+ | centroid | high thr (prec) | med thr (prec) |",
              "|---|---:|---:|:--:|---|---|"]
    for m in motifs:
        c = calib[m]
        if c.get("uncalibrated"):
            lines.append(f"| {m} | {c['genuine']} | {c['calib_pos']} | "
                         f"{'y' if c['centroid'] else 'n'} | _uncalibrated_ | _uncalibrated_ |")
        else:
            ht = f"{c['high_thr']:.2f} ({c['high_prec']})" if c["high_thr"] is not None else "none"
            mt = f"{c['med_thr']:.2f} ({c['med_prec']})" if c["med_thr"] is not None else "none"
            lines.append(f"| {m} | {c['genuine']} | {c['calib_pos']} | "
                         f"{'y' if c['centroid'] else 'n'} | {ht} | {mt} |")
    (OUT_DIR / "motif_coverage_report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
