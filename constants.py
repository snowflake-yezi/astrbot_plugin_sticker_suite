from __future__ import annotations

"""Shared constants for the sticker memory plugin.

The plugin needs to recognize QQ/NapCat image-like payloads from several
slightly different object shapes. Keep these field lists in one place so the
learning path and the diagnostic probe stay aligned when AstrBot/NapCat changes
its message structure.
"""

DEFAULT_COOLDOWN_SECONDS = 300
MAX_CONTEXTS = 12
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
TRIGGER_PROBABILITY_DENOMINATOR = 3

# 识图（OCR/多模态）默认配置
# 仅在 vision_enabled=True 时生效；冷却用于无上下文表情，防止同类无字图反复识。
VISION_COOLDOWN_MINUTES_DEFAULT = 10
VISION_COOLDOWN_MIN_MINUTES = 1
VISION_COOLDOWN_MAX_MINUTES = 1440
VISION_MODES = ("ocr", "llm", "auto")
VISION_TIMEOUT_SECONDS = 5

IMAGE_FIELD_NAMES = {
    "url",
    "URL",
    "file",
    "fileName",
    "file_name",
    "file_id",
    "fileUuid",
    "file_unique",
    "image_id",
    "path",
    "sourcePath",
    "local_path",
    "md5",
    "md5HexStr",
    "hash",
    "width",
    "height",
    "picWidth",
    "picHeight",
    "summary",
    "sub_type",
    "picElement",
}

# Strong identity fields are the values that make a sticker reusable and
# deduplicatable. Summary-only records such as "[图片]" are intentionally not
# enough; otherwise normal @/image-like payload noise can create duplicate junk.
IMAGE_IDENTITY_FIELD_NAMES = {
    "url",
    "URL",
    "file",
    "fileName",
    "file_name",
    "file_id",
    "fileUuid",
    "file_unique",
    "image_id",
    "path",
    "sourcePath",
    "local_path",
    "md5",
    "md5HexStr",
    "hash",
    "picElement",
}

MOOD_KEYWORDS: dict[str, list[str]] = {
    "happy": ["哈哈", "笑死", "绷不住", "乐", "草", "好耶"],
    "teasing": ["急了", "典", "孝", "偷笑", "乐子", "赢"],
    "annoyed": ["无语", "离谱", "啊这", "逆天", "蚌埠住"],
    "soft": ["抱抱", "摸摸", "可爱", "别哭", "贴贴"],
    "tired": ["困", "晚安", "睡觉", "累了", "睡了"],
}

TAG_LABELS: dict[str, str] = {
    "happy": "笑",
    "teasing": "阴阳怪气",
    "annoyed": "无语",
    "soft": "贴贴",
    "tired": "困",
}

# 语义词组：把"被欺负 / 嘲笑 / 委屈 / 破防 / 笑 / 生气 / 无语"这类常见标签
# 家族预先扩展一组语义近义词。检索时如果一个标签命中其家族里的任意词，给
# 7 分（介于"标签字面 10"和"情绪词 5"之间）。
# 设计原则：词组只包含明确指向该家族的词，避免引入广义噪声；自定义标签如
# 果出现在这里的家族 key 上会自动受益，未在表里的自定义标签不影响。
TAG_SEMANTIC_GROUPS: dict[str, list[str]] = {
    "被欺负": ["欺负", "得寸进尺", "认输", "投降", "讨厌", "别这样", "求饶", "呜呜", "委屈", "受气", "可怜", "不公平", "不行了", "饶了我"],
    "委屈": ["委屈", "呜呜", "眼泪", "想哭", "受气", "鼻子酸", "可怜兮兮", "不公平"],
    "嘲笑": ["嘲笑", "笑话你", "笑死", "丢人", "丢脸", "可笑", "嗤", "嘿嘿", "嘲讽", "讽刺", "看你笑话"],
    "阴阳怪气": ["阴阳", "嘲讽", "讽刺", "孝", "典", "急了", "破防", "赢麻了", "笑死", "看你笑话"],
    "破防": ["破防", "绷不住", "崩溃", "崩了", "撑不住", "心态崩了", "顶不住"],
    "无语": ["无语", "离谱", "啊这", "蚌埠住", "逆天", "无奈", "翻白眼", "无话可说", "懒得说"],
    "生气": ["生气", "气死", "气炸", "暴怒", "怒火", "可恶", "讨厌", "恼火", "不爽", "炸了", "我谢谢你"],
    "笑": ["笑死", "哈哈", "绷不住", "好笑", "好乐", "笑喷", "笑岔气", "好耶"],
    "贴贴": ["抱抱", "摸摸", "亲亲", "贴贴", "蹭蹭", "好可爱", "爱你", "rua"],
    "困": ["困", "晚安", "睡觉", "累了", "好累", "想睡", "瞌睡", "打哈欠"],
    "吃瓜": ["吃瓜", "围观", "看戏", "瓜", "搬好小板凳", "好戏"],
    "震惊": ["震惊", "卧槽", "天哪", "我去", "不会吧", "?", "！？", "离谱"],
}

