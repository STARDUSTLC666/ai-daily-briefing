# AI 早报本地审稿 App

这个入口只在本机运行，不公开到 GitHub Pages 或公网。

## 启动

双击仓库根目录：

```text
AI日报审稿台.cmd
```

它会自动：

1. 使用当天日期定位 `runs\YYYY-MM-DD`。
2. 如果还没有审稿包，先执行 `prepare-review`。
3. 在 `127.0.0.1:8765` 启动本地审稿服务。
4. 用 Edge App 模式打开审稿台窗口。

## 停止

双击：

```text
AI日报审稿台-停止服务.cmd
```

或手动执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\stop_review_app.ps1
```

## 注意

- 默认只监听 `127.0.0.1`，只有当前电脑能访问。
- 不要把审稿台部署到 GitHub Pages；未发布新闻稿和截图应留在本地。
- 审稿完成后，网页里的“保存并生成最终稿”会写入 `review\final-script.json`。
- 06:00 自动流程会在不覆盖人工稿的前提下追加过去 6 小时的新内容，并生成成品。
