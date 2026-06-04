from __future__ import annotations

"""Image/message extraction helpers shared by memory and probe.

AstrBot/NapCat may expose QQ stickers as message components, raw message
segments, or nested objects. These helpers keep the permissive traversal in one
module while still requiring a strong identity field before the memory plugin
stores a sticker.
"""

import json
from typing import Any

from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At

from .constants import IMAGE_FIELD_NAMES, IMAGE_IDENTITY_FIELD_NAMES


def looks_like_image(component: Any) -> bool:
    class_name = component.__class__.__name__.lower()
    if "image" in class_name or "picture" in class_name:
        return True
    fields = vars(component) if hasattr(component, "__dict__") else {}
    return any(name in fields for name in IMAGE_FIELD_NAMES)


def safe_component_fields(component: Any) -> dict[str, Any]:
    fields = vars(component) if hasattr(component, "__dict__") else {}
    result: dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = value
        elif isinstance(value, (list, tuple, dict)):
            try:
                json.dumps(value, ensure_ascii=False)
                result[str(key)] = value
            except TypeError:
                result[str(key)] = str(value)
        else:
            result[str(key)] = str(value)
    return result


def iter_raw_segments(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 8 or value is None:
        return []
    if isinstance(value, dict):
        segments: list[dict[str, Any]] = []
        value_type = str(value.get("type") or value.get("msg_type") or "").lower()
        data = value.get("data") if isinstance(value.get("data"), dict) else value
        if value_type in {"image", "mface"}:
            segments.append(value)
        elif any(key in value for key in IMAGE_IDENTITY_FIELD_NAMES) or any(key in data for key in IMAGE_IDENTITY_FIELD_NAMES):
            segments.append(value)
        pic_element = value.get("picElement")
        if isinstance(pic_element, dict):
            segments.append(pic_element)
        for item in value.values():
            segments.extend(iter_raw_segments(item, depth + 1))
        return segments
    if isinstance(value, list):
        segments: list[dict[str, Any]] = []
        for item in value:
            segments.extend(iter_raw_segments(item, depth + 1))
        return segments
    if hasattr(value, "__dict__"):
        return iter_raw_segments(vars(value), depth + 1)
    return []


def image_from_raw_segment(segment: dict[str, Any]) -> dict[str, Any]:
    data = segment.get("data")
    fields = data if isinstance(data, dict) else segment
    return {
        "class": str(segment.get("type") or segment.get("msg_type") or "raw_image"),
        "module": "raw_message",
        "fields": {str(key): value for key, value in fields.items() if isinstance(value, (str, int, float, bool)) or value is None},
    }


def pick_field(fields: dict[str, Any], names: list[str]) -> str:
    for name in names:
        value = fields.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def source_from_image(image: dict[str, Any]) -> dict[str, str]:
    fields = image.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    return {
        "url": pick_field(fields, ["url", "URL"]),
        "file": pick_field(fields, ["file", "fileName", "file_name"]),
        "file_id": pick_field(fields, ["file_id", "fileUuid", "file_unique", "image_id"]),
        "path": pick_field(fields, ["path", "sourcePath", "local_path"]),
        "md5": pick_field(fields, ["md5", "md5HexStr", "hash"]),
        "summary": pick_field(fields, ["summary"]),
        "class": str(image.get("class") or ""),
    }


def source_has_identity(source: dict[str, str]) -> bool:
    return any(source.get(name, "") for name in ["md5", "file_id", "file", "path", "url"])


def dedupe_images(images: list[dict[str, Any]], key_func) -> list[dict[str, Any]]:
    sources = [(image, source_from_image(image)) for image in images]
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for image, source in sources:
        if not source_has_identity(source):
            continue
        key = key_func(source)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(image)
    return deduped


def extract_images(event: AstrMessageEvent, key_func) -> tuple[list[dict[str, Any]], int]:
    """Return deduplicated image records and the raw image-like record count."""
    images: list[dict[str, Any]] = []
    for component in event.get_messages():
        if isinstance(component, At):
            continue
        if not looks_like_image(component):
            continue
        images.append(
            {
                "class": component.__class__.__name__,
                "module": component.__class__.__module__,
                "fields": safe_component_fields(component),
            }
        )

    raw_message = getattr(event.message_obj, "raw_message", None)
    for segment in iter_raw_segments(raw_message):
        images.append(image_from_raw_segment(segment))
    for segment in iter_raw_segments(event.message_obj):
        images.append(image_from_raw_segment(segment))
    return dedupe_images(images, key_func), len(images)
