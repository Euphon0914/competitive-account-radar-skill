# 运行契约

## 监控档案

`monitoring.yaml` 是版本 `1` 的映射：`salesperson.name`、`salesperson.alert_email`、IANA `timezone`、`smtp.env` 的 `host`/`port`/`username`/`password`/`from` 环境变量名称、字符串列表 `competitors`/`accounts`/`sources`，以及固定策略 `scan_interval_minutes: 60` 和 `daily_digest_time: "17:30"`。仅向 `alert_email` 内部发送。

## 观察记录

每行 JSON 必含 `entity_id`、`entity_name`、六类之一的 `signal_type`、带时区的 `observed_at`、`effective_date`、`normalized_value`、`source_uri`、`evidence_text`、稳定 `content_hash`、0–1 的 `source_quality`/`extraction_certainty`、正整数 `independent_sources` 和 1–5 的 `impact`/`urgency`。无效行必须阻止评估。

## 评估与输出

`evaluate` 输出 `run_id`、`baseline`、`candidates`、`suppressed` 与 `recorded_only`。首次成功运行是基线。`publish` 的草稿及 `alerts.json`/`alerts.md` 使用同一事件 ID；每个告警必须有证据、前后值、影响、24 小时/7 天行动、发现式问题、沟通话术、假设和升级条件。状态写入项目 `.competitive-radar/` 的 SQLite，不写入 Skill 或 Git 仓库。

## SMTP

运行时只读取 `CI_SMTP_HOST`、`CI_SMTP_PORT`、`CI_SMTP_USERNAME`、`CI_SMTP_PASSWORD`、`CI_SMTP_FROM`（或档案中对应的变量名）。凭据不得进入 YAML、输出或日志。高/中等级即时入队，低等级进入 17:30 摘要；失败按 1、5、30、120 分钟重试，最多五次。
