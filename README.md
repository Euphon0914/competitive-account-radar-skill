# Competitive Account Radar Skill

面向个人销售的竞争对手与大客户动态监控 Skill。它将 URL 和本地资料中的外部信号规范化，识别价格、产品、合作、客户业务、满意度和需求变化，并生成可追溯的销售预警与行动建议。

## 安装

将 `monitor-competitive-account-signals` 目录复制到 Codex Skills 目录，然后安装唯一的运行依赖：

```powershell
python monitor-competitive-account-signals/scripts/bootstrap.py --project <监控项目目录>
```

首次使用时运行交互式初始化向导。向导会询问销售姓名、预警收件邮箱、时区、SMTP 发件配置、竞争对手、大客户和资料来源；未完成必填信息前不会开始采集或发送邮件。

```powershell
python monitor-competitive-account-signals/scripts/monitor.py init --project <监控项目目录>
```

## 安全边界

- SMTP 密码只从环境变量读取，不写入 YAML、SQLite、日志或 Git。
- 真实客户资料、监控配置、运行数据库和生成的预警默认被 `.gitignore` 排除。
- Skill 只向销售本人发送内部预警，不会自动联系客户、修改商机或承诺折扣。

详细工作流、数据契约和运行命令见 Skill 内的 `SKILL.md` 与 `references/`。

## License

Apache License 2.0。见 [LICENSE](LICENSE)。

