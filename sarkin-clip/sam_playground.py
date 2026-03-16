#!/usr/bin/env python3
"""Interactive SAM playground for parameter tuning.

Runs NATIVELY on macOS (not in Docker) to use MPS acceleration.
Two modes: automatic mask generation with full parameter control,
and prompted prediction with click-to-place points/boxes.

Usage:
    make sam-playground
"""

from __future__ import annotations

import io
import os
import sys
import time
from urllib.parse import urlparse

import cv2
import gradio as gr
import httpx
import numpy as np
from PIL import Image

# Ensure clip_api is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clip_api.sam import (
    MAX_DIM,
    PREDICTOR_AVAILABLE,
    SAM_PRESETS,
    predict_from_prompts,
    segment_image_custom,
)

OMEKA_BASE_URL = os.getenv("OMEKA_BASE_URL", "http://localhost:8888")


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def _rewrite_url(url: str) -> str:
    parsed = urlparse(url)
    base_parsed = urlparse(OMEKA_BASE_URL)
    return url.replace(
        f"{parsed.scheme}://{parsed.netloc}",
        f"{base_parsed.scheme}://{base_parsed.netloc}",
        1,
    )


def load_from_omeka(omeka_id: int) -> np.ndarray | None:
    """Fetch the large thumbnail for an Omeka item."""
    try:
        resp = httpx.get(f"{OMEKA_BASE_URL}/api/items/{int(omeka_id)}", timeout=10)
        resp.raise_for_status()
        media_list = resp.json().get("o:media", [])
        if not media_list:
            return None
        media_id = media_list[0].get("o:id")
        media_resp = httpx.get(f"{OMEKA_BASE_URL}/api/media/{media_id}", timeout=10)
        media_resp.raise_for_status()
        media = media_resp.json()
        url = media.get("o:thumbnail_urls", {}).get("large") or media.get("o:original_url")
        if not url:
            return None
        img_resp = httpx.get(_rewrite_url(url), timeout=60, follow_redirects=True)
        img_resp.raise_for_status()
        return np.array(Image.open(io.BytesIO(img_resp.content)).convert("RGB"))
    except Exception as exc:
        raise gr.Error(f"Failed to load Omeka item {omeka_id}: {exc}")


def prepare_image(image_array: np.ndarray) -> np.ndarray:
    """Resize to MAX_DIM if needed (matches production behavior)."""
    h, w = image_array.shape[:2]
    if max(w, h) > MAX_DIM:
        scale = MAX_DIM / max(w, h)
        image_array = cv2.resize(
            image_array, (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_LANCZOS4,
        )
    return image_array


# ---------------------------------------------------------------------------
# Tab 1: Automatic mask generation
# ---------------------------------------------------------------------------

def fill_preset(preset_name: str):
    """Return slider values for a preset."""
    if preset_name == "custom":
        return [gr.skip()] * 9
    p = SAM_PRESETS[preset_name]
    return [
        p["points_per_side"],
        p["pred_iou_thresh"],
        p["stability_score_thresh"],
        p["min_mask_region_area"],
        p["crop_n_layers"],
        p["crop_n_points_downscale_factor"],
        p["min_area_pct"],
        p["max_area_pct"],
        p["max_segments"],
    ]


def run_auto_masks(
    image: np.ndarray | None,
    points_per_side, pred_iou_thresh, stability_score_thresh,
    min_mask_region_area, crop_n_layers, crop_n_points_downscale_factor,
    min_area_pct, max_area_pct, max_segments,
):
    if image is None:
        raise gr.Error("Load an image first")

    image = prepare_image(image)

    gen_params = {
        "points_per_side": int(points_per_side),
        "pred_iou_thresh": float(pred_iou_thresh),
        "stability_score_thresh": float(stability_score_thresh),
        "min_mask_region_area": int(min_mask_region_area),
        "crop_n_layers": int(crop_n_layers),
        "crop_n_points_downscale_factor": int(crop_n_points_downscale_factor),
    }

    t0 = time.time()
    segments, raw_count = segment_image_custom(
        image, gen_params,
        min_area_pct=float(min_area_pct),
        max_area_pct=float(max_area_pct),
        max_segments=int(max_segments),
    )
    elapsed = time.time() - t0

    # Build annotations for AnnotatedImage
    annotations = []
    for i, seg in enumerate(segments):
        label = f"#{i} area={seg['area_pct']:.1%} stab={seg['stability_score']:.3f}"
        annotations.append((seg["mask"].astype(np.float32), label))

    # Stats
    areas = [s["area_pct"] for s in segments]
    if areas:
        stats = (
            f"**{len(segments)}** masks shown "
            f"(from {raw_count} raw) in {elapsed:.1f}s\n\n"
            f"Area: min={min(areas):.1%}  max={max(areas):.1%}  "
            f"mean={np.mean(areas):.1%}  median={np.median(areas):.1%}"
        )
    else:
        stats = f"No masks passed filtering ({raw_count} raw masks generated in {elapsed:.1f}s)"

    return (image, annotations), stats


# ---------------------------------------------------------------------------
# Tab 2: Prompted prediction
# ---------------------------------------------------------------------------

def _draw_prompts(image: np.ndarray, points: list, box: list | None) -> np.ndarray:
    """Draw point markers and box on a copy of the image."""
    img = image.copy()
    for x, y, label in points:
        color = (0, 200, 0) if label == 1 else (200, 0, 0)
        cv2.circle(img, (int(x), int(y)), 7, color, -1)
        cv2.circle(img, (int(x), int(y)), 7, (255, 255, 255), 2)
    if box and len(box) == 4:
        cv2.rectangle(img, (int(box[0]), int(box[1])),
                      (int(box[2]), int(box[3])), (0, 120, 255), 3)
    return img


def _run_prediction(image: np.ndarray, points: list, box: list | None,
                    multimask: bool):
    """Run prompted prediction and return annotated image + info."""
    if not points and not box:
        return (image, []), "Click on the image to add prompts"

    point_coords = None
    point_labels = None
    box_arr = None

    if points:
        point_coords = np.array([[p[0], p[1]] for p in points], dtype=np.float32)
        point_labels = np.array([p[2] for p in points], dtype=np.int32)
    if box and len(box) == 4:
        box_arr = np.array(box, dtype=np.float32)

    t0 = time.time()
    results = predict_from_prompts(
        image,
        point_coords=point_coords,
        point_labels=point_labels,
        box=box_arr,
        multimask_output=multimask,
    )
    elapsed = time.time() - t0

    annotated_base = _draw_prompts(image, points, box)
    annotations = []
    for i, r in enumerate(results):
        label = f"Mask {i} score={r['score']:.3f} area={r['area']}px"
        annotations.append((r["mask"].astype(np.float32), label))

    total = image.shape[0] * image.shape[1]
    info_lines = [f"**{len(results)}** masks in {elapsed:.1f}s"]
    for i, r in enumerate(results):
        pct = r["area"] / total
        info_lines.append(f"  Mask {i}: score={r['score']:.3f}  area={pct:.1%}")
    prompts_desc = []
    if points:
        pos = sum(1 for p in points if p[2] == 1)
        neg = len(points) - pos
        prompts_desc.append(f"{pos} pos + {neg} neg points")
    if box:
        prompts_desc.append("box")
    info_lines.append(f"\nPrompts: {', '.join(prompts_desc)}")

    return (annotated_base, annotations), "\n".join(info_lines)


def on_image_click(state, mode, multimask, evt: gr.SelectData):
    """Handle click on the prompted prediction image."""
    img = state.get("image")
    if img is None:
        raise gr.Error("Load an image first")

    points = state.get("points", [])
    box_start = state.get("box_start", None)

    x, y = evt.index[0], evt.index[1]

    if mode == "Box":
        if box_start is None:
            state["box_start"] = [x, y]
            state["box"] = None
            annotated = _draw_prompts(img, points, None)
            cv2.drawMarker(annotated, (int(x), int(y)), (0, 120, 255),
                           cv2.MARKER_CROSS, 15, 2)
            return annotated, (img, []), "Click again to complete the box", state
        else:
            x1, y1 = box_start
            box = [min(x1, x), min(y1, y), max(x1, x), max(y1, y)]
            state["box"] = box
            state["box_start"] = None
    else:
        label = 1 if mode == "Positive (+)" else 0
        points.append([x, y, label])
        state["points"] = points

    result_img, info = _run_prediction(img, state["points"], state.get("box"), multimask)
    click_img = _draw_prompts(img, state["points"], state.get("box"))
    return click_img, result_img, info, state


def clear_points(state, multimask):
    img = state.get("image")
    if img is None:
        return None, (None, []), "No image loaded", state
    state["points"] = []
    result_img, info = _run_prediction(img, [], state.get("box"), multimask)
    click_img = _draw_prompts(img, [], state.get("box"))
    return click_img, result_img, info, state


def clear_box(state, multimask):
    img = state.get("image")
    if img is None:
        return None, (None, []), "No image loaded", state
    state["box"] = None
    state["box_start"] = None
    result_img, info = _run_prediction(img, state.get("points", []), None, multimask)
    click_img = _draw_prompts(img, state.get("points", []), None)
    return click_img, result_img, info, state


def clear_all(state, multimask):
    img = state.get("image")
    if img is None:
        return None, (None, []), "No image loaded", state
    state["points"] = []
    state["box"] = None
    state["box_start"] = None
    return img.copy(), (img, []), "Prompts cleared", state


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def build_ui():
    with gr.Blocks(title="SAM Playground") as demo:
        gr.Markdown("# SAM Playground")

        # Shared image state
        image_state = gr.State(value=None)  # np.ndarray (H,W,3) after prepare

        # --- Image loading ---
        with gr.Row():
            with gr.Column(scale=3):
                upload = gr.Image(label="Upload image", type="numpy",
                                  height=150)
            with gr.Column(scale=1):
                omeka_id = gr.Number(label="Omeka item ID", precision=0)
                load_btn = gr.Button("Load from Omeka", size="sm")

        def on_upload(img):
            if img is None:
                return None
            return prepare_image(img)

        def on_omeka_load(oid):
            if not oid:
                raise gr.Error("Enter an Omeka item ID")
            img = load_from_omeka(int(oid))
            if img is None:
                raise gr.Error(f"No image found for item {int(oid)}")
            img = prepare_image(img)
            return img, img

        upload.input(on_upload, inputs=[upload], outputs=[image_state])
        load_btn.click(on_omeka_load, inputs=[omeka_id],
                       outputs=[image_state, upload])

        # --- Tabs ---
        with gr.Tabs():
            # ============================================================
            # TAB 1: Automatic masks
            # ============================================================
            with gr.TabItem("Automatic Masks"):
                with gr.Row():
                    with gr.Column(scale=3):
                        auto_output = gr.AnnotatedImage(
                            label="Segmentation result",
                            show_legend=True,
                            height=600,
                        )
                        auto_stats = gr.Markdown("Generate masks to see results")
                    with gr.Column(scale=1):
                        preset_radio = gr.Radio(
                            ["sparse", "medium", "dense", "custom"],
                            value="medium", label="Preset",
                        )

                        gr.Markdown("### Generator params")
                        s_points = gr.Slider(8, 64, value=24, step=4,
                                             label="points_per_side")
                        s_iou = gr.Slider(0.5, 1.0, value=0.86, step=0.01,
                                          label="pred_iou_thresh")
                        s_stab = gr.Slider(0.5, 1.0, value=0.90, step=0.01,
                                           label="stability_score_thresh")
                        s_area = gr.Slider(0, 5000, value=500, step=100,
                                           label="min_mask_region_area")
                        s_crop = gr.Slider(0, 3, value=1, step=1,
                                           label="crop_n_layers")
                        s_down = gr.Slider(1, 4, value=2, step=1,
                                           label="crop_n_points_downscale_factor")

                        gr.Markdown("### Post-filtering")
                        s_min_pct = gr.Slider(0.0, 0.05, value=0.005, step=0.001,
                                              label="min_area_pct")
                        s_max_pct = gr.Slider(0.05, 1.0, value=0.40, step=0.01,
                                              label="max_area_pct")
                        s_max_seg = gr.Slider(5, 200, value=40, step=5,
                                              label="max_segments")

                        generate_btn = gr.Button("Generate Masks",
                                                 variant="primary")

                all_sliders = [s_points, s_iou, s_stab, s_area,
                               s_crop, s_down, s_min_pct, s_max_pct, s_max_seg]

                preset_radio.change(fill_preset, inputs=[preset_radio],
                                    outputs=all_sliders)

                generate_btn.click(
                    run_auto_masks,
                    inputs=[image_state] + all_sliders,
                    outputs=[auto_output, auto_stats],
                )

            # ============================================================
            # TAB 2: Prompted prediction
            # ============================================================
            with gr.TabItem("Prompted Prediction"):
                if not PREDICTOR_AVAILABLE:
                    gr.Markdown(
                        "**Prompted prediction requires SAM2.** "
                        "Install `sam-2` (see requirements.local.txt)."
                    )
                else:
                    prompt_state = gr.State(
                        value={"points": [], "box": None, "box_start": None,
                               "image": None}
                    )

                    with gr.Row():
                        with gr.Column(scale=3):
                            # Clickable image for placing prompts
                            click_image = gr.Image(
                                label="Click to place prompts",
                                type="numpy",
                                height=350,
                                interactive=False,
                            )
                            # Mask output below
                            prompt_output = gr.AnnotatedImage(
                                label="Predicted masks",
                                show_legend=True,
                                height=350,
                            )
                            prompt_info = gr.Markdown(
                                "Load an image, then click to place points"
                            )
                        with gr.Column(scale=1):
                            mode_radio = gr.Radio(
                                ["Positive (+)", "Negative (-)", "Box"],
                                value="Positive (+)",
                                label="Click mode",
                            )
                            multimask_cb = gr.Checkbox(
                                value=True, label="Multi-mask output (3 candidates)",
                            )
                            with gr.Row():
                                clear_pts_btn = gr.Button("Clear points", size="sm")
                                clear_box_btn = gr.Button("Clear box", size="sm")
                            clear_all_btn = gr.Button("Clear all", size="sm")

                            gr.Markdown("""
**How to use:**
- **Positive (+):** click on the object you want
- **Negative (-):** click on areas to exclude
- **Box:** two clicks define the bounding box
- Green dots = positive, red = negative
- Orange rectangle = box prompt
""")

                    # When image loads, initialize prompt state
                    def init_prompt_state(img):
                        if img is None:
                            return None, (None, []), {"points": [], "box": None,
                                                      "box_start": None, "image": None}
                        return img.copy(), (img, []), {"points": [], "box": None,
                                                       "box_start": None, "image": img}

                    image_state.change(
                        init_prompt_state,
                        inputs=[image_state],
                        outputs=[click_image, prompt_output, prompt_state],
                    )

                    # Click handling on the gr.Image
                    click_image.select(
                        on_image_click,
                        inputs=[prompt_state, mode_radio, multimask_cb],
                        outputs=[click_image, prompt_output, prompt_info,
                                 prompt_state],
                    )

                    clear_pts_btn.click(
                        clear_points,
                        inputs=[prompt_state, multimask_cb],
                        outputs=[click_image, prompt_output, prompt_info,
                                 prompt_state],
                    )
                    clear_box_btn.click(
                        clear_box,
                        inputs=[prompt_state, multimask_cb],
                        outputs=[click_image, prompt_output, prompt_info,
                                 prompt_state],
                    )
                    clear_all_btn.click(
                        clear_all,
                        inputs=[prompt_state, multimask_cb],
                        outputs=[click_image, prompt_output, prompt_info,
                                 prompt_state],
                    )

    return demo


if __name__ == "__main__":
    demo = build_ui()
    demo.launch(server_name="127.0.0.1", server_port=7860,
                theme=gr.themes.Soft())
