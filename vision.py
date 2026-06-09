from __future__ import annotations

"""Local OCR helpers for sticker vision.

The sticker suite treats OCR as an optional local enhancement. Import OCR
engines lazily so the plugin can still load when the dependency is not
installed; command output then explains what is missing instead of crashing the
whole plugin.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class VisionResult:
    ocr_text: str = ""
    engine: str = ""
    error: str = ""


_RAPID_OCR: Any = None
_RAPID_OCR_IMPORT_ERROR = ""


def _rapid_ocr_engine() -> Any:
    global _RAPID_OCR, _RAPID_OCR_IMPORT_ERROR
    if _RAPID_OCR is not None:
        return _RAPID_OCR
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:  # pragma: no cover - depends on optional package
        _RAPID_OCR_IMPORT_ERROR = str(exc)
        return None
    _RAPID_OCR = RapidOCR()
    return _RAPID_OCR


def _text_from_rapidocr_item(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("text", "rec_text", "label"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if isinstance(item, (list, tuple)):
        for value in item:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _normalize_ocr_lines(result: Any) -> list[str]:
    if result is None:
        return []
    # rapidocr_onnxruntime commonly returns (ocr_result, elapsed).
    if isinstance(result, tuple) and result:
        result = result[0]
    lines: list[str] = []
    if isinstance(result, dict):
        result = result.get("result") or result.get("data") or []
    if not isinstance(result, (list, tuple)):
        return lines
    for item in result:
        text = _text_from_rapidocr_item(item)
        if text and text not in lines:
            lines.append(text)
    return lines


def run_ocr(local_path: str, timeout_seconds: int = 5) -> VisionResult:
    """Run local OCR on a sticker image.

    `timeout_seconds` is kept in the signature for the caller contract and for
    future engines. RapidOCR itself is synchronous, so this first implementation
    relies on the engine call returning normally.
    """
    path = Path(local_path)
    if not path.exists() or not path.is_file():
        return VisionResult(engine="rapidocr", error="image file not found")

    engine = _rapid_ocr_engine()
    if engine is None:
        detail = _RAPID_OCR_IMPORT_ERROR or "rapidocr_onnxruntime is not installed"
        return VisionResult(engine="rapidocr", error=f"OCR dependency missing: {detail}")

    try:
        raw_result = engine(str(path))
    except Exception as exc:  # pragma: no cover - depends on OCR runtime
        return VisionResult(engine="rapidocr", error=f"OCR failed: {exc}")

    lines = _normalize_ocr_lines(raw_result)
    text = "\n".join(lines).strip()
    return VisionResult(ocr_text=text, engine="rapidocr")
