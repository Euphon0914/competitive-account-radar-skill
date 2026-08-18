# 竞争与客户战情雷达 Skill

`monitor-competitive-account-signals` 是一个面向个人销售的竞争与客户战情雷达。它将公开网页和本地资料中零散的外部信号，转为带证据、可去重、可派送的内部预警，以及可执行的下一步销售建议。

## 它解决什么问题

销售人员往往需要手动关注竞品调价、产品更新、合作新闻，以及重点客户的业务、满意度与需求变化。信息分散、重复出现且难以判断优先级时，真正重要的变化容易被错过。

本 Skill 将这两类工作合并为两个协作引擎：

- **动态感知引擎**：标准化竞品与大客户信号，并保留来源、时间和原文证据。
- **策略推荐引擎**：基于已知证据生成个人销售行动，不编造事实，也不会自行承诺价格、折扣或合同条件。

## 可以实现的效果

| 能力 | 结果 |
| --- | --- |
| 多来源信息整理 | 将 URL，以及 CSV、XLSX、JSON、TXT、Markdown、PDF、DOCX 中提取的证据统一为观察记录。 |
| 六类业务信号识别 | 追踪竞品价格、产品组合、合作动向，以及大客户业务、满意度和需求变化。 |
| 可信度与优先级判断 | 按来源质量、提取确定性、时效性和独立佐证评分，并结合影响与紧迫度确定等级。 |
| 可追溯变化预警 | 使用稳定事件 ID、证据 ID 和 SQLite 状态去重；只在等级升级、关键值变化或实质新证据出现时再次通知。 |
| 销售行动建议 | 每项告警可包含影响判断、24 小时/7 天行动、三个发现式问题、沟通话术、假设与升级条件。 |
| 分级内部派送 | 高/中等级立即入队发送；低等级汇入本地时区 17:30 的每日摘要；失败邮件会按计划重试。 |

## 3 步开始使用

> **先分清两类内容：**标注为 `powershell` 的代码块要在 Windows 的 **PowerShell 或 Windows Terminal** 中运行；标注为 `text` 的“提示词”要复制到 **Codex 聊天输入框**中发送，不能粘贴到 PowerShell。`<skill目录>` 和 `<监控项目目录>` 是占位符，必须替换成自己的真实文件夹路径，尖括号本身不要输入。

### 1. 安装运行环境

将 `monitor-competitive-account-signals` 复制到 Codex Skills 目录，然后在独立的监控项目中创建隔离虚拟环境：

```powershell
python <skill目录>/scripts/bootstrap.py --project <监控项目目录>
```

Windows 示例：按 `Win + X`，选择“终端”或“PowerShell”，将路径替换为你的实际位置后执行：

```powershell
$skill = "C:\Codex\skills\monitor-competitive-account-signals"
$project = "C:\SalesRadar\my-monitoring-project"
python "$skill\scripts\bootstrap.py" --project $project
```

### 2. 完成交互式监控档案

首次使用必须先运行初始化向导：

```powershell
python <skill目录>/scripts/monitor.py init --project <监控项目目录>
```

向导会询问销售姓名、**内部预警收件邮箱**、时区、SMTP 环境变量名称、竞品、大客户及信息来源。只有完成必填项才会写入 `monitoring.yaml`；不会猜测这些信息，也不会要求输入 SMTP 密码。

### 3. 评估并派送变化

先使用网页、文档、表格或 PDF 工具，将材料提取为符合[观察契约](monitor-competitive-account-signals/references/contracts.md#观察记录)的 `observations.jsonl`，再运行：

```powershell
python <skill目录>/scripts/monitor.py validate --project <监控项目目录>
python <skill目录>/scripts/monitor.py evaluate --project <监控项目目录> --observations <observations.jsonl> --trigger scheduled
python <skill目录>/scripts/monitor.py publish --project <监控项目目录> --draft <alerts.draft.json>
python <skill目录>/scripts/monitor.py dispatch --project <监控项目目录>
```

第一次成功评估只建立基线，不会发送“变化”告警。后续运行才会识别相对基线的有效变化。

## 直接复制给 Codex 的提示词

无需记住命令；将以下任一提示词复制到 **Codex 聊天输入框**后发送，**不要在 PowerShell 中运行**。首次提示会先触发必要的交互式问题，不要在对话中提供 SMTP 密码。

### 首次建立监控

```text
使用 $monitor-competitive-account-signals 帮我创建竞争与大客户监控档案。先通过交互式对话收集我的姓名、内部预警邮箱、时区、竞品、大客户和资料来源；不要猜测这些信息，也不要要求 SMTP 密码。完成后创建隔离运行环境，并告诉我如何进行第一次基线扫描。
```

### 分析一批最新资料

```text
使用 $monitor-competitive-account-signals 分析我提供的 URL 和本地资料，识别竞品价格、产品组合、合作，以及大客户业务、满意度和需求的变化。提取可核验证据后运行评估；如没有有效变化或仍在建立基线，请明确说明。对候选事件给出可追溯的个人销售行动建议，但不得编造事实、承诺折扣或自动联系客户。
```

### 处理当天预警

```text
使用 $monitor-competitive-account-signals 检查当前监控项目的最新候选事件。仅在草稿包含完整证据、影响判断、24 小时/7 天行动、三个发现式问题、沟通话术、假设和升级条件时才发布；随后报告哪些内部邮件已发送、哪些仍在队列或失败重试。
```

### 配置定时运行

```text
使用 $monitor-competitive-account-signals 为我的监控项目设计外部自动化：每 60 分钟提取资料、评估、发布和派送；每天在我的配置时区 17:30 发送低等级摘要。不要让 Skill 常驻，不要连接 CRM、Slack、企业微信，也不要把 SMTP 密码写入配置或日志。
```

## 运行后会得到什么

每次成功发布都会生成共享事件 ID 的 `alerts.json` 与 `alerts.md`。一条合格告警会清楚回答：

- **发生了什么**：信号类型、变化内容、来源和可核验的证据片段。
- **为什么重要**：可信度、影响、紧迫度和告警等级。
- **应该怎么做**：未来 24 小时和 7 天内的建议行动。
- **如何推进对话**：恰好三个发现式问题与一段不含未经授权承诺的沟通话术。
- **何时升级处理**：显式列出假设、升级条件，以及需要审批的商业承诺。

高、中的候选事件会形成即时内部邮件；低等级候选事件等待每日摘要。邮件无法发送时，系统保留队列并按 1、5、30、120 分钟的间隔重试，最多五次。

## 自动化运行建议

Skill 不常驻运行。建议由任务计划程序、CI 或其他外部自动化每 60 分钟执行一次资料提取、`evaluate`、`publish` 和 `dispatch`，并在销售人员配置的本地时区每天 17:30 调用：

```powershell
python <skill目录>/scripts/monitor.py digest --project <监控项目目录> --date YYYY-MM-DD
```

SMTP 凭据只从环境变量读取：`CI_SMTP_HOST`、`CI_SMTP_PORT`、`CI_SMTP_USERNAME`、`CI_SMTP_PASSWORD`、`CI_SMTP_FROM`。可在监控档案中改用其他环境变量名称，但绝不应把密码写入 YAML、日志或 Git。

## 隐私、授权与限制

- 仅向配置的销售人员本人发送内部预警；不会自动联系客户。
- 不会修改 CRM、商机或合同；第一版不接入 CRM、Slack、企业微信。
- 不会承诺折扣、费用减免或合同条款；此类措辞必须有明确授权。
- 404、URL 失效和文件解析失败只保留为来源失败，不会被当成业务变化。
- 本仓库的 `.gitignore` 排除监控档案、SQLite 状态、运行产物与 `.env`。若监控项目本身也受 Git 管理，请在该项目单独排除这些敏感运行数据，不要提交真实客户、邮箱、价格或竞争情报。

详细的数据格式、输出字段与评分规则请参阅 [运行契约](monitor-competitive-account-signals/references/contracts.md) 和 [领域规则](monitor-competitive-account-signals/references/domain-rules.md)。

## License

[Apache-2.0](LICENSE)。
