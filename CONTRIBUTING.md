# Contributing

感谢你改进 AI Daily Briefing。项目优先接受能提高来源可靠性、内容可核查性、渲染稳定性和公开复用能力的修改。

## 开始之前

1. 从 `main` 创建独立分支。
2. 不要提交 Token、Cookie、浏览器配置、账号 ID、绝对路径或真实投稿后台截图。
3. 新来源必须说明来源类型、发布时间字段和失败时的降级行为。
4. 修改事实选择、稿件生成或发布门禁时，必须补充回归测试。

## 本地检查

```powershell
py -3 -m unittest discover -s tests -v
npm --prefix .\remotion test
npm --prefix .\remotion run typecheck
```

涉及画面布局的修改还应完成一次实际渲染，并检查封面、字幕、证据弹窗和 contact sheet。

## Pull Request

PR 描述应包含：

- 要解决的问题和影响范围。
- 新旧行为差异。
- 已运行的测试或渲染命令。
- 失败模式、兼容性和回滚方式。

请保持提交聚焦，不要把本地运行产物或无关格式化混入功能修改。
