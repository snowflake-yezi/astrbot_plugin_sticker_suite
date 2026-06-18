from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable


def _path_exists(path_value: str) -> bool:
    return bool(path_value and Path(path_value).exists())


def sticker_help_text() -> str:
    return "\n".join(
        [
            "表情库帮助",
            "",
            "基础：",
            "- 表情库帮助：查看本帮助",
            "- 表情库状态：查看数量、冷却、开关、心情",
            "- 表情开 / 表情关：开启/关闭自动复用",
            "- 表情测试开 / 表情测试关：开启/关闭 10 分钟测试模式",
            "- 表情冷却 秒数：设置自动发表情冷却，最低 30 秒",
            "- 表情心情：查看当前心情",
            "",
            "发送与查看：",
            "- 表情随机：随机发送一张已缓存表情",
            "- 表情最近：发送最近入库的一张已缓存表情",
            "- 表情列表 [页码]：查看表情列表",
            "- 表情详情 编号/#ID：查看单张表情详情",
            "- 表情发送 编号/#ID：发送指定表情",
            "- 表情删除 编号/#ID：删除指定表情",
            "- 表情清理重复：合并/移除重复记录",
            "",
            "标签：",
            "- 表情标记 [编号/#ID] 标签：给最近或指定表情加标签",
            "- 表情删标 编号/#ID 标签：删除指定标签",
            "- 表情清标 编号/#ID：清空标签",
            "- 表情标签：查看可触发标签和缓存数量",
            "",
            "自动标记：",
            "- 表情自动标记开 / 关 / 状态：管理自动标记",
            "- 表情自动标记模式 严格/关闭：设置自动标记模式",
            "- 表情重标记 [编号/#ID]：重新推断单张标签",
            "- 表情重标记全部：重新推断全部标签",
            "",
            "识图：",
            "- 表情识图开 / 关 / 状态：管理 OCR 识图",
            "- 表情识图模式 ocr/llm/auto：设置识图模式",
            "- 表情识图冷却 分钟数：设置识图冷却",
            "- 表情重识图 编号/#ID：重跑单张识图",
            "- 表情重识图全部：重跑全部识图",
            "",
            "同义词：",
            "- 表情同义词 标签 触发词：增加触发词",
            "- 表情删同义词 标签 触发词：删除触发词",
            "- 表情清同义词 标签：清空触发词",
            "- 表情同义词列表：查看触发词",
            "",
            "跨群与跟随：",
            "- 表情跨群开 / 关：允许/禁止使用共享表情",
            "- 表情跟随开 / 关：机器人回复后跟发表情",
            "- 表情跟随冷却 秒数：设置跟随冷却",
            "- 表情跟随测试开 / 关：开启/关闭跟随测试模式",
            "",
            "探针：",
            "- 表情探针开 [分钟数] / 探针开 [分钟数]：开启诊断探针",
            "- 表情探针关：关闭探针",
            "- 表情探针状态：查看探针状态",
            "- 表情探针详情：查看最近图片/表情事件结构",
            "",
            "说明：",
            "- 所有 表情... 指令均支持 / 前缀。",
            "- 编号来自 表情列表，#ID 是稳定短 ID，例如 #A1B2C3D4。",
        ]
    )


def format_timestamp(timestamp: Any) -> str:
    try:
        value = int(timestamp or 0)
    except (TypeError, ValueError):
        return "无"
    if value <= 0:
        return "无"
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))
    except Exception:
        return str(value)


def clip_text(text: Any, limit: int = 80) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def sticker_line(
    index: int,
    key: str,
    sticker: dict[str, Any],
    *,
    ensure_metadata: Callable[[str, dict[str, Any]], None],
    short_id: Callable[[str, dict[str, Any]], str],
    source_has_identity: Callable[[dict[str, Any]], bool],
    path_exists: Callable[[str], bool] = _path_exists,
) -> str:
    ensure_metadata(key, sticker)
    sticker_id = short_id(key, sticker)
    tags = "、".join(sticker.get("tags") or []) or "无标签"
    local_path = str(sticker.get("local_path") or "")
    cached = "可发" if path_exists(local_path) else "未缓存"
    seen = int(sticker.get("seen_count", 0) or 0)
    sent = int(sticker.get("send_count", 0) or 0)
    source = sticker.get("source") or {}
    identity = "强" if source_has_identity(source) or sticker.get("content_hash") else "弱"
    name = str(source.get("file") or source.get("summary") or source.get("md5") or source.get("url") or "表情")
    if len(name) > 20:
        name = name[:17] + "..."
    return f"{index}. #{sticker_id} [{cached}/{identity}] {tags}｜见{seen}/发{sent}｜{name}"


def find_sticker_index(indexed_stickers: Iterable[tuple[str, dict[str, Any]]], key: str, sticker: dict[str, Any]) -> int | None:
    for index, (candidate_key, candidate) in enumerate(indexed_stickers, 1):
        if candidate_key == key or candidate is sticker:
            return index
    return None


def sticker_detail_text(
    group: dict[str, Any],
    shared: dict[str, Any],
    key: str,
    sticker: dict[str, Any],
    *,
    ensure_metadata: Callable[[str, dict[str, Any]], None],
    short_id: Callable[[str, dict[str, Any]], str],
    source_has_identity: Callable[[dict[str, Any]], bool],
    indexed_stickers_with_shared: Callable[[dict[str, Any], dict[str, Any]], list[tuple[str, dict[str, Any]]]],
    path_exists: Callable[[str], bool] = _path_exists,
) -> str:
    ensure_metadata(key, sticker)
    sticker_id = short_id(key, sticker)
    index = find_sticker_index(indexed_stickers_with_shared(group, shared), key, sticker)
    local_stickers = group.get("stickers") or {}
    source_scope = "当前群" if sticker is local_stickers.get(key) or key in local_stickers else "共享池"
    local_path = str(sticker.get("local_path") or "")
    cached = "可发" if path_exists(local_path) else "未缓存"
    source = sticker.get("source") or {}
    identity = "强" if source_has_identity(source) or sticker.get("content_hash") else "弱"
    tags = "、".join(str(item) for item in sticker.get("tags") or [] if str(item).strip()) or "无标签"
    contexts = [clip_text(item) for item in sticker.get("contexts") or [] if clip_text(item)]
    ocr_text = clip_text(sticker.get("ocr_text"), 120) or "无"
    vision_engine = str(sticker.get("vision_engine") or "无")
    seen = int(sticker.get("seen_count", 0) or 0)
    sent = int(sticker.get("send_count", 0) or 0)
    source_items = []
    for name in ["summary", "file", "file_id", "md5", "path", "url"]:
        value = clip_text(source.get(name), 80)
        if value:
            source_items.append(f"{name}={value}")
    source_text = "；".join(source_items[:4]) or "无"
    content_hash = str(sticker.get("content_hash") or "")
    hash_text = content_hash[:16] + "..." if len(content_hash) > 16 else (content_hash or "无")
    group_ids = [str(item) for item in sticker.get("group_ids") or [] if str(item).strip()]
    sender_count = len([item for item in sticker.get("sender_ids") or [] if str(item).strip()])

    lines = [f"表情详情 #{sticker_id}"]
    if index is not None:
        lines.append(f"编号：{index}")
    lines.extend(
        [
            f"来源：{source_scope}",
            f"状态：{cached}/{identity}",
            f"标签：{tags}",
            f"OCR：{ocr_text}",
            f"视觉引擎：{vision_engine}",
            f"统计：见{seen}/发{sent}",
            f"入库时间：{format_timestamp(sticker.get('created_at'))}",
            f"最近见到：{format_timestamp(sticker.get('last_seen_at'))}",
            f"来源摘要：{source_text}",
            f"内容哈希：{hash_text}",
        ]
    )
    if group_ids:
        lines.append(f"关联群数：{len(group_ids)}")
    if sender_count:
        lines.append(f"记录发送者数：{sender_count}")
    if contexts:
        lines.append("上下文：")
        for context in contexts[:5]:
            lines.append(f"- {context}")
        if len(contexts) > 5:
            lines.append(f"- ... 还有 {len(contexts) - 5} 条")
    else:
        lines.append("上下文：无")
    return "\n".join(lines)
