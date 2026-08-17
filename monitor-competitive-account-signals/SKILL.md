---
name: monitor-competitive-account-signals
description: Use when salespeople monitor competitor pricing, product, or partnership changes, or key-account business, satisfaction, or needs from URLs and files, and need change alerts or next-best-action recommendations.
---

# 竞争与客户战情雷达

运行可审计的竞品与大客户变化监控；只产出内部销售预警和建议。

## 工作流

1. 首次使用必须运行交互式 `init`，再收集或评估资料。不要猜测销售身份、收件人、SMTP 环境变量映射、竞品/客户实体或来源；向导只记录环境变量名称，绝不索取密码值。
2. 对 URL 以及 CSV、XLSX、JSON、TXT、Markdown、PDF、DOCX，使用可用工具提取可核验片段，按 [观察契约](references/contracts.md#观察记录) 写入 JSONL。保留来源、时间、原文证据和稳定内容哈希。
3. 运行 `evaluate`。若观察记录无效，停止并修复资料；若为基线，明确说明只建立基线、不发送变化预警。
4. 为候选事件补充证据约束的个人销售行动。不要编造事实、折扣、价格、合同权限或客户承诺；采用 [领域规则](references/domain-rules.md)。
5. 以契约 JSON 草稿执行 `publish`、`dispatch`，并由外部计划任务调用 `digest`。如有排队或失败，准确报告，不宣称已送达。

## 命令速查

| 目的 | 命令 |
| --- | --- |
| 建虚拟环境 | `python scripts/bootstrap.py --project <目录>` |
| 首次交互配置 | `python scripts/monitor.py init --project <目录>` |
| 检查配置 | `python scripts/monitor.py validate --project <目录>` |
| 评估观察 | `python scripts/monitor.py evaluate --project <目录> --observations <observations.jsonl> --trigger scheduled` |
| 发布/发送 | `python scripts/monitor.py publish --project <目录> --draft <alerts.draft.json>`；`python scripts/monitor.py dispatch --project <目录>` |
| 低等级摘要 | `python scripts/monitor.py digest --project <目录> --date YYYY-MM-DD` |

合成观察示例：

```json
{"entity_id":"demo-rival","entity_name":"演示竞品","signal_type":"competitor.price","observed_at":"2026-08-17T09:00:00+08:00","effective_date":"2026-08-17","normalized_value":{"price":99},"source_uri":"https://example.invalid/pricing","evidence_text":"演示价格页面显示 99。","content_hash":"demo-price-99","source_quality":0.9,"extraction_certainty":0.9,"independent_sources":2,"impact":4,"urgency":4}
```

## 常见错误

- 不要把 404、下载失败或解析失败当作业务变化；它们只保留为来源失败记录。
- 不要以名称或自由文本去重；使用事件/证据 ID 和持久 SQLite 状态，冲突来源必须保留不确定性。
- 不要自动联系客户、修改商机、承诺费用、折扣或合同条款。

脚本：[`bootstrap.py`](scripts/bootstrap.py)、[`monitor.py`](scripts/monitor.py)。详细格式见 [契约](references/contracts.md) 与 [领域规则](references/domain-rules.md)。
