# Codex 每日早报自动化运行手册

## 职责边界

Agent 负责需要内容判断力的工作：X/社区线索核对、逐条打开原文、第一方来源补查、整期取舍、开场与收束文案优化，以及成片抽帧视觉验片。

本地代码负责不可绕过的硬边界：故事全集、`FactClaim` 白名单、来源 URL、社交账号白名单、截图身份、稿件指纹契约、音频响度门、Editorial Newsroom Remotion 渲染、媒体哈希与 Bilibili 两阶段防重上传。

## 每日标准顺序

### 1. 采集 X 三赛道候选（可选）

若启用 `sources.yaml` 中的三个 `agent_social` 源，先启动项目专用登录浏览器并拉取候选原帖：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\open_authenticated_browser.ps1 -Url "https://x.com/"
py -3 -m briefing doctor --collect-x --limit 8
```

逐条核实 `data/opencli-x-candidates.json` 中的账号、status ID、正文与发布时间后，将当日完整 `lane_audits` 写入 `data/codex-social-leads.json`。若仅运行公开网页版，直接使用 `--config .\sources.public-web.json`。

### 2. 生成受约束 Agent 工作包

```powershell
py -3 -m briefing prepare --mode agent --date today --quality 1080p --force
```

生成产物：
- `review/agent-brief.json`：选题、已核验 `FactClaim`、证据链接与边界规则
- `review/draft-state.json`：不可篡改的冻结基线稿
- `review/state.json`：可编辑审稿状态
- `review/agent-audit.template.json`：来源与文案审计模板

### 3. 来源与文案复核并冻结定稿

完成 `review/state.json` 调整并填写 `review/agent-audit.json` 后执行冻结校验：

```powershell
py -3 -m briefing review --finalize --run-dir .\runs\YYYY-MM-DD
```

核心纪律：
- 只在冻结 `FactClaim` 范围内组织事实，不引入未核验的数字、型号或发布结论。
- 测试、预览、灰度、开放权重与正式开源严格区分；一线人员动态与社区传闻必须保留免责声明与原帖截图。
- 标题、口播、卡片和字幕均不得出现省略号（`…` / `...`）或未闭合括号。

### 4. 渲染与视觉验片

```powershell
py -3 -m briefing render --run-dir .\runs\YYYY-MM-DD --quality 1080p --visual-qa --require-clean
```

检查 `cover.png`、`review/visual-qa/contact-sheet-*.png`、`script.json`、`subtitles.srt` 与 `audio-quality.json`，将全部抽帧 SHA-256 写入 `review/visual-qa/visual-agent-audit.json`。

### 5. 关闭视觉门禁并执行发布预检

```powershell
py -3 -m briefing verify --complete-visual-qa --run-dir .\runs\YYYY-MM-DD
py -3 -m briefing publish --run-dir .\runs\YYYY-MM-DD --tid 231
```

全部通过且在授权发布时间窗口内时，执行正式上传：

```powershell
py -3 -m briefing publish --run-dir .\runs\YYYY-MM-DD --tid 231 --schedule-at 08:00 --execute
```

可通过只读状态机随时查询当前进度与下一步安全命令：

```powershell
py -3 -m briefing doctor --status .\runs\YYYY-MM-DD
```
