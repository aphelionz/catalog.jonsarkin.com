from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Set

PREPROC_VERSION = 1
OCR_MAX_CHARS = 1500

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_MIN_RATIO_LEN = 8
_MIN_UNIQUE_RATIO = 0.15
_REPEAT_RUN_LEN = 6
_RATIO_SAMPLE_LEN = 80


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if item is not None)
    return str(value)


def _has_repeat_run(text: str, *, run_len: int = _REPEAT_RUN_LEN) -> bool:
    last = None
    run = 0
    for ch in text:
        if ch == last:
            run += 1
        else:
            last = ch
            run = 1
        if run >= run_len:
            return True
    return False


def _unique_ratio(text: str) -> float:
    if not text:
        return 0.0
    sample = text[:_RATIO_SAMPLE_LEN]
    return len(set(sample)) / len(sample)


def _is_garbage_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    if not compact:
        return True
    if _has_repeat_run(compact):
        return True
    if len(compact) >= _MIN_RATIO_LEN and _unique_ratio(compact) < _MIN_UNIQUE_RATIO:
        return True
    return False


def sanitize_ocr(text: str, *, max_chars: int = OCR_MAX_CHARS) -> str:
    if not text:
        return ""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _is_garbage_line(line):
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    if max_chars > 0 and len(cleaned) > max_chars:
        return cleaned[:max_chars].rstrip()
    return cleaned


def tokenize(text: str) -> Set[str]:
    if not text:
        return set()
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(text)}


def _iter_values(values: Iterable[Any]) -> Iterable[str]:
    for value in values:
        text = _coerce_text(value).strip()
        if text:
            yield text


def _payload_value(payload: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return _coerce_text(payload[key])
    return ""


def compose_text_blob(payload: Dict[str, Any], *, ocr_char_limit: int = OCR_MAX_CHARS) -> str:
    description = _payload_value(payload, ["omeka_description", "description"])
    curator_notes = _payload_value(payload, ["curator_notes", "curator_note"])
    tags = _payload_value(payload, ["subjects", "tags", "subject", "tag"])
    ocr_raw = _payload_value(payload, ["ocr_text_raw", "ocr_text"])
    ocr_text = sanitize_ocr(ocr_raw, max_chars=ocr_char_limit)

    parts = list(_iter_values([description, curator_notes, tags, ocr_text]))
    if not parts:
        fallback = _payload_value(payload, ["text_blob"])
        if fallback:
            parts.append(fallback)
    return "\n".join(parts)


def extract_tags_text(payload: Dict[str, Any]) -> str:
    return _payload_value(payload, ["subjects", "tags", "subject", "tag"])
