from __future__ import annotations

"""Diagnostic probe helpers for sticker payloads.

The probe is intentionally diagnostic only: it snapshots AstrBot/NapCat message
shapes so we can update the real learning extractor without guessing field
names. It does not decide whether a sticker should be stored or sent.
"""

import json
from collections import deque
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At

from .constants import IMAGE_FIELD_NAMES
from .image_extract import looks_like_image


class StickerProbe:
    def __init__(self, max_events: int = 5):
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=max_events)

    def safe_value(self, value: Any, depth: int = 0) -> Any:
        if depth > 2:
            return str(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [self.safe_value(item, depth + 1) for item in value[:8]]
        if isinstance(value, dict):
            return {str(key): self.safe_value(val, depth + 1) for key, val in list(value.items())[:20]}
        if hasattr(value, "__dict__"):
            return self.safe_value(vars(value), depth + 1)
        return str(value)

    def compact_image_fields(self, value: Any, depth: int = 0) -> list[dict[str, Any]]:
        if depth > 8 or value is None:
            return []
        if isinstance(value, dict):
            found: list[dict[str, Any]] = []
            value_type = str(value.get("type") or value.get("msg_type") or "").lower()
            data = value.get("data") if isinstance(value.get("data"), dict) else value
            if value_type in {"image", "mface"} or any(key in data for key in IMAGE_FIELD_NAMES) or "picElement" in value:
                compact = self.build_compact_image(data)
                if compact:
                    found.append(compact)
            pic_element = value.get("picElement")
            if isinstance(pic_element, dict):
                compact = self.build_compact_image(pic_element)
                if compact:
                    found.append(compact)
            for item in value.values():
                found.extend(self.compact_image_fields(item, depth + 1))
            return found
        if isinstance(value, (list, tuple)):
            found: list[dict[str, Any]] = []
            for item in value:
                found.extend(self.compact_image_fields(item, depth + 1))
            return found
        if hasattr(value, "__dict__"):
            return self.compact_image_fields(vars(value), depth + 1)
        return []

    def build_compact_image(self, fields: dict[str, Any]) -> dict[str, Any]:
        keys = ["type", "summary", "file", "fileName", "file_id", "fileUuid", "url", "path", "sourcePath", "md5", "md5HexStr", "file_size", "fileSize", "picWidth", "picHeight"]
        return {key: fields.get(key) for key in keys if fields.get(key) not in (None, "", {})}

    def component_snapshot(self, component: Any) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "class": component.__class__.__name__,
            "module": component.__class__.__module__,
        }
        raw = self.safe_value(component)
        if isinstance(raw, dict):
            snapshot["fields"] = raw
        else:
            snapshot["value"] = raw
        return snapshot

    def event_snapshot(self, event: AstrMessageEvent) -> dict[str, Any]:
        message_obj = getattr(event, "message_obj", None)
        raw_message = getattr(message_obj, "raw_message", None)
        snapshot = {
            "message_str": event.get_message_str(),
            "message_outline": event.get_message_outline(),
            "raw_image_like": self.compact_image_fields(raw_message),
            "message_obj_image_like": self.compact_image_fields(message_obj),
            "components": [snapshot for snapshot in [self.component_snapshot(component) for component in event.get_messages()] if snapshot.get("class") != "Plain"],
        }
        if hasattr(message_obj, "__dict__"):
            snapshot["message_obj_keys"] = sorted(list(vars(message_obj).keys()))
        snapshot["raw_message_type"] = type(raw_message).__name__ if raw_message is not None else "None"
        return snapshot

    def extract_image_snapshots(self, event: AstrMessageEvent) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for component in event.get_messages():
            if isinstance(component, At):
                continue
            if looks_like_image(component):
                snapshots.append(self.component_snapshot(component))
        return snapshots

    def capture(self, event: AstrMessageEvent) -> dict[str, Any] | None:
        if not event.get_group_id():
            return None
        message_text = event.get_message_str().strip()
        message_outline = event.get_message_outline().strip()
        if message_text.startswith("/") or message_outline.startswith("/") or message_text.startswith("表情"):
            return None

        snapshots = self.extract_image_snapshots(event)
        event_snapshot = self.event_snapshot(event)
        message_outline = str(event_snapshot.get("message_outline") or "")
        message_str = str(event_snapshot.get("message_str") or "")
        payload = {
            "group_id": str(event.get_group_id()),
            "sender_id": str(event.get_sender_id()),
            "message_id": str(getattr(event.message_obj, "message_id", "") or ""),
            "looks_like_image": bool(snapshots) or "[图片]" in message_outline or "[图片]" in message_str,
            "images": snapshots,
            "event": event_snapshot,
        }
        self.recent_events.append(payload)
        try:
            logger.info("[sticker_probe] " + json.dumps(payload, ensure_ascii=False, default=str))
        except Exception as exc:
            logger.warning(f"[sticker_probe] log payload failed: {exc}")
        return payload

    def status_text(self) -> str:
        captured = len(self.recent_events)
        looks_like_image = sum(1 for event in self.recent_events if event.get("looks_like_image"))
        latest_outline = ""
        if self.recent_events:
            latest_outline = str(self.recent_events[-1].get("event", {}).get("message_outline") or "")
        return (
            "表情探针（sticker_suite 内置）\n"
            f"缓存事件数：{captured}（含图片/表情的：{looks_like_image}）\n"
            f"最近一条 outline：{latest_outline or '无'}\n"
            "日志前缀：[sticker_probe]；查看详情：表情探针详情"
        )

    def detail_text(self) -> str:
        if not self.recent_events:
            return "还没有捕获到图片/表情事件。请先在群里发送一张图片或表情包。"

        latest = self.recent_events[-1]
        event_snapshot = latest.get("event") or {}
        components = event_snapshot.get("components") or []
        lines = [
            f"最近群聊事件：群{latest.get('group_id')}，发送者{latest.get('sender_id')}",
            f"looks_like_image: {latest.get('looks_like_image')}",
            f"message_id: {latest.get('message_id')}",
            f"message_str: {event_snapshot.get('message_str')}",
            f"message_outline: {event_snapshot.get('message_outline')}",
            f"raw_message_type: {event_snapshot.get('raw_message_type')}",
            f"message_obj_keys: {event_snapshot.get('message_obj_keys')}",
            f"组件数: {len(components)}",
        ]
        for index, component in enumerate(components[:5], 1):
            fields = component.get("fields") if isinstance(component, dict) else None
            field_names = ", ".join(fields.keys()) if isinstance(fields, dict) else "无"
            lines.append(f"组件{index}: {component.get('class')} fields=[{field_names}]")
        raw_image_like = event_snapshot.get("raw_image_like") or []
        message_obj_image_like = event_snapshot.get("message_obj_image_like") or []
        lines.append(f"raw_image_like: {json.dumps(raw_image_like[:5], ensure_ascii=False, default=str)}")
        lines.append(f"message_obj_image_like: {json.dumps(message_obj_image_like[:5], ensure_ascii=False, default=str)}")
        return "\n".join(lines)
