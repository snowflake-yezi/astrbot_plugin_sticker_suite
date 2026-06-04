from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image
from astrbot.api.star import Context, Star

from .constants import (
    DEFAULT_COOLDOWN_SECONDS,
    IMAGE_FIELD_NAMES,
    IMAGE_IDENTITY_FIELD_NAMES,
    MAX_CONTEXTS,
    MAX_DOWNLOAD_BYTES,
    MOOD_KEYWORDS,
    TAG_LABELS,
    TRIGGER_PROBABILITY_DENOMINATOR,
)
from .image_extract import (
    extract_images,
    image_from_raw_segment,
    iter_raw_segments,
    looks_like_image,
    safe_component_fields,
    source_from_image,
    source_has_identity,
)
from .probe import StickerProbe


def _optional_filter_decorator(name: str):
    """尝试从 filter 取一个可选钩子装饰器。

    AstrBot 不同版本里 filter 暴露的钩子名可能不一样（例如
    on_decorating_result 在新版才有）。这里在缺失时退化成 no-op，让插件
    仍可加载；同时记录一条日志，避免用户调试时以为方法没被注册却找不到原因。
    """
    decorator_factory = getattr(filter, name, None)
    if callable(decorator_factory):
        try:
            return decorator_factory()
        except TypeError:
            return decorator_factory

    logger.warning(f"[sticker_suite] filter.{name} not available; decorated method will not be registered.")

    def passthrough(func):
        return func

    return passthrough


class StickerSuitePlugin(Star):
    """sticker_suite：按群学习/检索/复用 QQ 表情包，内置消息结构探针。

    本类只做命令编排和 AstrBot 装饰器注册；纯逻辑（图片识别、常量、探针）
    放在 constants/image_extract/probe 模块里。
    """

    DEFAULT_COOLDOWN_SECONDS = DEFAULT_COOLDOWN_SECONDS
    MAX_CONTEXTS = MAX_CONTEXTS
    MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_BYTES
    TRIGGER_PROBABILITY_DENOMINATOR = TRIGGER_PROBABILITY_DENOMINATOR
    IMAGE_FIELD_NAMES = IMAGE_FIELD_NAMES
    IMAGE_IDENTITY_FIELD_NAMES = IMAGE_IDENTITY_FIELD_NAMES
    MOOD_KEYWORDS = MOOD_KEYWORDS
    TAG_LABELS = TAG_LABELS

    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.data_dir = Path(__file__).with_name("data")
        self.images_dir = self.data_dir / "images"
        self.data_path = self.data_dir / "stickers.json"
        self.probe = StickerProbe()

    def _now(self) -> int:
        return int(time.time())

    def _load_data(self) -> dict[str, Any]:
        if not self.data_path.exists():
            return {"groups": {}, "shared": {"stickers": {}}}
        try:
            data = json.loads(self.data_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[sticker_suite] load data failed: {exc}")
            return {"groups": {}, "shared": {"stickers": {}}}
        if not isinstance(data, dict):
            return {"groups": {}, "shared": {"stickers": {}}}
        if not isinstance(data.get("groups"), dict):
            data["groups"] = {}
        if not isinstance(data.get("shared"), dict):
            data["shared"] = {"stickers": {}}
        if not isinstance(data["shared"].get("stickers"), dict):
            data["shared"]["stickers"] = {}
        if not isinstance(data["shared"].get("triggers"), dict):
            data["shared"]["triggers"] = {}
        return data

    def _save_data(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_group_key(self, event: AstrMessageEvent) -> str | None:
        group_id = event.get_group_id()
        if not group_id:
            return None
        return str(group_id)

    def _get_group(self, data: dict[str, Any], group_key: str) -> dict[str, Any]:
        groups = data.setdefault("groups", {})
        group = groups.setdefault(group_key, {})
        group.setdefault("enabled", False)
        group.setdefault("cooldown_seconds", self.DEFAULT_COOLDOWN_SECONDS)
        group.setdefault("last_sent_at", 0)
        group.setdefault("mood", "neutral")
        group.setdefault("allow_shared", False)
        group.setdefault("follow_enabled", False)
        group.setdefault("follow_cooldown_seconds", 120)
        group.setdefault("last_follow_sent_at", 0)
        group.setdefault("follow_test_mode_until", 0)
        group.setdefault("auto_tag_enabled", True)
        group.setdefault("auto_tag_mode", "strict")
        group.setdefault("recent_texts", [])
        group.setdefault("triggers", {})
        group.setdefault("stickers", {})
        return group

    def _get_shared(self, data: dict[str, Any]) -> dict[str, Any]:
        shared = data.setdefault("shared", {})
        shared.setdefault("stickers", {})
        shared.setdefault("triggers", {})
        return shared

    def _extract_message_text(self, event: AstrMessageEvent) -> str:
        parts: list[str] = []
        for component in event.get_messages():
            if isinstance(component, At):
                continue
            text = getattr(component, "text", None)
            if text is None:
                text = getattr(component, "plain", None)
            if text is None:
                continue
            value = str(text).strip()
            if value:
                parts.append(value)
        if parts:
            return "\n".join(parts).strip()
        return event.get_message_str().strip()

    def _looks_like_image(self, component: Any) -> bool:
        return looks_like_image(component)

    def _safe_component_fields(self, component: Any) -> dict[str, Any]:
        return safe_component_fields(component)

    def _iter_raw_segments(self, value: Any, depth: int = 0) -> list[dict[str, Any]]:
        return iter_raw_segments(value, depth)

    def _image_from_raw_segment(self, segment: dict[str, Any]) -> dict[str, Any]:
        return image_from_raw_segment(segment)

    def _extract_images(self, event: AstrMessageEvent) -> list[dict[str, Any]]:
        deduped, total = extract_images(event, self._sticker_key)
        if total and not deduped:
            logger.info(f"[sticker_suite] ignored weak image-like records: total={total} outline={event.get_message_outline()}")
        return deduped

    def _source_has_identity(self, source: dict[str, str]) -> bool:
        return source_has_identity(source)

    def _is_self_event(self, event: AstrMessageEvent) -> bool:
        self_id = str(event.get_self_id() or "")
        return bool(self_id) and str(event.get_sender_id()) == self_id

    def _dedupe_images(self, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sources = [(image, self._source_from_image(image)) for image in images]
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for image, source in sources:
            if not self._source_has_identity(source):
                continue
            key = self._sticker_key(source)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(image)
        return deduped

    def _pick_field(self, fields: dict[str, Any], names: list[str]) -> str:
        for name in names:
            value = fields.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _source_from_image(self, image: dict[str, Any]) -> dict[str, str]:
        return source_from_image(image)

    def _sticker_key_candidates(self, source: dict[str, str]) -> list[str]:
        candidates: list[str] = []
        for name in ["md5", "file_id", "file", "path", "url"]:
            value = source.get(name, "")
            if value:
                candidates.append(hashlib.sha256(f"{name}:{value}".encode("utf-8")).hexdigest())
        if not candidates:
            candidates.append(hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest())
        return candidates

    def _sticker_key(self, source: dict[str, str]) -> str:
        return self._sticker_key_candidates(source)[0]

    def _find_existing_sticker_key(self, stickers: dict[str, Any], source: dict[str, str]) -> str | None:
        for candidate in self._sticker_key_candidates(source):
            if candidate in stickers:
                return candidate
        for key, sticker in stickers.items():
            if not isinstance(sticker, dict):
                continue
            old_source = sticker.get("source") or {}
            if not isinstance(old_source, dict):
                continue
            for name in ["md5", "file_id", "file", "path", "url"]:
                value = source.get(name, "")
                if value and str(old_source.get(name) or "") == value:
                    return str(key)
        return None

    def _infer_mood(self, text: str) -> str | None:
        compact = re.sub(r"\s+", "", text)
        if not compact:
            return None
        for mood, keywords in self.MOOD_KEYWORDS.items():
            if any(keyword in compact for keyword in keywords):
                return mood
        return None

    def _tags_from_text(self, text: str) -> list[str]:
        mood = self._infer_mood(text)
        if mood is None:
            return []
        return [self.TAG_LABELS[mood]]

    def _infer_tags_for_sticker(self, group: dict[str, Any], shared: dict[str, Any], text: str) -> list[str]:
        compact = re.sub(r"\s+", "", text)
        if not compact or not group.get("auto_tag_enabled", True):
            return []
        shared_for_tags = shared if group.get("allow_shared", False) else None
        scored: dict[str, tuple[int, str]] = {}
        for tag in self._all_tags(group, shared_for_tags):
            if tag and tag in compact:
                scored[tag] = max(scored.get(tag, (0, "")), (10, "标签"), key=lambda item: item[0])
        for tag, words in self._trigger_map(group, shared_for_tags).items():
            for word in words:
                if word and word in compact:
                    scored[tag] = max(scored.get(tag, (0, "")), (8, "同义词"), key=lambda item: item[0])
        mood = self._infer_mood(compact)
        if mood is not None:
            tag = self.TAG_LABELS[mood]
            scored[tag] = max(scored.get(tag, (0, "")), (5, "情绪词"), key=lambda item: item[0])
        normalized = self._normalize_tag(compact)
        if normalized is not None and len(compact) <= 12:
            scored[normalized] = max(scored.get(normalized, (0, "")), (4, "短文本"), key=lambda item: item[0])
        return [tag for tag, _ in sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))[:3]]

    def _append_unique(self, values: list[Any], value: Any, limit: int | None = None) -> list[Any]:
        if value not in values:
            values.append(value)
        if limit is not None and len(values) > limit:
            return values[-limit:]
        return values

    def _source_texts_for_auto_tag(self, source: dict[str, str]) -> list[str]:
        ignored = {"", "表情", "图片", "[图片]", "[动画表情]", "动画表情", "image", "mface", "raw_image"}
        texts: list[str] = []
        for name in ["summary", "file", "path"]:
            value = str(source.get(name) or "").strip()
            if name == "path" and value:
                value = Path(value).stem
            elif name == "file" and value:
                value = Path(value).stem
            value = value.strip()
            if not value or value.lower() in ignored:
                continue
            if re.fullmatch(r"[A-Fa-f0-9]{16,}", value):
                continue
            if value not in texts:
                texts.append(value)
        return texts

    def _remember_recent_text(self, group: dict[str, Any], text: str, sender_id: str) -> None:
        normalized = text.strip()
        if not normalized or normalized.startswith("/") or normalized.startswith("表情"):
            return
        now = self._now()
        recent = [item for item in list(group.get("recent_texts", [])) if now - int(item.get("created_at", 0) or 0) <= 120]
        recent.append({"sender_id": str(sender_id), "text": normalized, "created_at": now})
        group["recent_texts"] = recent[-10:]

    def _previous_text_for_auto_tag(self, group: dict[str, Any], sender_id: str) -> str:
        if group.get("auto_tag_mode", "strict") == "off":
            return ""
        now = self._now()
        for item in reversed(list(group.get("recent_texts", []))):
            if str(item.get("sender_id") or "") != str(sender_id):
                continue
            if now - int(item.get("created_at", 0) or 0) > 60:
                continue
            text = str(item.get("text") or "").strip()
            if text:
                return text
        return ""

    def _infer_tags_from_texts_for_sticker(self, group: dict[str, Any], shared: dict[str, Any], texts: list[str]) -> list[str]:
        tags: list[str] = []
        for text in texts:
            for tag in self._infer_tags_for_sticker(group, shared, text):
                tags = self._append_unique(tags, tag, 3)
        return tags

    def _record_stickers(self, group: dict[str, Any], shared: dict[str, Any], images: list[dict[str, Any]], text: str, sender_id: str, group_key: str) -> bool:
        if not images:
            return False
        stickers = group.setdefault("stickers", {})
        shared_stickers = shared.setdefault("stickers", {})
        now = self._now()
        changed = False
        previous_text = self._previous_text_for_auto_tag(group, sender_id)
        for image in images:
            source = self._source_from_image(image)
            tag_texts = [item for item in [text, previous_text, *self._source_texts_for_auto_tag(source)] if item]
            tags = self._infer_tags_from_texts_for_sticker(group, shared, tag_texts)
            key = self._find_existing_sticker_key(stickers, source) or self._find_existing_sticker_key(shared_stickers, source) or self._sticker_key(source)
            sticker = stickers.setdefault(
                key,
                {
                    "source": source,
                    "local_path": "",
                    "send_count": 0,
                    "seen_count": 0,
                    "sender_ids": [],
                    "tags": [],
                    "contexts": [],
                    "created_at": now,
                    "last_seen_at": now,
                },
            )
            sticker["source"] = {**sticker.get("source", {}), **{k: v for k, v in source.items() if v}}
            sticker["seen_count"] = int(sticker.get("seen_count", 0)) + 1
            sticker["last_seen_at"] = now
            sticker["sender_ids"] = self._append_unique(list(sticker.get("sender_ids", [])), sender_id)
            for tag in tags:
                sticker["tags"] = self._append_unique(list(sticker.get("tags", [])), tag)
            for context_text in tag_texts:
                sticker["contexts"] = self._append_unique(list(sticker.get("contexts", [])), context_text, self.MAX_CONTEXTS)
            self._cache_sticker_file(key, sticker)
            self._ensure_sticker_metadata(key, sticker)

            shared_sticker = shared_stickers.setdefault(
                key,
                {
                    "source": source,
                    "local_path": "",
                    "send_count": 0,
                    "seen_count": 0,
                    "group_ids": [],
                    "tags": [],
                    "contexts": [],
                    "created_at": now,
                    "last_seen_at": now,
                },
            )
            shared_sticker["source"] = {**shared_sticker.get("source", {}), **{k: v for k, v in source.items() if v}}
            shared_sticker["local_path"] = sticker.get("local_path", shared_sticker.get("local_path", ""))
            shared_sticker["seen_count"] = int(shared_sticker.get("seen_count", 0)) + 1
            shared_sticker["last_seen_at"] = now
            shared_sticker["group_ids"] = self._append_unique(list(shared_sticker.get("group_ids", [])), group_key)
            for tag in tags:
                shared_sticker["tags"] = self._append_unique(list(shared_sticker.get("tags", [])), tag)
            for context_text in tag_texts:
                shared_sticker["contexts"] = self._append_unique(list(shared_sticker.get("contexts", [])), context_text, self.MAX_CONTEXTS)
            if not shared_sticker.get("local_path"):
                self._cache_sticker_file(key, shared_sticker)
            self._ensure_sticker_metadata(key, shared_sticker)
            changed = True
        return changed

    def _cache_sticker_file(self, key: str, sticker: dict[str, Any]) -> None:
        if sticker.get("local_path"):
            return
        source = sticker.get("source") or {}
        path_value = str(source.get("path") or "")
        if path_value and Path(path_value).exists():
            sticker["local_path"] = path_value
            content_hash = self._file_sha256(path_value)
            if content_hash:
                sticker["content_hash"] = content_hash
                sticker["id"] = content_hash[:8].upper()
            return
        url = str(source.get("url") or "")
        if not url.startswith(("http://", "https://")):
            return
        try:
            request = Request(url, headers={"User-Agent": "AstrBotStickerMemory/1.0"})
            with urlopen(request, timeout=8) as response:
                content = response.read(self.MAX_DOWNLOAD_BYTES + 1)
                content_type = response.headers.get("Content-Type", "")
            if not content or len(content) > self.MAX_DOWNLOAD_BYTES:
                return
            suffix = self._guess_suffix(content_type, url)
            self.images_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.images_dir / f"{key}{suffix}"
            out_path.write_bytes(content)
            sticker["local_path"] = str(out_path)
            content_hash = hashlib.sha256(content).hexdigest()
            sticker["content_hash"] = content_hash
            sticker["id"] = content_hash[:8].upper()
        except Exception as exc:
            logger.warning(f"[sticker_suite] cache image failed: {exc}")

    def _guess_suffix(self, content_type: str, url: str) -> str:
        lowered_url = url.lower().split("?", 1)[0]
        for suffix in [".gif", ".png", ".jpg", ".jpeg", ".webp"]:
            if lowered_url.endswith(suffix):
                return suffix
        content_type = content_type.lower()
        if "gif" in content_type:
            return ".gif"
        if "webp" in content_type:
            return ".webp"
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
        return ".png"

    def _file_sha256(self, path_value: str) -> str:
        try:
            path = Path(path_value)
            if not path.exists() or not path.is_file():
                return ""
            digest = hashlib.sha256()
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except Exception:
            return ""

    def _short_id(self, key: str, sticker: dict[str, Any]) -> str:
        existing = str(sticker.get("id") or "")
        if existing:
            return existing
        content_hash = str(sticker.get("content_hash") or "")
        return (content_hash or key)[:8].upper()

    def _ensure_sticker_metadata(self, key: str, sticker: dict[str, Any]) -> None:
        sticker.setdefault("id", self._short_id(key, sticker))
        local_path = str(sticker.get("local_path") or "")
        if local_path and not sticker.get("content_hash"):
            content_hash = self._file_sha256(local_path)
            if content_hash:
                sticker["content_hash"] = content_hash
                sticker["id"] = content_hash[:8].upper()

    def _merge_unique_lists(self, target: dict[str, Any], source: dict[str, Any], names: list[str]) -> None:
        for name in names:
            merged = list(target.get(name, []))
            for item in source.get(name, []) if isinstance(source.get(name, []), list) else []:
                merged = self._append_unique(merged, item, self.MAX_CONTEXTS if name == "contexts" else None)
            target[name] = merged

    def _merge_sticker_data(self, target: dict[str, Any], source: dict[str, Any]) -> None:
        target["source"] = {**source.get("source", {}), **target.get("source", {})}
        if not target.get("local_path") and source.get("local_path"):
            target["local_path"] = source.get("local_path")
        if not target.get("content_hash") and source.get("content_hash"):
            target["content_hash"] = source.get("content_hash")
        target["send_count"] = int(target.get("send_count", 0) or 0) + int(source.get("send_count", 0) or 0)
        target["seen_count"] = int(target.get("seen_count", 0) or 0) + int(source.get("seen_count", 0) or 0)
        target["created_at"] = min(int(target.get("created_at", 0) or 0), int(source.get("created_at", 0) or 0))
        target["last_seen_at"] = max(int(target.get("last_seen_at", 0) or 0), int(source.get("last_seen_at", 0) or 0))
        self._merge_unique_lists(target, source, ["sender_ids", "group_ids", "tags", "contexts"])

    def _merge_duplicate_stickers(self, stickers: dict[str, Any]) -> int:
        buckets: dict[str, str] = {}
        removed: list[str] = []
        for key, sticker in list(stickers.items()):
            if not isinstance(sticker, dict):
                removed.append(str(key))
                continue
            self._ensure_sticker_metadata(str(key), sticker)
            source = sticker.get("source") or {}
            identity_parts: list[str] = []
            for name in ["md5", "file_id", "file", "path", "url"]:
                value = str(source.get(name) or "")
                if value:
                    identity_parts.append(f"{name}:{value}")
            content_hash = str(sticker.get("content_hash") or "")
            if content_hash:
                identity_parts.append(f"content:{content_hash}")
            if not identity_parts:
                local_path = str(sticker.get("local_path") or "")
                if not local_path or not Path(local_path).exists():
                    continue
                identity_parts.append(f"weak:{str(source.get('summary') or '')}:{local_path}")
            canonical_key = ""
            for identity in identity_parts:
                if identity in buckets:
                    canonical_key = buckets[identity]
                    break
            if not canonical_key:
                canonical_key = str(key)
                for identity in identity_parts:
                    buckets[identity] = canonical_key
                continue
            if canonical_key == str(key):
                continue
            target = stickers.get(canonical_key)
            if isinstance(target, dict):
                self._merge_sticker_data(target, sticker)
                for identity in identity_parts:
                    buckets[identity] = canonical_key
            removed.append(str(key))
        for key in removed:
            stickers.pop(key, None)
        return len(removed)

    def _purge_weak_uncached_stickers(self, stickers: dict[str, Any]) -> int:
        removed = 0
        for key, sticker in list(stickers.items()):
            if not isinstance(sticker, dict):
                stickers.pop(key, None)
                removed += 1
                continue
            source = sticker.get("source") or {}
            local_path = str(sticker.get("local_path") or "")
            has_cache = bool(local_path and Path(local_path).exists())
            has_identity = self._source_has_identity(source) or bool(sticker.get("content_hash"))
            if not has_cache and not has_identity:
                stickers.pop(str(key), None)
                removed += 1
        return removed

    def _cleanup_data(self, data: dict[str, Any], group_key: str | None = None) -> int:
        removed = 0
        if group_key is not None:
            groups = data.get("groups") or {}
            group = groups.get(group_key)
            if isinstance(group, dict):
                stickers = group.setdefault("stickers", {})
                removed += self._purge_weak_uncached_stickers(stickers)
                removed += self._merge_duplicate_stickers(stickers)
        else:
            for group in (data.get("groups") or {}).values():
                if isinstance(group, dict):
                    stickers = group.setdefault("stickers", {})
                    removed += self._purge_weak_uncached_stickers(stickers)
                    removed += self._merge_duplicate_stickers(stickers)
        shared = self._get_shared(data)
        shared_stickers = shared.setdefault("stickers", {})
        removed += self._purge_weak_uncached_stickers(shared_stickers)
        removed += self._merge_duplicate_stickers(shared_stickers)
        return removed

    def _should_ignore_text(self, event: AstrMessageEvent, text: str) -> bool:
        if not text:
            return True
        if str(event.get_sender_id()) == str(event.get_self_id() or ""):
            return True
        normalized = text.strip()
        if not normalized:
            return True
        if normalized.startswith("/"):
            return True
        if normalized.startswith("表情"):
            return True
        return False

    def _normalize_trigger_word(self, word: str) -> str | None:
        normalized = word.strip()
        if not normalized or len(normalized) > 20:
            return None
        return normalized

    def _trigger_map(self, group: dict[str, Any], shared: dict[str, Any] | None = None) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        maps = [group.get("triggers") or {}]
        if shared is not None:
            maps.append(shared.get("triggers") or {})
        for trigger_map in maps:
            if not isinstance(trigger_map, dict):
                continue
            for tag, words in trigger_map.items():
                normalized_tag = self._normalize_tag(str(tag))
                if normalized_tag is None:
                    continue
                current = result.setdefault(normalized_tag, [])
                if isinstance(words, list):
                    for word in words:
                        normalized_word = self._normalize_trigger_word(str(word))
                        if normalized_word and normalized_word not in current:
                            current.append(normalized_word)
        return result

    def _context_score(self, text: str, sticker: dict[str, Any]) -> int:
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 2:
            return 0
        score = 0
        for context in sticker.get("contexts") or []:
            context_compact = re.sub(r"\s+", "", str(context))
            if not context_compact:
                continue
            if compact in context_compact or context_compact in compact:
                score = max(score, 2)
                continue
            for size in [4, 3, 2]:
                if len(compact) < size:
                    continue
                if any(compact[index : index + size] in context_compact for index in range(0, len(compact) - size + 1)):
                    score = max(score, 1)
                    break
        return score

    def _retrieve_sticker_candidates(self, group: dict[str, Any], shared: dict[str, Any], text: str) -> list[tuple[int, int, str, dict[str, Any], str]]:
        compact = re.sub(r"\s+", "", text)
        if not compact:
            return []
        tag_scores: dict[str, tuple[int, str]] = {}
        mood = self._infer_mood(compact)
        if mood is not None:
            tag_scores[self.TAG_LABELS[mood]] = (5, "情绪词")
        trigger_map = self._trigger_map(group, shared if group.get("allow_shared", False) else None)
        for tag, words in trigger_map.items():
            for word in words:
                if word and word in compact:
                    old_score = tag_scores.get(tag, (0, ""))[0]
                    if 8 > old_score:
                        tag_scores[tag] = (8, f"同义词:{word}")
        for tag in self._all_tags(group, shared if group.get("allow_shared", False) else None):
            if tag and tag in compact:
                old_score = tag_scores.get(tag, (0, ""))[0]
                if 10 > old_score:
                    tag_scores[tag] = (10, f"标签:{tag}")

        pools = [(0, group.get("stickers") or {})]
        if group.get("allow_shared", False):
            pools.append((1, shared.get("stickers") or {}))
        candidates: list[tuple[int, int, str, dict[str, Any], str]] = []
        for priority, pool in pools:
            for key, sticker in pool.items():
                if not isinstance(sticker, dict):
                    continue
                local_path = str(sticker.get("local_path") or "")
                if not local_path or not Path(local_path).exists():
                    continue
                best_score = 0
                best_reason = ""
                for tag in sticker.get("tags") or []:
                    score, reason = tag_scores.get(str(tag), (0, ""))
                    if score > best_score:
                        best_score = score
                        best_reason = reason
                context_score = self._context_score(text, sticker)
                if context_score > best_score:
                    best_score = context_score
                    best_reason = "上下文"
                if best_score <= 0:
                    continue
                candidates.append((best_score, priority, str(key), sticker, best_reason))
        candidates.sort(key=lambda item: (-item[0], item[1], int(item[3].get("send_count", 0) or 0), -int(item[3].get("last_seen_at", 0) or 0), item[2]))
        return candidates

    def _find_trigger_tag(self, group: dict[str, Any], shared: dict[str, Any], text: str) -> str | None:
        compact = re.sub(r"\s+", "", text)
        if not compact:
            return None
        mood = self._infer_mood(compact)
        if mood is not None:
            return self.TAG_LABELS[mood]
        shared_for_tags = shared if group.get("allow_shared", False) else None
        for tag in sorted(self._all_tags(group, shared_for_tags), key=len, reverse=True):
            if tag and tag in compact:
                return tag
        return None

    def _should_try_send(self, group: dict[str, Any], shared: dict[str, Any], text: str) -> tuple[bool, tuple[str, dict[str, Any]] | None]:
        if not group.get("enabled", False):
            return False, None
        candidates = self._retrieve_sticker_candidates(group, shared, text)
        if not candidates:
            return False, None
        now = self._now()
        cooldown = int(group.get("cooldown_seconds", self.DEFAULT_COOLDOWN_SECONDS) or self.DEFAULT_COOLDOWN_SECONDS)
        if now - int(group.get("last_sent_at", 0) or 0) < cooldown:
            return False, (candidates[0][2], candidates[0][3])
        if int(group.get("test_mode_until", 0) or 0) > now:
            return True, (candidates[0][2], candidates[0][3])
        seed = hashlib.sha256(f"{text}:{now // 60}:{candidates[0][2]}".encode("utf-8")).hexdigest()
        if int(seed[:8], 16) % self.TRIGGER_PROBABILITY_DENOMINATOR != 0:
            return False, (candidates[0][2], candidates[0][3])
        return True, (candidates[0][2], candidates[0][3])

    def _choose_sticker(self, group: dict[str, Any], shared: dict[str, Any], tag: str) -> tuple[str, dict[str, Any]] | None:
        pools = [group.get("stickers") or {}]
        if group.get("allow_shared", False):
            pools.append(shared.get("stickers") or {})
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for priority, pool in enumerate(pools):
            for key, sticker in pool.items():
                if not isinstance(sticker, dict):
                    continue
                local_path = str(sticker.get("local_path") or "")
                if not local_path or not Path(local_path).exists():
                    continue
                tags = sticker.get("tags") or []
                if tag in tags:
                    candidates.append((priority, str(key), sticker))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], int(item[2].get("send_count", 0) or 0), -int(item[2].get("last_seen_at", 0) or 0), item[1]))
        _, key, sticker = candidates[0]
        return key, sticker

    def _cached_stickers(self, group: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        stickers = group.get("stickers") or {}
        candidates: list[tuple[str, dict[str, Any]]] = []
        for key, sticker in stickers.items():
            if not isinstance(sticker, dict):
                continue
            local_path = str(sticker.get("local_path") or "")
            if local_path and Path(local_path).exists():
                candidates.append((str(key), sticker))
        return candidates

    def _cached_stickers_with_shared(self, group: dict[str, Any], shared: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        candidates = self._cached_stickers(group)
        if not group.get("allow_shared", False):
            return candidates
        local_keys = {key for key, _ in candidates}
        for key, sticker in self._cached_stickers(shared):
            if key not in local_keys:
                candidates.append((key, sticker))
        return candidates

    def _choose_any_cached_sticker(self, group: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        candidates = self._cached_stickers(group)
        if not candidates:
            return None
        seed = hashlib.sha256(str(self._now() // 3).encode("utf-8")).hexdigest()
        index = int(seed[:8], 16) % len(candidates)
        return candidates[index]

    def _choose_recent_cached_sticker(self, group: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        candidates = self._cached_stickers(group)
        if not candidates:
            return None
        candidates.sort(key=lambda item: (int(item[1].get("last_seen_at", 0) or 0), int(item[1].get("created_at", 0) or 0), item[0]), reverse=True)
        return candidates[0]

    def _extract_result_text(self, result: Any) -> str:
        parts: list[str] = []
        for attr in ["chain", "message", "message_chain", "messages"]:
            value = getattr(result, attr, None)
            if isinstance(value, list):
                for component in value:
                    text = getattr(component, "text", None)
                    if text is None:
                        text = getattr(component, "plain", None)
                    if text is not None and str(text).strip():
                        parts.append(str(text).strip())
            elif isinstance(value, str) and value.strip():
                parts.append(value.strip())
        for attr in ["text", "content"]:
            value = getattr(result, attr, None)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        return "\n".join(parts).strip()

    def _result_has_image(self, result: Any) -> bool:
        for attr in ["chain", "message", "message_chain", "messages"]:
            value = getattr(result, attr, None)
            if not isinstance(value, list):
                continue
            for component in value:
                class_name = component.__class__.__name__.lower()
                if "image" in class_name or "picture" in class_name:
                    return True
        return False

    def _build_image_component(self, local_path: str) -> Any:
        for name in ["fromFileSystem", "from_file_system", "fromFile", "from_file", "fromPath", "from_path"]:
            factory = getattr(Image, name, None)
            if callable(factory):
                try:
                    return factory(local_path)
                except TypeError:
                    continue
        for kwargs in [{"file": local_path}, {"path": local_path}, {"url": local_path}]:
            try:
                return Image(**kwargs)
            except TypeError:
                continue
        return None

    def _append_image_to_result(self, result: Any, local_path: str) -> bool:
        component = self._build_image_component(local_path)
        if component is None:
            return False
        for attr in ["chain", "message", "message_chain", "messages"]:
            value = getattr(result, attr, None)
            if isinstance(value, list):
                value.append(component)
                return True
        return False

    def _should_follow_reply(self, group: dict[str, Any], shared: dict[str, Any], text: str) -> tuple[bool, tuple[str, dict[str, Any]] | None]:
        if not group.get("follow_enabled", False):
            return False, None
        if not text or text.strip().startswith("表情"):
            return False, None
        candidates = self._retrieve_sticker_candidates(group, shared, text)
        if not candidates:
            return False, None
        now = self._now()
        # 测试模式期内绕过冷却，但仍受 follow_enabled 控制。
        if int(group.get("follow_test_mode_until", 0) or 0) > now:
            return True, (candidates[0][2], candidates[0][3])
        cooldown = int(group.get("follow_cooldown_seconds", 120) or 120)
        if now - int(group.get("last_follow_sent_at", 0) or 0) < cooldown:
            return False, (candidates[0][2], candidates[0][3])
        return True, (candidates[0][2], candidates[0][3])

    def _choose_recent_sticker(self, group: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        stickers = group.get("stickers") or {}
        candidates = [(str(key), sticker) for key, sticker in stickers.items() if isinstance(sticker, dict)]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (int(item[1].get("last_seen_at", 0) or 0), int(item[1].get("created_at", 0) or 0), item[0]), reverse=True)
        return candidates[0]

    def _normalize_tag(self, tag: str) -> str | None:
        normalized = re.sub(r"\s+", "", tag.strip())
        if not normalized or len(normalized) > 12:
            return None
        aliases = {
            "开心": "笑",
            "快乐": "笑",
            "哈哈": "笑",
            "笑死": "笑",
            "阴阳": "阴阳怪气",
            "嘲讽": "阴阳怪气",
            "乐子": "阴阳怪气",
            "无奈": "无语",
            "离谱": "无语",
            "逆天": "无语",
            "抱抱": "贴贴",
            "摸摸": "贴贴",
            "可爱": "贴贴",
            "晚安": "困",
            "睡觉": "困",
            "累": "困",
        }
        return aliases.get(normalized, normalized)

    def _all_tags(self, group: dict[str, Any], shared: dict[str, Any] | None = None) -> set[str]:
        tags: set[str] = set(self.TAG_LABELS.values())
        pools = [group.get("stickers") or {}]
        if shared is not None:
            pools.append(shared.get("stickers") or {})
        for pool in pools:
            for sticker in pool.values():
                if not isinstance(sticker, dict):
                    continue
                for tag in sticker.get("tags") or []:
                    if isinstance(tag, str) and tag.strip():
                        tags.add(tag.strip())
        return tags

    def _tag_counts(self, group: dict[str, Any], shared: dict[str, Any] | None = None) -> dict[str, int]:
        counts = {label: 0 for label in sorted(self._all_tags(group, shared))}
        pools = [group.get("stickers") or {}]
        if shared is not None:
            pools.append(shared.get("stickers") or {})
        for pool in pools:
            for sticker in pool.values():
                if not isinstance(sticker, dict):
                    continue
                for tag in sticker.get("tags") or []:
                    if tag in counts:
                        counts[tag] += 1
        return counts

    def _indexed_stickers(self, group: dict[str, Any], include_uncached: bool = True) -> list[tuple[str, dict[str, Any]]]:
        stickers = group.get("stickers") or {}
        candidates: list[tuple[str, dict[str, Any]]] = []
        for key, sticker in stickers.items():
            if not isinstance(sticker, dict):
                continue
            if not include_uncached:
                local_path = str(sticker.get("local_path") or "")
                if not local_path or not Path(local_path).exists():
                    continue
            candidates.append((str(key), sticker))
        candidates.sort(key=lambda item: (int(item[1].get("last_seen_at", 0) or 0), int(item[1].get("created_at", 0) or 0), item[0]), reverse=True)
        return candidates

    def _indexed_stickers_with_shared(self, group: dict[str, Any], shared: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        candidates = self._indexed_stickers(group)
        if not group.get("allow_shared", False):
            return candidates
        local_keys = {key for key, _ in candidates}
        for key, sticker in self._indexed_stickers(shared):
            if key not in local_keys:
                candidates.append((key, sticker))
        candidates.sort(key=lambda item: (int(item[1].get("last_seen_at", 0) or 0), int(item[1].get("created_at", 0) or 0), item[0]), reverse=True)
        return candidates

    def _sticker_by_index(self, group: dict[str, Any], index: int) -> tuple[str, dict[str, Any]] | None:
        candidates = self._indexed_stickers(group)
        if index < 1 or index > len(candidates):
            return None
        return candidates[index - 1]

    def _retag_sticker(self, group: dict[str, Any], shared: dict[str, Any], key: str, sticker: dict[str, Any]) -> int:
        texts = list(sticker.get("contexts") or [])
        source = sticker.get("source") or {}
        texts.extend(self._source_texts_for_auto_tag(source))
        added = 0
        for text in texts:
            for tag in self._infer_tags_for_sticker(group, shared, str(text)):
                old_len = len(list(sticker.get("tags", [])))
                sticker["tags"] = self._append_unique(list(sticker.get("tags", [])), tag)
                if len(sticker["tags"]) > old_len:
                    added += 1
        shared_sticker = shared.setdefault("stickers", {}).get(key)
        if isinstance(shared_sticker, dict):
            for tag in sticker.get("tags", []):
                shared_sticker["tags"] = self._append_unique(list(shared_sticker.get("tags", [])), tag)
        return added

    def _sticker_by_ref(self, group: dict[str, Any], ref: str, shared: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]] | None:
        candidates = self._indexed_stickers_with_shared(group, shared) if shared is not None else self._indexed_stickers(group)
        if ref.isdigit():
            index = int(ref)
            if index < 1 or index > len(candidates):
                return None
            return candidates[index - 1]
        normalized = ref.strip().lstrip("#").upper()
        for key, sticker in candidates:
            if self._short_id(key, sticker).upper() == normalized:
                return key, sticker
        return None

    def _sticker_line(self, index: int, key: str, sticker: dict[str, Any]) -> str:
        self._ensure_sticker_metadata(key, sticker)
        short_id = self._short_id(key, sticker)
        tags = "、".join(sticker.get("tags") or []) or "无标签"
        cached = "可发" if str(sticker.get("local_path") or "") and Path(str(sticker.get("local_path") or "")).exists() else "未缓存"
        seen = int(sticker.get("seen_count", 0) or 0)
        sent = int(sticker.get("send_count", 0) or 0)
        source = sticker.get("source") or {}
        identity = "强" if self._source_has_identity(source) or sticker.get("content_hash") else "弱"
        name = str(source.get("file") or source.get("summary") or source.get("md5") or source.get("url") or "表情")
        if len(name) > 20:
            name = name[:17] + "..."
        return f"{index}. #{short_id} [{cached}/{identity}] {tags}｜见{seen}/发{sent}｜{name}"

    def _delete_shared_group_ref(self, shared: dict[str, Any], key: str, group_key: str) -> None:
        shared_stickers = shared.get("stickers") or {}
        shared_sticker = shared_stickers.get(key)
        if not isinstance(shared_sticker, dict):
            return
        group_ids = [item for item in list(shared_sticker.get("group_ids", [])) if str(item) != group_key]
        if group_ids:
            shared_sticker["group_ids"] = group_ids
        else:
            shared_stickers.pop(key, None)

    def _mark_sent(self, group: dict[str, Any], sticker: dict[str, Any]) -> None:
        sticker["send_count"] = int(sticker.get("send_count", 0) or 0) + 1
        group["last_sent_at"] = self._now()

    @filter.command("表情随机")
    async def random_sticker(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        selected = self._choose_any_cached_sticker(group)
        if selected is None and group.get("allow_shared", False):
            selected = self._choose_any_cached_sticker(shared)
        if selected is None:
            yield event.plain_result("当前群还没有可发送的缓存表情。请先发几张表情包入库，或检查图片 URL/本地路径是否可用。")
            return
        _, sticker = selected
        local_path = str(sticker.get("local_path") or "")
        self._mark_sent(group, sticker)
        self._save_data(data)
        yield event.image_result(local_path)

    @filter.command("表情最近")
    async def recent_sticker(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        selected = self._choose_recent_cached_sticker(group)
        if selected is None:
            yield event.plain_result("当前群还没有可发送的缓存表情。请先发一张表情包入库后再试。")
            return
        _, sticker = selected
        local_path = str(sticker.get("local_path") or "")
        self._mark_sent(group, sticker)
        self._save_data(data)
        yield event.image_result(local_path)

    @filter.regex(r"^表情标记\s+(?:(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s+)?(\S+)\s*$")
    async def mark_recent_sticker(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情标记\s+(?:(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s+)?(\S+)\s*$", event.get_message_str().strip())
        if not match:
            return
        index_text, raw_tag = match.groups()
        tag = self._normalize_tag(raw_tag)
        if tag is None:
            yield event.plain_result("标签不能为空，且最多 12 个字。示例：表情标记 笑死、表情标记 3 吃瓜")
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        selected = self._sticker_by_ref(group, index_text, shared) if index_text else self._choose_recent_sticker(group)
        if selected is None:
            yield event.plain_result("没有找到这个表情。先用 表情列表 查看编号，或先发一张表情包再标记。")
            return
        key, sticker = selected
        sticker["tags"] = self._append_unique(list(sticker.get("tags", [])), tag)
        shared_sticker = shared.setdefault("stickers", {}).setdefault(key, {**sticker, "group_ids": []})
        shared_sticker["tags"] = self._append_unique(list(shared_sticker.get("tags", [])), tag)
        shared_sticker["local_path"] = sticker.get("local_path", shared_sticker.get("local_path", ""))
        shared_sticker["source"] = {**shared_sticker.get("source", {}), **sticker.get("source", {})}
        shared_sticker["group_ids"] = self._append_unique(list(shared_sticker.get("group_ids", [])), group_key)
        self._save_data(data)
        target = f"第{index_text}张" if index_text else "最近入库的表情"
        yield event.plain_result(f"已给{target}标记：{tag}。之后消息里包含“{tag}”时可触发。")

    @filter.regex(r"^表情删标\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s+(\S+)\s*$")
    async def remove_sticker_tag(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情删标\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s+(\S+)\s*$", event.get_message_str().strip())
        if not match:
            return
        ref, raw_tag = match.groups()
        tag = self._normalize_tag(raw_tag)
        if tag is None:
            yield event.plain_result("标签不能为空，且最多 12 个字。示例：表情删标 1 吃瓜")
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        selected = self._sticker_by_ref(group, ref, shared)
        if selected is None:
            yield event.plain_result("没有找到这个编号或 ID。请先用 表情列表 查看。")
            return
        key, sticker = selected
        old_tags = list(sticker.get("tags", []))
        sticker["tags"] = [item for item in old_tags if item != tag]
        shared_sticker = shared.setdefault("stickers", {}).get(key)
        if isinstance(shared_sticker, dict):
            shared_sticker["tags"] = [item for item in list(shared_sticker.get("tags", [])) if item != tag]
        self._save_data(data)
        if len(sticker["tags"]) == len(old_tags):
            yield event.plain_result(f"这张表情没有标签：{tag}")
        else:
            yield event.plain_result(f"已删除表情 {ref} 的标签：{tag}")

    @filter.regex(r"^表情清标\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s*$")
    async def clear_sticker_tags(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情清标\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s*$", event.get_message_str().strip())
        if not match:
            return
        ref = match.group(1)
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        selected = self._sticker_by_ref(group, ref, shared)
        if selected is None:
            yield event.plain_result("没有找到这个编号或 ID。请先用 表情列表 查看。")
            return
        key, sticker = selected
        removed_count = len(list(sticker.get("tags", [])))
        sticker["tags"] = []
        shared_sticker = shared.setdefault("stickers", {}).get(key)
        if isinstance(shared_sticker, dict):
            shared_sticker["tags"] = []
        self._save_data(data)
        yield event.plain_result(f"已清空表情 {ref} 的标签，共删除 {removed_count} 个。")

    @filter.command("表情自动标记开")
    async def enable_auto_tagging(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["auto_tag_enabled"] = True
        if group.get("auto_tag_mode", "strict") == "off":
            group["auto_tag_mode"] = "strict"
        self._save_data(data)
        yield event.plain_result("表情自动标记已开启（严格模式）。")

    @filter.command("表情自动标记关")
    async def disable_auto_tagging(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["auto_tag_enabled"] = False
        self._save_data(data)
        yield event.plain_result("表情自动标记已关闭，仍可手动使用 表情标记。")

    @filter.regex(r"^表情自动标记模式\s*(严格|关闭)\s*$")
    async def set_auto_tagging_mode(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情自动标记模式\s*(严格|关闭)\s*$", event.get_message_str().strip())
        if not match:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        mode = "off" if match.group(1) == "关闭" else "strict"
        group["auto_tag_mode"] = mode
        group["auto_tag_enabled"] = mode != "off"
        self._save_data(data)
        yield event.plain_result(f"表情自动标记模式已设置为：{match.group(1)}")

    @filter.command("表情自动标记状态")
    async def auto_tagging_status(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        mode_label = "关闭" if group.get("auto_tag_mode", "strict") == "off" else "严格"
        lines = [
            f"自动标记：{'开启' if group.get('auto_tag_enabled', True) else '关闭'}",
            f"自动标记模式：{mode_label}",
            "标记来源：同条文字、同发送者上一条文字、表情元数据文字、直接标签、动态同义词、内置情绪词。",
            "说明：只会自动追加标签，不会删除手动标签。",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.regex(r"^表情重标记\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s*$")
    async def retag_sticker(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情重标记\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s*$", event.get_message_str().strip())
        if not match:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        selected = self._sticker_by_ref(group, match.group(1), shared)
        if selected is None:
            yield event.plain_result("没有找到这个编号或 ID。请先用 表情列表 查看。")
            return
        key, sticker = selected
        added = self._retag_sticker(group, shared, key, sticker)
        self._save_data(data)
        yield event.plain_result(f"表情 {match.group(1)} 重标记完成，新增标签 {added} 个。")

    @filter.command("表情重标记全部")
    async def retag_all_stickers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        total_added = 0
        for key, sticker in self._indexed_stickers(group):
            total_added += self._retag_sticker(group, shared, key, sticker)
        self._save_data(data)
        yield event.plain_result(f"当前群表情重标记完成，共新增标签 {total_added} 个。")

    @filter.regex(r"^表情列表(?:\s+(\d+))?\s*$")
    async def sticker_list(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情列表(?:\s+(\d+))?\s*$", event.get_message_str().strip())
        page = int(match.group(1)) if match and match.group(1) else 1
        page = max(1, page)
        page_size = 10
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        stickers = self._indexed_stickers_with_shared(group, shared)
        if not stickers:
            yield event.plain_result("当前群还没有入库表情。")
            return
        total_pages = (len(stickers) + page_size - 1) // page_size
        page = min(page, total_pages)
        start = (page - 1) * page_size
        prefix = "当前群+共享表情列表" if group.get("allow_shared", False) else "当前群表情列表"
        lines = [f"{prefix}（第 {page}/{total_pages} 页，共 {len(stickers)} 张）："]
        for index, (key, sticker) in enumerate(stickers[start : start + page_size], start + 1):
            lines.append(self._sticker_line(index, key, sticker))
        self._save_data(data)
        lines.append("用法：表情发送 编号/#ID｜表情标记 编号/#ID 标签｜表情删标 编号/#ID 标签｜表情清标 编号/#ID｜表情删除 编号/#ID")
        yield event.plain_result("\n".join(lines))

    @filter.regex(r"^表情发送\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s*$")
    async def send_indexed_sticker(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情发送\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s*$", event.get_message_str().strip())
        if not match:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        selected = self._sticker_by_ref(group, match.group(1), shared)
        if selected is None:
            yield event.plain_result("没有找到这个编号或 ID。请先用 表情列表 查看。")
            return
        _, sticker = selected
        local_path = str(sticker.get("local_path") or "")
        if not local_path or not Path(local_path).exists():
            yield event.plain_result("这张表情没有本地缓存，暂时不能发送。")
            return
        self._mark_sent(group, sticker)
        self._save_data(data)
        yield event.image_result(local_path)

    @filter.regex(r"^表情删除\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s*$")
    async def delete_indexed_sticker(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情删除\s+(\d+|#[A-Fa-f0-9]{8}|[A-Fa-f0-9]{8})\s*$", event.get_message_str().strip())
        if not match:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        selected = self._sticker_by_ref(group, match.group(1), shared)
        if selected is None:
            yield event.plain_result("没有找到这个编号或 ID。请先用 表情列表 查看。")
            return
        key, _ = selected
        group.setdefault("stickers", {}).pop(key, None)
        self._delete_shared_group_ref(shared, key, group_key)
        self._save_data(data)
        yield event.plain_result(f"已删除当前群表情 {match.group(1)}。")

    @filter.command("表情清理重复")
    async def cleanup_duplicate_stickers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        removed = self._cleanup_data(data, group_key)
        self._save_data(data)
        yield event.plain_result(f"表情重复清理完成，合并/移除重复记录 {removed} 条。")

    @filter.regex(r"^表情同义词\s+(\S+)\s+(\S+)\s*$")
    async def add_sticker_trigger(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情同义词\s+(\S+)\s+(\S+)\s*$", event.get_message_str().strip())
        if not match:
            return
        tag = self._normalize_tag(match.group(1))
        word = self._normalize_trigger_word(match.group(2))
        if tag is None or word is None:
            yield event.plain_result("用法：表情同义词 标签 触发词，标签最多12字，触发词最多20字。")
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        triggers = group.setdefault("triggers", {})
        words = list(triggers.get(tag, []))
        triggers[tag] = self._append_unique(words, word)
        self._save_data(data)
        yield event.plain_result(f"已添加同义触发：{word} -> {tag}")

    @filter.regex(r"^表情删同义词\s+(\S+)\s+(\S+)\s*$")
    async def remove_sticker_trigger(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情删同义词\s+(\S+)\s+(\S+)\s*$", event.get_message_str().strip())
        if not match:
            return
        tag = self._normalize_tag(match.group(1))
        word = self._normalize_trigger_word(match.group(2))
        if tag is None or word is None:
            yield event.plain_result("用法：表情删同义词 标签 触发词。")
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        triggers = group.setdefault("triggers", {})
        old_words = list(triggers.get(tag, []))
        triggers[tag] = [item for item in old_words if item != word]
        if not triggers[tag]:
            triggers.pop(tag, None)
        self._save_data(data)
        if len(old_words) == len(triggers.get(tag, [])):
            yield event.plain_result(f"没有找到同义触发：{word} -> {tag}")
        else:
            yield event.plain_result(f"已删除同义触发：{word} -> {tag}")

    @filter.regex(r"^表情清同义词\s+(\S+)\s*$")
    async def clear_sticker_triggers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情清同义词\s+(\S+)\s*$", event.get_message_str().strip())
        if not match:
            return
        tag = self._normalize_tag(match.group(1))
        if tag is None:
            yield event.plain_result("用法：表情清同义词 标签。")
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        triggers = group.setdefault("triggers", {})
        removed_count = len(list(triggers.get(tag, [])))
        triggers.pop(tag, None)
        self._save_data(data)
        yield event.plain_result(f"已清空 {tag} 的同义触发词，共删除 {removed_count} 个。")

    @filter.command("表情同义词列表")
    async def list_sticker_triggers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        triggers = self._trigger_map(group, shared if group.get("allow_shared", False) else None)
        if not triggers:
            yield event.plain_result("当前没有配置同义触发词。")
            return
        lines = ["当前同义触发词："]
        for tag in sorted(triggers):
            lines.append(f"- {tag}：{'、'.join(triggers[tag])}")
        lines.append(f"跨群表情：{'开启' if group.get('allow_shared', False) else '关闭'}")
        yield event.plain_result("\n".join(lines))

    @filter.command("表情标签")
    async def sticker_tags(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        counts = self._tag_counts(group, shared if group.get("allow_shared", False) else None)
        cached_count = len(self._cached_stickers_with_shared(group, shared))
        lines = ["当前可触发标签："]
        lines.extend([f"- {tag}：{count}张" for tag, count in counts.items() if count > 0 or tag in self.TAG_LABELS.values()])
        lines.append(f"跨群表情：{'开启' if group.get('allow_shared', False) else '关闭'}")
        lines.append(f"可发送缓存表情：{cached_count}张")
        yield event.plain_result("\n".join(lines))

    @filter.command("表情跨群开")
    async def enable_shared_stickers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["allow_shared"] = True
        self._save_data(data)
        yield event.plain_result("跨群表情复用已开启。")

    @filter.command("表情跨群关")
    async def disable_shared_stickers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["allow_shared"] = False
        self._save_data(data)
        yield event.plain_result("跨群表情复用已关闭，本群只会使用自己学到的表情。")

    @filter.command("表情跟随开")
    async def enable_follow_stickers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["follow_enabled"] = True
        self._save_data(data)
        yield event.plain_result("机器人回复跟随表情已开启。")

    @filter.command("表情跟随关")
    async def disable_follow_stickers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["follow_enabled"] = False
        self._save_data(data)
        yield event.plain_result("机器人回复跟随表情已关闭。")

    @filter.regex(r"^表情跟随冷却\s*(\d+)\s*$")
    async def follow_sticker_cooldown(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情跟随冷却\s*(\d+)\s*$", event.get_message_str().strip())
        if not match:
            return
        seconds = max(30, int(match.group(1)))
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["follow_cooldown_seconds"] = seconds
        self._save_data(data)
        yield event.plain_result(f"表情跟随冷却已设置为 {seconds} 秒。")

    @filter.command("表情跟随测试开")
    async def enable_follow_test_mode(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["follow_enabled"] = True
        group["follow_cooldown_seconds"] = 30
        group["follow_test_mode_until"] = self._now() + 600
        self._save_data(data)
        yield event.plain_result("表情跟随测试模式已开启 10 分钟：跟随开启，冷却 30 秒。")

    @filter.command("表情跟随测试关")
    async def disable_follow_test_mode(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["follow_test_mode_until"] = 0
        self._save_data(data)
        yield event.plain_result("表情跟随测试模式已关闭。")

    @filter.command("表情测试开")
    async def enable_test_mode(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["enabled"] = True
        group["cooldown_seconds"] = 30
        group["test_mode_until"] = self._now() + 600
        self._save_data(data)
        yield event.plain_result("表情测试模式已开启 10 分钟：自动复用开启，冷却 30 秒，触发概率临时提高到 100%。")

    @filter.command("表情测试关")
    async def disable_test_mode(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["test_mode_until"] = 0
        self._save_data(data)
        yield event.plain_result("表情测试模式已关闭。")

    @filter.command("表情开")
    async def enable_stickers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["enabled"] = True
        self._save_data(data)
        yield event.plain_result("表情包自动复用已开启。")

    @filter.command("表情关")
    async def disable_stickers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["enabled"] = False
        self._save_data(data)
        yield event.plain_result("表情包自动复用已关闭，但仍会继续学习表情包。")

    @filter.command("表情库状态")
    async def sticker_status(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        stickers = group.get("stickers") or {}
        shared_stickers = shared.get("stickers") or {}
        effective_count = len(self._indexed_stickers_with_shared(group, shared))
        lines = [
            f"本群表情包数量：{len(stickers)}",
        ]
        if group.get("allow_shared", False):
            lines.append(f"共享池表情包数量：{len(shared_stickers)}")
            lines.append(f"当前可用表情包数量：{effective_count}")
        lines.extend(
            [
                f"自动复用：{'开启' if group.get('enabled', False) else '关闭'}",
                f"自动标记：{'开启' if group.get('auto_tag_enabled', True) else '关闭'}",
                f"跟随回复：{'开启' if group.get('follow_enabled', False) else '关闭'}",
                f"冷却：{int(group.get('cooldown_seconds', self.DEFAULT_COOLDOWN_SECONDS) or self.DEFAULT_COOLDOWN_SECONDS)}秒",
                f"跟随冷却：{int(group.get('follow_cooldown_seconds', 120) or 120)}秒",
                f"当前心情：{group.get('mood', 'neutral')}",
                f"跨群表情：{'开启' if group.get('allow_shared', False) else '关闭'}",
            ]
        )
        yield event.plain_result("\n".join(lines))

    @filter.command("表情心情")
    async def sticker_mood(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        yield event.plain_result(f"当前心情：{group.get('mood', 'neutral')}")

    @filter.regex(r"^表情冷却\s*(\d+)\s*$")
    async def sticker_cooldown(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        match = re.match(r"^表情冷却\s*(\d+)\s*$", event.get_message_str().strip())
        if not match:
            return
        seconds = max(30, int(match.group(1)))
        data = self._load_data()
        group = self._get_group(data, group_key)
        group["cooldown_seconds"] = seconds
        self._save_data(data)
        yield event.plain_result(f"表情冷却已设置为 {seconds} 秒。")

    def _get_event_result(self, event: AstrMessageEvent) -> Any:
        getter = getattr(event, "get_result", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                pass
        for attr in ["result", "_result", "message_result"]:
            value = getattr(event, attr, None)
            if value is not None:
                return value
        return None

    @_optional_filter_decorator("on_decorating_result")
    async def follow_reply_sticker(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return
        result = self._get_event_result(event)
        if result is None:
            logger.info("[sticker_suite] follow skipped: no event result")
            return
        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        if not group.get("follow_enabled", False):
            return
        if self._result_has_image(result):
            return
        reply_text = self._extract_result_text(result)
        if not reply_text:
            logger.info("[sticker_suite] follow skipped: empty reply text")
            return
        should_send, selected = self._should_follow_reply(group, shared, reply_text)
        if not should_send or selected is None:
            logger.info("[sticker_suite] follow skipped: no candidate or cooldown")
            return
        key, sticker = selected
        local_path = str(sticker.get("local_path") or "")
        if not local_path or not Path(local_path).exists():
            return
        if not self._append_image_to_result(result, local_path):
            logger.warning("[sticker_suite] follow failed: cannot append image to result")
            return
        sticker["send_count"] = int(sticker.get("send_count", 0) or 0) + 1
        group["last_follow_sent_at"] = self._now()
        self._save_data(data)
        logger.info(f"[sticker_suite] follow appended sticker: key={key}")

    @filter.command("表情探针状态")
    async def probe_status(self, event: AstrMessageEvent):
        yield event.plain_result(self.probe.status_text())

    @filter.command("表情探针详情")
    async def probe_detail(self, event: AstrMessageEvent):
        yield event.plain_result(self.probe.detail_text())

    @filter.event_message_type(filter.EventMessageType.ALL)
    @filter.regex(r".*")
    async def learn_and_send_stickers(self, event: AstrMessageEvent):
        group_key = self._get_group_key(event)
        if group_key is None:
            return

        data = self._load_data()
        group = self._get_group(data, group_key)
        shared = self._get_shared(data)
        text = self._extract_message_text(event)
        self.probe.capture(event)
        if self._is_self_event(event):
            return
        images = self._extract_images(event)
        if images:
            changed = self._record_stickers(group, shared, images, text, str(event.get_sender_id()), group_key)
            if text and not self._should_ignore_text(event, text):
                self._remember_recent_text(group, text, str(event.get_sender_id()))
                changed = True
            if changed:
                self._save_data(data)
            return

        if not self._should_ignore_text(event, text):
            self._remember_recent_text(group, text, str(event.get_sender_id()))
            self._save_data(data)

        mood = self._infer_mood(text)
        if mood is not None:
            group["mood"] = mood
            self._save_data(data)

        if self._should_ignore_text(event, text):
            return

        should_send, selected = self._should_try_send(group, shared, text)
        if not should_send or selected is None:
            return

        key, sticker = selected
        local_path = str(sticker.get("local_path") or "")
        if not local_path:
            return

        sticker["send_count"] = int(sticker.get("send_count", 0) or 0) + 1
        group["last_sent_at"] = self._now()
        self._save_data(data)
        yield event.image_result(local_path)
