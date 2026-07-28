# Codex 每日早报自动化运行手册

## 职责边界

Codex 负责需要判断力的工作：X/社区线索发现、逐条打开原文、第一方来源补查、整期取舍、开场与收束文案、冻结新闻事实块的编排，以及成片视觉与字幕验片。

本地代码负责不可绕过的边界：故事全集、claim 白名单、来源 URL、社交账号白名单、截图身份、稿件合同、音频门、Remotion 渲染、媒体哈希与 B 站 preflight。

自动化不得修改仓库源码，不得读取或写入任何外部模型密钥。B 站上传只能发生在全部来源、文案、画面、音频和 preflight 门禁通过之后，并使用用户已明确授权的每日 08:00 定时发布参数。

## 每日顺序

### 1. 先补 X 三条赛道

X 是当天首发发现的最高优先级。读取 [`codex-social-leads.example.json`](codex-social-leads.example.json) 和 `sources.yaml` 中三个 `agent_social` 源，逐个打开白名单账号最近 24 小时原帖：

- 官方公司/产品：`OpenAI`、`OpenAIDevs`、`ChatGPTapp`、`AnthropicAI`、`GoogleDeepMind`、`GoogleAI`、`grok`、`xai`、`Alibaba_Qwen`、`deepseek_ai`、`Kimi_Moonshot`、`MistralAI`、`huggingface`、`NVIDIAAI`。
- 关键负责人：`thsottiaux`、`sama`、`gdb`、`darioamodei`、`demishassabis`。
- 社区线索：`testingcatalog`。

先确保日报专用 Chrome 已启动，再用 OpenCLI 生成结构化候选，减少反复翻页与复制错误：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\open_authenticated_browser.ps1 -Url "https://x.com/"
py -3 .\scripts\collect_x_candidates.py --profile daily-briefing
```

读取 `data/opencli-x-candidates.json` 中每条候选的精确 status URL。该文件只属于发现层：不得把 `producer=opencli` 改成 `agent=codex`，不得直接复制成发布输入，也不得把“命令返回成功”视为账号、正文或发布时间已经核实。某账号采集失败时必须实际打开该账号补查，不能把失败记成已检查。

只把实际打开并核对过账号、status id、正文和发布时间的帖子写入 `data/codex-social-leads.json`。公司/产品账号必须确认身份，跳转后账号不一致时只能记录为已检查，不能收录。三个 `source_id` 都要填写 `lane_audits`：`status=checked`、新鲜 `checked_at`、完整账号白名单和 `qualifying_items`。找不到合格帖子时如实写 0；缺审计、账号未查全、数量不一致或伪造条目才会封锁发布。

### 2. 生成受约束工作包

```powershell
py -3 -m briefing prepare-agent --date today --quality 1080p --force
```

命令会生成：

- `review/agent-brief.json`：选题、approved claims、证据和规则。
- `review/draft-state.json`：不可篡改的冻结稿。
- `review/state.json`：Codex 可编辑稿。
- `review/agent-audit.template.json`：来源与文案审计模板。

### 3. 来源与文案复核

对 `agent-brief.json` 中每个故事：

1. 先找官方 X 原帖，再找官方网页；RSS、Google News、媒体摘要与镜像只在后台发现候选，不能出现在视频、字幕或 B 站文案。
2. 有官方 X 或第一方 URL 时必须打开；每个 claim 至少打开一个自己的 supporting URL。官方 X 首发可作为官方来源，关键负责人个人发言仍按一线消息标注，社区原帖始终标为传闻/风向。
3. 核对实体、动作、版本、数字、开放范围、价格和不确定性。
4. 只在 `review/state.json` 中重排或删减冻结稿已有的 approved copy blocks；不得自由改写新闻事实句，也不得改变故事、claim、角色或顺序。开场与收束可以重写，但不得引入新数字、型号或事实结论。
5. 新闻页按事实数量排版，不追求固定卡片数：优先保留版本/数字/变化/范围/对象/时间等具体信息，删除“是否重要要看……”“重点是接口、价格……”一类模板分析；只有 1 条合格事实时就放大这一条，不拿空话补到 3 条。
6. 已捕获原文、榜单或 X 截图时，必须保留对应证据页；截图中的账号、status id、来源和新闻序号要与稿件一致。
7. 视频文案只呈现新闻，不呈现采集、筛选、核验、截图绑定等工作流程；标题、口播、卡片和字幕都不得使用省略号。
8. 从模板复制生成 `review/agent-audit.json`，逐条填写实际打开的 URL；任一项无法核实时标为 rejected 并停止，不得强行 finalize。

#### 内容总编规则

- 每条新闻内部按“发生了什么 → 关键变化 → 对谁有什么具体用处或影响 → 必要限制”组织，但这些栏目名和制作判断不得出现在视频里。
- “有什么用”必须落到具体对象、功能入口、成本、时间、工作步骤或选择变化；没有 approved claim 支持时直接省去，不能用“值得关注、未来可期、推动行业发展”等空话补位。
- 测试、预览、灰度、计划、暗示和爆料不能改写成正式发布；开放权重不等于完全开源，可申请体验不等于所有人可用，厂商自测和榜单结论必须保留测试口径。
- 标题候选继续生成三类：单一事实、双新闻、共同价值。封面只表达一个由正文直接支持的结论；禁用“震撼、炸裂、王炸、杀疯了、颠覆、彻底取代、程序员失业”等无法由证据直接证明的措辞。
- 句子不得以“以及、包括、例如、但、因此、从而”等连接词突然结束；引号、括号、书名号必须闭合。截图正文不可读时，用经 approved claim 约束的中文事实卡辅助，不把整页小字当有效信息。
- 置顶评论问题应具体且可回答，优先采用两项新闻或“实际效果 / 使用门槛”的二选一，不再使用空泛的“你怎么看”。

然后执行：

```powershell
py -3 -m briefing finalize-agent --run-dir .\runs\YYYY-MM-DD
```

### 4. 渲染与视觉验片

```powershell
py -3 -m briefing render-agent --run-dir .\runs\YYYY-MM-DD --quality 1080p
```

该命令不会立即关闭质量门。它按每个 segment、每个 visual page 以及每条新闻的转场前后抽取原分辨率帧，并生成一张或多张 contact sheet：

- 查看 `cover.png`。
- 查看 `review/visual-qa/contact-sheet-*.png`。
- 对小字、证据页、X 原帖和疑似裁切位置，再查看对应 `frame-*.png` 原图。
- 对照 `script.json`、`subtitles.srt` 和 `audio-quality.json` 检查口播完整、字幕同步、音频无异常。

从模板复制生成 `review/visual-qa/visual-agent-audit.json`，`reviewed_frame_sha256` 必须逐项包含 `visual-qa-input.json` 中全部帧哈希；不能用一个自报数字代替。

### 5. 关闭质量门

```powershell
py -3 -m briefing complete-agent-run --run-dir .\runs\YYYY-MM-DD
py -3 -m briefing verify-run --run-dir .\runs\YYYY-MM-DD
py -3 -m briefing bilibili-preflight --run-dir .\runs\YYYY-MM-DD --tid 231
```

只有三个命令全部通过，且当前时间不晚于 07:50，才执行：

```powershell
py -3 -m briefing bilibili-publish --run-dir .\runs\YYYY-MM-DD --tid 231 --schedule-at 08:00 --execute
```

若缺凭据、时间已晚、定时发布参数无效或任一门禁失败，保留运行包并停止，不改成立即发布，不绕过门禁。

## 失败策略

- 24 小时内 0 条合格新闻、X 三赛道未完成当日审计、原文打不开、claim 无支撑、截图身份不符、Remotion 失败、TTS 降级、字幕/音频/画面或哈希合同失败：保留日志并报告具体文件，不发布。
- 合格新闻有多少播多少；不设固定条数，也不为数量降低证据标准。
- 不在日常任务中改代码自救；代码问题单独交给维护任务。
