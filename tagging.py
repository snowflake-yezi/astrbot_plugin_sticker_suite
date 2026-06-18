from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .constants import MOOD_KEYWORDS, TAG_LABELS, TAG_SEMANTIC_GROUPS


# ── leaf utilities ──────────────────────────────────────────────────────


def normalize_tag(tag: str) -> str | None:
    """规范化标签：去空白、限长 12 字、别名映射。"""
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


def normalize_trigger_word(word: str) -> str | None:
    """规范化触发词：去空白、限长 20 字。"""
    normalized = word.strip()
    if not normalized or len(normalized) > 20:
        return None
    return normalized


def append_unique(values: list[Any], value: Any, limit: int | None = None) -> list[Any]:
    """向列表追加不重复元素，可选截断到 limit。"""
    if value not in values:
        values.append(value)
    if limit is not None and len(values) > limit:
        return values[-limit:]
    return values


def infer_mood(
    text: str,
    mood_keywords: dict[str, list[str]] | None = None,
) -> str | None:
    """从文本推断心情/情绪标签。"""
    if mood_keywords is None:
        mood_keywords = MOOD_KEYWORDS
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return None
    for mood, keywords in mood_keywords.items():
        if any(keyword in compact for keyword in keywords):
            return mood
    return None


def tags_from_text(
    text: str,
    tag_labels: dict[str, str] | None = None,
    mood_keywords: dict[str, list[str]] | None = None,
) -> list[str]:
    """从文本提取情绪标签列表。"""
    if tag_labels is None:
        tag_labels = TAG_LABELS
    mood = infer_mood(text, mood_keywords=mood_keywords)
    if mood is None:
        return []
    return [tag_labels[mood]]


def source_texts_for_auto_tag(source: dict[str, str]) -> list[str]:
    """从表情 source 字段提取可用于自动标记的文本。

    过滤 [图片]、[动画表情]、mface、纯 md5 等噪声。
    """
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


def tag_text_variants(tag: str) -> list[tuple[str, int, str]]:
    """生成一个标签的可匹配文本变体，附带分数和命中原因。

    给检索侧用，不参与自动打标。覆盖"标签是'被欺负'但回复里只有'欺负'"
    这类一字之差的常见情况：
    - 原标签：10 分
    - 去常见前缀（被/不/没/小/老）或后缀（吧/啊/啦/呢/了/的）：7 分
    - 标签 ≥ 3 字时的连续 2/3 字子串：4 分（防过宽，2 字标签不做）
    """
    variants: list[tuple[str, int, str]] = []
    seen: set[str] = set()

    def push(text: str, score: int, reason: str) -> None:
        if not text or text == tag or text in seen:
            return
        seen.add(text)
        variants.append((text, score, reason))

    variants.append((tag, 10, f"标签:{tag}"))
    seen.add(tag)

    prefixes = ("被", "不", "没", "小", "老")
    suffixes = ("吧", "啊", "啦", "呢", "了", "的")
    for prefix in prefixes:
        if tag.startswith(prefix) and len(tag) > len(prefix):
            push(tag[len(prefix):], 7, f"标签去前缀:{tag}")
    for suffix in suffixes:
        if tag.endswith(suffix) and len(tag) > len(suffix):
            push(tag[: -len(suffix)], 7, f"标签去后缀:{tag}")

    if len(tag) >= 3:
        for size in (3, 2):
            if len(tag) < size:
                continue
            for index in range(0, len(tag) - size + 1):
                push(tag[index : index + size], 4, f"标签子串:{tag}")
    return variants


# ── time-dependent helpers ──────────────────────────────────────────────


def remember_recent_text(
    group: dict[str, Any],
    text: str,
    sender_id: str,
    *,
    now: Callable[[], int],
) -> None:
    """记录同群最近一条文本，用于"先发文字再补表情"的自动标记。"""
    normalized = text.strip()
    if not normalized or normalized.startswith("/") or normalized.startswith("表情"):
        return
    now_ts = now()
    recent = [item for item in list(group.get("recent_texts", [])) if now_ts - int(item.get("created_at", 0) or 0) <= 120]
    recent.append({"sender_id": str(sender_id), "text": normalized, "created_at": now_ts})
    group["recent_texts"] = recent[-10:]


def previous_text_for_auto_tag(
    group: dict[str, Any],
    sender_id: str,
    *,
    now: Callable[[], int],
) -> str:
    """获取同一发送者 60 秒内上一条文字，用于自动标记。"""
    if group.get("auto_tag_mode", "strict") == "off":
        return ""
    now_ts = now()
    for item in reversed(list(group.get("recent_texts", []))):
        if str(item.get("sender_id") or "") != str(sender_id):
            continue
        if now_ts - int(item.get("created_at", 0) or 0) > 60:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            return text
    return ""


# ── data-access helpers ─────────────────────────────────────────────────


def all_tags(
    group: dict[str, Any],
    shared: dict[str, Any] | None = None,
    *,
    tag_labels: dict[str, str] | None = None,
) -> set[str]:
    """收集当前群（及可选共享池）所有出现过的标签。"""
    if tag_labels is None:
        tag_labels = TAG_LABELS
    tags: set[str] = set(tag_labels.values())
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


def trigger_map(
    group: dict[str, Any],
    shared: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """构建标签 → 同义触发词列表的映射。

    合并当前群和可选共享池的同义词配置；所有标签和触发词都会过规范化。
    """
    result: dict[str, list[str]] = {}
    maps: list[dict[str, Any]] = [group.get("triggers") or {}]
    if shared is not None:
        maps.append(shared.get("triggers") or {})
    for trigger_data in maps:
        if not isinstance(trigger_data, dict):
            continue
        for tag, words in trigger_data.items():
            normalized_tag = normalize_tag(str(tag))
            if normalized_tag is None:
                continue
            current = result.setdefault(normalized_tag, [])
            if isinstance(words, list):
                for word in words:
                    normalized_word = normalize_trigger_word(str(word))
                    if normalized_word and normalized_word not in current:
                        current.append(normalized_word)
    return result


def tag_counts(
    group: dict[str, Any],
    shared: dict[str, Any] | None = None,
) -> dict[str, int]:
    """统计每个标签下有多少张表情。"""
    counts = {label: 0 for label in sorted(all_tags(group, shared))}
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


# ── auto-tag inference ──────────────────────────────────────────────────


def infer_tags_for_sticker(
    group: dict[str, Any],
    shared: dict[str, Any],
    text: str,
) -> list[str]:
    """根据一段文本为表情推断标签（最多 3 个）。

    信号优先级：直接标签 (10) > 同义词 (8) > 情绪词 (5) > 短文本 (4)。
    只追加标签，不删除已有标签。
    """
    compact = re.sub(r"\s+", "", text)
    if not compact or not group.get("auto_tag_enabled", True):
        return []
    shared_for_tags = shared if group.get("allow_shared", False) else None
    scored: dict[str, tuple[int, str]] = {}
    for tag in all_tags(group, shared_for_tags):
        if tag and tag in compact:
            scored[tag] = max(scored.get(tag, (0, "")), (10, "标签"), key=lambda item: item[0])
    for tag, words in trigger_map(group, shared_for_tags).items():
        for word in words:
            if word and word in compact:
                scored[tag] = max(scored.get(tag, (0, "")), (8, "同义词"), key=lambda item: item[0])
    mood = infer_mood(compact)
    if mood is not None:
        tag = TAG_LABELS[mood]
        scored[tag] = max(scored.get(tag, (0, "")), (5, "情绪词"), key=lambda item: item[0])
    normalized = normalize_tag(compact)
    if normalized is not None and len(compact) <= 12:
        scored[normalized] = max(scored.get(normalized, (0, "")), (4, "短文本"), key=lambda item: item[0])
    return [tag for tag, _ in sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))[:3]]


def infer_tags_from_texts_for_sticker(
    group: dict[str, Any],
    shared: dict[str, Any],
    texts: list[str],
) -> list[str]:
    """从多条文本综合推断表情标签（最多 3 个）。"""
    tags: list[str] = []
    for text in texts:
        for tag in infer_tags_for_sticker(group, shared, text):
            tags = append_unique(tags, tag, 3)
    return tags


def retag_sticker(
    group: dict[str, Any],
    shared: dict[str, Any],
    key: str,
    sticker: dict[str, Any],
) -> int:
    """对单张表情重新跑自动标记，只追加不删除。返回新增标签数。"""
    texts = list(sticker.get("contexts") or [])
    source = sticker.get("source") or {}
    texts.extend(source_texts_for_auto_tag(source))
    added = 0
    for text in texts:
        for tag in infer_tags_for_sticker(group, shared, str(text)):
            old_len = len(list(sticker.get("tags", [])))
            sticker["tags"] = append_unique(list(sticker.get("tags", [])), tag)
            if len(sticker["tags"]) > old_len:
                added += 1
    shared_sticker = shared.setdefault("stickers", {}).get(key)
    if isinstance(shared_sticker, dict):
        for tag in sticker.get("tags", []):
            shared_sticker["tags"] = append_unique(list(shared_sticker.get("tags", [])), tag)
    return added
