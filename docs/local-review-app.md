# 本地审稿工作台（Review Workbench）

本地审稿工作台默认只监听回环地址 `127.0.0.1:8765`，不会将未发布的草稿、内部备注或原始截图暴露到公网。

## 启动流程

1. **生成今日审稿工作区**：

```powershell
py -3 -m briefing prepare --mode review --date today
```

2. **启动本地 Web 审稿台**：

```powershell
py -3 -m briefing review --run-dir .\runs\YYYY-MM-DD
```

浏览器打开 `http://127.0.0.1:8765` 即可进入工作台。

## 工作台功能

- **多段落编排与检索**：按关键词检索段落，按“全部 / 已启用 / 头条 / 快讯”筛选，支持段落排序与启用开关。
- **实时口播与文案卫生检查**：实时统计字数与预计口播时长，自动提示省略号（`…`/`...`）、未闭合括号、标题党词汇、裸 URL 及缺失的一线/传闻免责声明。
- **事实卡片与证据画廊**：增删重排画面要点卡片；预览、上传或启用证据截图（`required` 必需证据截图由系统自动锁定保护，保存时即使前端遗漏也会从 `manifest.json` 强制回填）。
- **封面文案工作室（Cover Studio）**：在弹窗中直接编辑并预览 Bilibili 封面主标题、副标题、角标、看点列表与实体标签。
- **冻结事实溯源检查器**：随时对照 `manifest.json` 中经核验的 `FactClaim` 列表与原始一手链接。
- **外观自适应**：支持跟随系统、浅色纸感与柔和深色石板（Soft Slate）三种模式切换。

## 定稿与渲染

点击右上角 **保存并生成定稿**（或快捷键 `Ctrl+S` / `Cmd+S`）后，系统会原子写入 `review/state.json`、`review/final-script.json` 与 `review/render-required.json`。随后执行：

```powershell
py -3 -m briefing render --run-dir .\runs\YYYY-MM-DD --quality 1080p
```
