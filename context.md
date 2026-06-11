# sticker_suite 项目上下文（精简版）

最后整理：2026-06-10

## 1. 项目定位

`astrbot_plugin_sticker_suite` 是一个 AstrBot QQ 群表情套件插件。

目标是把“机器人发表情”从硬编码关键词判断，升级为一个本地、可解释、可控的表情记忆与检索系统：

- 自动学习群聊里的 QQ 图片 / 表情包。
- 根据文字、上下文、元数据和本地 OCR 给表情追加标签。
- 根据用户消息或机器人回复内容检索合适表情。
- 支持自动触发、机器人回复跟随、群隔离、跨群共享、冷却、测试模式。
- 内置 NapCat / AstrBot 表情结构探针，便于排查不同消息结构里的图片字段。

技术路线：优先本地确定性规则，不接外部向量库；OCR 使用本地 `rapidocr-onnxruntime`，默认关闭；多模态 LLM 识图入口已预留但暂未实现。

## 2. 当前目录结构

```text
astrbot_plugin_sticker_suite/
  __init__.py        # 导出 StickerSuitePlugin
  main.py            # AstrBot 插件入口；命令、钩子、主流程仍集中在这里
  constants.py       # 图片字段、默认冷却、内置情绪词、语义词组、OCR 配置
  image_extract.py   # 从 AstrBot/NapCat 消息结构中提取图片/表情字段
  probe.py           # 内置表情探针；只做诊断，不参与入库/发送/标记
  vision.py          # 本地 OCR 封装；懒加载 rapidocr_onnxruntime
  metadata.yaml      # 插件元数据
  README.md          # 用户文档和命令说明
  requirements.txt   # 可选 OCR 依赖
  context.md         # 当前精简上下文
```

## 3. 核心数据与存储

数据文件：

```text
data/stickers.json
data/images/
```

每个群有独立数据：

- `enabled`：用户消息自动触发开关。
- `cooldown_seconds` / `last_sent_at`：用户消息自动发送冷却。
- `allow_shared`：是否允许跨群使用共享池。
- `follow_enabled`：机器人回复跟随开关。
- `follow_cooldown_seconds` / `last_follow_sent_at`：回复跟随冷却。
- `follow_test_mode_until`：回复跟随测试模式到期时间。
- `auto_tag_enabled`：自动标记开关，默认开启。
- `auto_tag_mode`：自动标记模式，目前主要是 `strict` / `off`。
- `recent_texts`：同群最近文本缓存，用于“先发文字再补表情”的自动标记。
- `vision_enabled` / `vision_mode` / `vision_cooldown_minutes` / `last_vision_at`：本地 OCR 识图配置。
- `probe_enabled` / `probe_until`：探针开关与限时开启。
- `triggers`：动态同义词。
- `stickers`：当前群表情记录。

共享池：

- `shared.stickers`：所有群同步写入的一份共享表情池。
- `shared.triggers`：共享同义词。

默认行为：群隔离。只有执行 `表情跨群开` 后，当前群才会在列表、发送、标记、自动触发中看到共享池。

## 4. 表情入库规则

入口在 `main.py` 的 `learn_and_send_stickers()`，图片提取逻辑主要在 `image_extract.py`。

入库时会从以下结构提取图片/表情：

- AstrBot message components。
- `raw_message`。
- message object 嵌套对象。
- `picElement`。
- `image` / `mface` 段。

只有带强身份字段的记录才入库，例如：

- `md5`
- `file_id`
- `file`
- `path`
- `url`

只有 `summary=表情`、`[图片]` 这类弱记录会被忽略，避免重复垃圾入库。

重复合并依据包括：

- `md5`
- `file_id`
- `file`
- `path`
- `url`
- 本地文件内容哈希

机器人自己发出的消息会跳过学习，避免自己发图又被自己学进去。

## 5. 自动标记规则

自动标记默认开启，严格模式下只追加标签，不删除手动标签。

自动标记文本来源按优先级理解为：

1. 同条消息文字：例如 `笑死 + 表情包`。
2. 同一发送者 60 秒内上一条文字：例如先发 `笑死我了`，再立刻补一张表情包。
3. 表情元数据文字：`summary`、`file`、`path` 文件名等，过滤 `[图片]`、`[动画表情]`、`mface`、长 md5 等噪声。
4. OCR 文本：开启 `表情识图开` 后，本地 OCR 识别出的文字会写入上下文并参与自动标记。

自动标记使用的标签推断信号：

- 直接标签命中。
- 动态同义词命中。
- 内置情绪词命中。
- 短文本标签。

每张表情最多自动追加 3 个标签。

手动纠错命令仍然保留并优先：

- `表情标记 编号/#ID 标签`
- `表情删标 编号/#ID 标签`
- `表情清标 编号/#ID`

历史表情可重跑自动标记：

- `表情重标记 编号/#ID`
- `表情重标记全部`

## 6. 本地检索与选择规则

核心检索函数：`main.py` 的 `_retrieve_sticker_candidates()`。

用户消息和机器人回复跟随都复用同一套检索层。

评分规则：

| 分数 | 命中类型 | 说明 |
|---|---|---|
| 10 | 标签字面命中 | 文本直接包含表情标签 |
| 8 | 动态同义词命中 | `表情同义词 生气 恼火` 后，文本含 `恼火` |
| 7 | 标签变体命中 | 去掉常见前/后缀后匹配 |
| 6 | 内置语义词组 | 被欺负、委屈、嘲笑、阴阳怪气、破防等标签家族 |
| 5 | 内置情绪词 | `笑死` → `笑`，`离谱` → `无语` |
| 4 | 标签连续子串 | 三字及以上标签的连续 2/3 字子串 |
| 1-2 | 历史上下文重叠 | 当前文本和表情保存过的上下文有短文本重叠 |

候选选择顺序：

1. 分数高优先。
2. 当前群表情优先于共享池。
3. `send_count` 少的优先，避免刷同一张。
4. `last_seen_at` 近的优先。
5. 仍相同则按内部 key 稳定排序。

注意：检索侧的“变体 / 语义词组 / 子串”只用于触发匹配，不影响自动打标。

## 7. 两种自动发表情方式

### 7.1 用户消息触发

开关：

```text
表情开
表情关
表情测试开
表情测试关
```

流程：

```text
用户普通文本
  -> 检索候选表情
  -> 冷却判断
  -> 概率门控
  -> 自动发一张表情
```

图片消息优先用于学习入库，不会同时触发自动发表情。

测试模式持续 10 分钟：自动开启复用、冷却改 30 秒、概率门控视为通过。

### 7.2 机器人回复跟随

开关：

```text
表情跟随开
表情跟随关
表情跟随测试开
表情跟随测试关
表情跟随冷却 秒数
```

流程：

```text
机器人生成回复
  -> AstrBot on_decorating_result
  -> 插件从 event 中读取 result
  -> 提取回复文本
  -> 检索候选表情
  -> 追加 Image 到回复结果链
```

已知 AstrBot 兼容点：

- AstrBot v4.25.1 的 `result_decorate` 调用形式是 `handler(event)`。
- 不是 `handler(event, result)`。
- 所以 `follow_reply_sticker()` 必须只接收 `event`，再从 `event.get_result()` / `event.result` / `event._result` / `event.message_result` 中尝试取 result。

已修复过的问题：

- 命令响应会跳过跟随。否则 `/表情标记 xxx` 的回复文本包含标签，可能误触发表情并消耗跟随冷却。
- 冷却与无候选日志已拆开，便于调试。

## 8. 本地 OCR 识图

模块：`vision.py`。

依赖：

```text
rapidocr-onnxruntime
```

默认关闭。开启命令：

```text
表情识图开
表情识图关
表情识图模式 ocr/llm/auto
表情识图冷却 分钟数
表情识图状态
表情重识图 编号/#ID
表情重识图全部
```

当前实现：

- `ocr`：使用本地 RapidOCR。
- `auto`：目前等价于优先 OCR。
- `llm`：入口保留，但多模态 LLM 兜底暂未实现。

设计约束：

- 只在识图开启后处理无上下文表情。
- OCR 成功识别文字后，写入 `ocr_text`、`vision_engine`，追加到 `contexts`，并复用自动打标逻辑。
- OCR 跑过但无文字，也推进冷却，避免无字图反复消耗资源。

## 9. 内置探针

模块：`probe.py`。

用途：诊断 AstrBot / NapCat 表情消息结构，不参与业务决策。

命令：

```text
表情探针开
表情探针开 10
探针开
探针开 10
表情探针关
表情探针状态
表情探针详情
```

默认关闭，避免长期记录 raw message 摘要、图片字段、URL/file/md5 等诊断信息导致日志膨胀或增加风控风险。

开启期间日志前缀保留：

```text
[sticker_probe]
```

业务主日志前缀：

```text
[sticker_suite]
```

## 10. 常用命令索引

基础：

```text
表情开 / 表情关
表情测试开 / 表情测试关
表情库状态
表情冷却 秒数
表情心情
```

表情管理：

```text
表情列表 [页码]
表情详情 编号/#ID
表情发送 编号/#ID
表情随机
表情最近
表情删除 编号/#ID
表情清理重复
```

标签与同义词：

```text
表情标记 [编号/#ID] 标签
表情删标 编号/#ID 标签
表情清标 编号/#ID
表情标签
表情同义词 标签 触发词
表情删同义词 标签 触发词
表情清同义词 标签
表情同义词列表
```

自动标记：

```text
表情自动标记开
表情自动标记关
表情自动标记模式 严格/关闭
表情自动标记状态
表情重标记 编号/#ID
表情重标记全部
```

回复跟随：

```text
表情跟随开
表情跟随关
表情跟随冷却 秒数
表情跟随测试开
表情跟随测试关
```

跨群：

```text
表情跨群开
表情跨群关
```

OCR：

```text
表情识图开
表情识图关
表情识图模式 ocr/llm/auto
表情识图冷却 分钟数
表情识图状态
表情重识图 编号/#ID
表情重识图全部
```

探针：

```text
表情探针开 [分钟数]
探针开 [分钟数]
表情探针关
表情探针状态
表情探针详情
```

所有 `表情...` 命令兼容 AstrBot `/` 前缀，例如 `/表情库状态`。

## 11. 已修复的重要问题

### 11.1 合并工程化修复

- `init.py` 已改为 `__init__.py`，确保相对导入正常。
- 类名统一为 `StickerSuitePlugin`。
- 主日志前缀统一为 `[sticker_suite]`。
- 探针日志保留 `[sticker_probe]`。
- 探针状态命令返回真实状态，不再是静态文案。
- `_optional_filter_decorator` 缺失钩子时会 warning，不再静默。
- `表情自动标记开` 会复位 `auto_tag_mode=strict`，避免 enabled=True 但模式仍 off。
- 跟随测试模式会读取 `follow_test_mode_until`，测试期绕过跟随冷却。
- `metadata.yaml` 版本已到 `1.1.0`。

### 11.2 回复跟随钩子签名问题

曾出现：

```text
TypeError: StickerMemoryPlugin.follow_reply_sticker() missing 1 required positional argument: 'result'
```

根因：AstrBot v4.25.1 调用 `handler(event)`，不是 `handler(event, result)`。

修复：跟随函数只接收 `event`，内部从 event 取 result。

### 11.3 命令响应误触发跟随

曾出现：`表情标记 xxx` 的命令响应包含标签，触发跟随并消耗冷却。

修复：入站消息以 `/` 或 `表情` 开头时，回复跟随直接跳过。

### 11.4 探针默认关闭

探针现在默认关闭，只在手动开启或限时开启时捕获事件，避免日志膨胀和风控风险。

### 11.5 本地 OCR 接入

`vision.py` 已接入 `rapidocr-onnxruntime`，OCR 结果能进入上下文并参与自动标记。

### 11.6 表情详情命令

已增加 `表情详情 编号/#ID`，用于查看单张表情的标签、上下文、OCR 文本、来源摘要、缓存/身份状态，以及发送/见到次数等详情。

## 12. 当前未完成 / 后续方向

优先级较高：

1. 继续工程化拆分 `main.py`。
   - 建议先拆纯逻辑，保留 AstrBot 装饰器和命令注册在 `main.py`。
   - 候选模块：`storage.py`、`tagging.py`、`retrieval.py`、`selection.py`、`commands/`。
2. 增加选择策略。
   - 例如稳定、轮换、前 N 随机、随机度低/中/高。
3. 完善 OCR / 视觉能力。
   - 当前只有本地 OCR；多模态 LLM 兜底未实现。

暂不建议默认做：

- 使用别人上一条消息给表情打标。容易误标，除非后续增加宽松模式。
- 默认调用云端视觉模型。涉及成本、隐私、延迟和稳定性。

## 13. 工程约定

- 行为变化、命令变化、触发规则变化、跨群规则变化、自动标记规则变化：同步更新 `README.md` 和项目记录。
- `main.py` 拆分时，优先移动纯逻辑；AstrBot 装饰器注册留在 `main.py`，降低插件加载风险。
- 提交前运行：

```text
python -m py_compile <改动的 .py 文件>
```那么

- commit 信息不要加 `Co-Authored-By: Claude`。
- 旧插件目录 `astrbot_plugin_sticker_memory` / `astrbot_plugin_sticker_probe` 如果仍存在，运行时不要和 suite 同时启用，否则会重复注册同名命令。

## 14. 当前一句话总结

这是一个 AstrBot 表情记忆与复用插件：它会学习群里的表情包，用本地规则和可选 OCR 自动打标签，再根据用户消息或机器人回复检索最合适的一张表情发送；目前核心功能已闭环，下一步主要是继续拆分 `main.py`、补充详情命令和选择策略。
