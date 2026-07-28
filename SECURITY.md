# Security Policy

## 支持范围

安全修复优先覆盖当前 `main` 分支。旧提交和个人修改版不保证获得回溯修复。

## 报告问题

请使用 GitHub Private Vulnerability Reporting 提交安全问题。不要在公开 Issue、日志或截图中提供：

- Bilibili Open Platform 凭据。
- Cookie、浏览器登录态或用户数据目录。
- Webhook 地址、访问令牌或账号 ID。
- 可识别个人身份的本机路径和运行记录。

报告中请说明受影响版本、复现条件、预期影响和建议修复方向。维护者确认问题前，请避免公开可直接利用的细节。

## 凭据处理

真实发布凭据应通过 `scripts/configure_bilibili.ps1` 加密保存到当前 Windows 用户，或在当前进程中通过环境变量提供。仓库不读取或提交普通文本凭据文件。
