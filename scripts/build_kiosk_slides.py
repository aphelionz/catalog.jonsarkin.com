#!/usr/bin/env python3
"""Build the kiosk slide deck for the attract loop at asset/kiosk/.

Reads scripts/kiosk/slides.txt (one catalog number per line, optional `video`
flag, `#` comments), pulls metadata from the local DB through the dockerised
mariadb client, fetches similar pieces from the local SimilarPieces endpoint,
renders 1600px derivatives from the local originals, writes the /ja QR code,
and emits asset/kiosk/slides.json.

Usage (from the repo root, with `make local` up):
  python3 scripts/build_kiosk_slides.py [--list scripts/kiosk/slides.txt]
      [--base http://localhost:8888] [--skip-images]

Fails loudly (non-zero, names the offenders) when a listed item is unpublished,
has no primary image, or has no usable transcription.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "omeka/volume/themes/sarkin-jeppesen"
KIOSK = THEME / "asset/kiosk"
ORIGINALS = ROOT / "omeka/volume/files/original"

# Property IDs (docs/omeka-invariants.md)
P_IDENT, P_DATE, P_MEDIUM, P_TRANSCRIPTION = 10, 7, 26, 91
P_HEIGHT, P_SUPPORT, P_WIDTH = 603, 931, 1129

QR_TARGET = "https://catalog.jonsarkin.com/ja"
MAX_EDGE = 1600
TRANSCRIPTION_BUDGET = 320   # characters
TRANSCRIPTION_LINES = 8      # what the kiosk panel can show
SIMILAR_COUNT = 4


def db_query(sql: str) -> list[list[str]]:
    """Run a SELECT via the dockerised mariadb client; return rows of fields."""
    cmd = [
        "docker", "compose", "exec", "-T", "db",
        "mariadb", "-u", "root", "-proot", "omeka",
        "--batch", "--skip-column-names", "-e", sql,
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        sys.exit(f"DB query failed:\n{res.stderr}")
    return [[unescape(f) for f in line.split("\t")]
            for line in res.stdout.splitlines() if line != ""]


_ESC = {"n": "\n", "t": "\t", "0": "\0", "\\": "\\"}


def unescape(field: str) -> str:
    """Undo mariadb --batch escaping (\\n, \\t, \\0, \\\\)."""
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), m.group(0)), field)


def null(v: str) -> str:
    return "" if v == "NULL" else v


def load_list(path: Path) -> list[tuple[str, bool]]:
    entries: list[tuple[str, bool]] = []
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        entries.append((parts[0], "video" in parts[1:]))
    return entries


def fmt_num(n: float) -> str:
    s = f"{n:.1f}"
    return s[:-2] if s.endswith(".0") else s


def format_dims(height: str, width: str) -> str:
    """Port of view/common/dimensions.phtml (inches + cm, 1 decimal)."""
    def num(v: str) -> float | None:
        try:
            return float(v)
        except ValueError:
            return None
    h, w = num(height), num(width)
    if h is not None and w is not None:
        return (f"{fmt_num(h)} × {fmt_num(w)} in "
                f"({fmt_num(h * 2.54)} × {fmt_num(w * 2.54)} cm)")
    v = h if h is not None else w
    if v is not None:
        return f"{fmt_num(v)} in ({fmt_num(v * 2.54)} cm)"
    return ""


def split_transcription(text: str) -> list[list[str]]:
    """' // ' separates paragraphs, ' / ' separates lines (show.phtml rules).

    Keeps at most TRANSCRIPTION_LINES lines and roughly TRANSCRIPTION_BUDGET
    characters, cutting at a line boundary and marking the cut with an ellipsis.
    """
    text = text.strip().strip("/").strip()
    out: list[list[str]] = []
    used_chars = used_lines = 0
    cut = False
    for para in (p.strip() for p in text.split(" // ") if p.strip()):
        kept: list[str] = []
        for line in (l.strip() for l in para.split(" / ") if l.strip()):
            if used_lines >= TRANSCRIPTION_LINES or (
                    used_chars and used_chars + len(line) > TRANSCRIPTION_BUDGET):
                cut = True
                break
            kept.append(line)
            used_lines += 1
            used_chars += len(line) + 1
        if kept:
            out.append(kept)
        if cut:
            break
    if cut and out:
        out[-1][-1] += " …"
    return out


def fetch_items(catalog_numbers: list[str]) -> dict[str, dict]:
    quoted = ",".join("'" + c.replace("'", "''") + "'" for c in catalog_numbers)

    def one(prop: int) -> str:
        return (f"(SELECT v.value FROM value v WHERE v.resource_id=r.id "
                f"AND v.property_id={prop} AND v.value IS NOT NULL LIMIT 1)")

    sql = f"""
    SELECT r.id, r.is_public, cn.value,
      {one(P_DATE)},
      (SELECT GROUP_CONCAT(DISTINCT v.value ORDER BY v.value SEPARATOR ', ')
         FROM value v WHERE v.resource_id=r.id AND v.property_id={P_MEDIUM}
         AND v.value NOT LIKE '%/%'),
      {one(P_SUPPORT)}, {one(P_HEIGHT)}, {one(P_WIDTH)}, {one(P_TRANSCRIPTION)},
      m.storage_id, m.extension
    FROM resource r
    JOIN value cn ON cn.resource_id=r.id AND cn.property_id={P_IDENT}
    LEFT JOIN media m ON m.item_id=r.id AND m.position=1 AND m.has_thumbnails=1
    WHERE cn.value IN ({quoted})
    """
    items: dict[str, dict] = {}
    for row in db_query(sql):
        (rid, is_public, cat, year, medium, support, height, width,
         transcription, storage_id, ext) = (null(f) for f in row)
        items[cat] = {
            "id": int(rid), "public": is_public == "1", "catalog_no": cat,
            "year": year, "medium": medium, "support": support,
            "height": height, "width": width, "transcription": transcription,
            "storage_id": storage_id, "ext": ext,
        }
    return items


def validate(entries: list[tuple[str, bool]], items: dict[str, dict]) -> None:
    problems = []
    for cat, _ in entries:
        it = items.get(cat)
        if not it:
            problems.append(f"{cat}: not in DB")
            continue
        if not it["public"]:
            problems.append(f"{cat}: unpublished")
        if not it["storage_id"]:
            problems.append(f"{cat}: no primary image")
        t = it["transcription"].strip().lower()
        if t in ("", "∅", "no text"):
            problems.append(f"{cat}: no usable transcription")
        if not it["year"] or it["year"] == "∅":
            problems.append(f"{cat}: no year")
    if problems:
        sys.exit("Slide list has problems:\n  " + "\n  ".join(problems))


def public_ids(ids: list[int]) -> set[int]:
    if not ids:
        return set()
    rows = db_query("SELECT id FROM resource WHERE is_public=1 AND id IN ("
                    + ",".join(str(i) for i in ids) + ")")
    return {int(r[0]) for r in rows}


def fetch_similar(base: str, item_id: int) -> list[dict]:
    url = f"{base}/similar/{item_id}/json?site=catalog"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        print(f"  similar {item_id}: {exc}", file=sys.stderr)
        return []
    results = [r for r in payload.get("results", [])
               if not re.match(r"JS-0000-|.*-T\d", r.get("title", ""))]
    ok = public_ids([int(r["id"]) for r in results])
    out = []
    for r in results:
        if int(r["id"]) not in ok:
            continue
        m = re.search(r"/files/medium/([0-9a-f]+\.\w+)$", r.get("thumbnail", ""))
        if not m:
            continue
        out.append({
            "id": int(r["id"]), "catalog_no": r["title"],
            "thumb": f"/files/medium/{m.group(1)}", "url": f"/item/{r['id']}",
        })
        if len(out) == SIMILAR_COUNT:
            break
    return out


def make_derivative(it: dict, skip: bool) -> tuple[str, int, int]:
    from PIL import Image, ImageOps

    src = ORIGINALS / f"{it['storage_id']}.{it['ext']}"
    dst = KIOSK / "img" / f"{it['catalog_no']}.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and (skip or dst.stat().st_mtime >= src.stat().st_mtime):
        with Image.open(dst) as im:
            return f"img/{dst.name}", im.width, im.height
    if not src.exists():
        sys.exit(f"{it['catalog_no']}: original missing at {src}")
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        icc = im.info.get("icc_profile")
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
        im.save(dst, "JPEG", quality=85, progressive=True, optimize=True,
                icc_profile=icc)
        return f"img/{dst.name}", im.width, im.height


def make_qr() -> None:
    import segno

    qr = segno.make(QR_TARGET, error="m")
    # Black modules, transparent ground: the page sets a white box behind it.
    qr.save(str(KIOSK / "qr-ja.svg"), scale=8, dark="#000000", light=None,
            svgclass=None, lineclass=None)
    qr.save(str(KIOSK / "qr-ja.png"), scale=12, dark="#000000",
            light="#ffffff", border=4)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--list", default=ROOT / "scripts/kiosk/slides.txt", type=Path)
    ap.add_argument("--base", default="http://localhost:8888")
    ap.add_argument("--skip-images", action="store_true",
                    help="reuse existing derivatives without checking mtimes")
    ap.add_argument("--out", default=KIOSK / "slides.json", type=Path)
    args = ap.parse_args()

    entries = load_list(args.list)
    if not entries:
        sys.exit(f"{args.list}: no entries")
    items = fetch_items([c for c, _ in entries])
    validate(entries, items)

    slides = []
    for cat, video in entries:
        it = items[cat]
        image, w, h = make_derivative(it, args.skip_images)
        slides.append({
            "id": it["id"], "catalog_no": cat, "year": it["year"],
            "medium": it["medium"], "support": it["support"],
            "dims": format_dims(it["height"], it["width"]),
            "image": image, "w": w, "h": h, "url": f"/item/{it['id']}",
            "thumb": f"/files/medium/{it['storage_id']}.jpg",
            "video": video,
            "transcription": split_transcription(it["transcription"]),
            "similar": fetch_similar(args.base, it["id"]),
        })
        print(f"{cat}: {w}x{h}, {len(slides[-1]['similar'])} similar")

    make_qr()
    args.out.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "qr_url": QR_TARGET,
        "slides": slides,
    }, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {args.out} ({len(slides)} slides, "
          f"{sum(s['video'] for s in slides)} tagged video)")


if __name__ == "__main__":
    main()
