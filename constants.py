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
