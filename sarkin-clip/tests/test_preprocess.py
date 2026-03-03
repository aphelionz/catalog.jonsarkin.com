from __future__ import annotations

from clip_api.preprocess import sanitize_ocr


def test_sanitize_ocr_removes_garbage() -> None:
    text = "Hello World\nbbbbbbbbbbbb\nnooooooo!!!\nClean Line"
    cleaned = sanitize_ocr(text, max_chars=500)
    assert "bbbbbbbb" not in cleaned
    assert "nooooooo" not in cleaned
    assert "Hello World" in cleaned
    assert "Clean Line" in cleaned


def test_sanitize_ocr_caps_length() -> None:
    text = ("abcdefghijklmnopqrstuvwxyz" * 200).strip()
    cleaned = sanitize_ocr(text, max_chars=100)
    assert len(cleaned) <= 100
