# ashare-trading-reporter-skill

[中文](#中文) | [English](#english)

---

## 中文

OpenClaw Skill：用于 **A 股（中国股市）盘中两次报告 + 0-token 盘中触发提醒**，可复用到任意个股（只需换 `symbol`）。

支持内容：
- **交易日午间报告**：11:45（数据截至 11:30）
- **交易日收盘详报**：15:10
- （可选）**集合竞价** best-effort 快照（建议 09:26 抓取，用于补强“竞价/开盘”段落）
- （可选）**盘中触发短提醒**：脚本轮询，只有触发才发消息，设计为 **0 token（不调用大模型）**

> 说明
> - 数据源使用免费公开接口（Sina + 可选 Eastmoney），best-effort，可能会限流。
> - 本项目仅用于数据自动化与信息展示，不构成投资建议。

### 下载

去 Releases 下载 `.skill`：
- v0.1.0：https://github.com/jarvis7778-lgtm/ashare-trading-reporter-skill/releases/tag/v0.1.0

需要的文件：
- `ashare-trading-reporter.skill`

### 安装（OpenClaw）

在 OpenClaw Control UI（或 CLI）里导入/安装 `.skill` 文件。

安装后 skill 名称：
- `ashare-trading-reporter`

### Skill 内包含哪些文件

- `SKILL.md`：供 Agent 触发与执行的说明
- `scripts/`
  - `a_share_intraday_report.py`：生成 11:45/15:10 报告
  - `a_share_price_alerts.py`：0-token 盘中触发提醒（Telegram/Discord）
  - `a_share_auction_snapshot.py`：（可选）集合竞价快照
- `references/`
  - `data-sources.md`：数据源说明

### 用法概览

#### A) 两次定时报告（11:45 + 15:10）

推荐方式：用 OpenClaw **cron jobs** 在固定时间执行脚本，并把 stdout 原样投递到目标渠道。

常用 schedule（Asia/Shanghai）：
- 11:45（周一到周五）：`45 11 * * 1-5`
- 15:10（周一到周五）：`10 15 * * 1-5`

Discord 投递硬规则：
- 必须用 `channel:<id>`（thread 也是 channel），不要用裸 id。

#### B) 0-token 盘中触发提醒

推荐方式：系统 **crontab** 每分钟跑一次脚本，但脚本只有触发才发消息（不会刷屏，也不吃 token）。

示例（Telegram）：

```cron
* * * * 1-5 cd "$OPENCLAW_WORKSPACE" && /usr/bin/python3 scripts/a_share_price_alerts.py \
  --symbol sh600158 \
  --config data/ashare/config/sh600158.json \
  --channel telegram \
  --target <telegramChatId> \
  --state-dir "$OPENCLAW_WORKSPACE/data/ashare/alerts" \
  >/dev/null 2>&1
```

触发条件不应写死：
- 建议先用 **TradingView/tvscreener** 做多日分析，生成 `data/ashare/config/<symbol>.json`（阻力/支撑、破位线、是否启用 VWAP cross）。
- 脚本按 config 执行；每个条件每日只提醒一次（本地 state 去重）。

每个条件每日只提醒一次（本地 state 去重）。

### Roadmap / 想法

- 支持 per-symbol 配置文件（关注价位、触发条件等）
- 一套 cron 同时监控多个 symbol
- 更完善的交易日历（节假日）判断

---

## English

OpenClaw Skill for **China A-share intraday reports + 0-token intraday trigger alerts**. Reusable for any stock (just change the `symbol`).

Features:
- Trading-day **midday report** at **11:45** (data up to 11:30)
- Trading-day **close report** at **15:10**
- (Optional) best-effort **call auction snapshot** (recommended capture around **09:26**) to enrich the “auction/open” section
- (Optional) **intraday trigger alerts**: polling script that sends messages **only on triggers**, designed to be **0 token (no LLM calls)**

> Notes
> - Data sources are free/public (Sina + optional Eastmoney). Best-effort and may rate-limit.
> - This project is for data automation and information only and does not constitute investment advice.

### Download

Download the `.skill` file from Releases:
- v0.1.0: https://github.com/jarvis7778-lgtm/ashare-trading-reporter-skill/releases/tag/v0.1.0

File you want:
- `ashare-trading-reporter.skill`

### Install (OpenClaw)

Import/install the `.skill` file in OpenClaw Control UI (or CLI).

Skill name after install:
- `ashare-trading-reporter`

### What’s inside

- `SKILL.md` – instructions for the agent
- `scripts/`
  - `a_share_intraday_report.py` – generates 11:45 / 15:10 reports
  - `a_share_price_alerts.py` – 0-token trigger alerts (Telegram/Discord)
  - `a_share_auction_snapshot.py` – optional auction snapshot helper
- `references/`
  - `data-sources.md` – endpoints and notes

### Usage overview

#### A) Two scheduled reports (11:45 + 15:10)

Recommended: create two OpenClaw **cron jobs** that run the report script and post stdout as-is.

Schedules (Asia/Shanghai):
- 11:45 (Mon–Fri): `45 11 * * 1-5`
- 15:10 (Mon–Fri): `10 15 * * 1-5`

Discord delivery hard rule:
- Always use `channel:<id>` for Discord threads/channels. Never use bare ids.

#### B) 0-token intraday trigger alerts

Recommended: system **crontab** runs the polling script every minute, but it sends messages only when a trigger fires.

Example (Telegram):

```cron
* * * * 1-5 cd "$OPENCLAW_WORKSPACE" && /usr/bin/python3 scripts/a_share_price_alerts.py \
  --symbol sh600158 \
  --config data/ashare/config/sh600158.json \
  --channel telegram \
  --target <telegramChatId> \
  --state-dir "$OPENCLAW_WORKSPACE/data/ashare/alerts" \
  >/dev/null 2>&1
```

Triggers should not be hard-coded:
- Use **TradingView/tvscreener** multi-day analysis to generate `data/ashare/config/<symbol>.json` (resistance/support levels, breakdown level, whether VWAP cross is useful).
- The polling script reads the config; each trigger fires at most once per trading day (de-dup via a local state file).

### Roadmap / ideas

- Per-symbol config file (watch levels, triggers)
- Multiple symbols monitored by one cron
- Cleaner trading-calendar detection (holidays)

---

## Disclaimer

For informational purposes only. Not investment advice.
