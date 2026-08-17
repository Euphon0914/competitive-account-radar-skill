# 竞争与客户战情雷达 Skill

`monitor-competitive-account-signals` 为个人销售提供双引擎：从公开 URL 与本地资料识别竞品价格、产品组合、合作，以及大客户业务、满意度、需求信号；再把有证据的变化转为内部预警和可执行的下一步。

## 安装与首次使用

复制 `monitor-competitive-account-signals` 到 Codex Skills 目录，在监控项目中运行：

```powershell
python <skill目录>/scripts/bootstrap.py --project <监控项目目录>
python <skill目录>/scripts/monitor.py init --project <监控项目目录>
```

`init` 会交互询问销售姓名、内部收件邮箱、时区、SMTP 环境变量名称、竞品、大客户和来源。未完成对话不会收集、评估或发信；不要把 SMTP 密码输入向导。

支持 URL 及 CSV、XLSX、JSON、TXT、Markdown、PDF、DOCX（先提取为观察 JSONL）。常用命令：

```powershell
python <skill目录>/scripts/monitor.py evaluate --project <目录> --observations <observations.jsonl> --trigger scheduled
python <skill目录>/scripts/monitor.py publish --project <目录> --draft <alerts.draft.json>
python <skill目录>/scripts/monitor.py dispatch --project <目录>
python <skill目录>/scripts/monitor.py digest --project <目录> --date 2026-08-17
```

由外部任务每 60 分钟运行评估/派送，并在本地时区 17:30 调用摘要。SMTP 只从 `CI_SMTP_HOST`、`CI_SMTP_PORT`、`CI_SMTP_USERNAME`、`CI_SMTP_PASSWORD`、`CI_SMTP_FROM` 环境变量读取。

## 隐私与限制

本仓库的 `.gitignore` 排除监控档案、SQLite 状态、运行产物和 `.env`；若监控项目本身也是 Git 仓库，请在该项目单独排除这些文件，且不要提交真实客户、邮箱、价格或竞争情报。系统仅给销售本人发内部通知，不连接 CRM/Slack/企业微信，不联系客户，不修改合同或商机，也不自动承诺折扣。它不会自己常驻、抓取受限来源或替代人工验证。

## License

[Apache-2.0](LICENSE)。
