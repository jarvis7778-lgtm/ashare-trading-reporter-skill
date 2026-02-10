# ashare-trading-reporter-skill

OpenClaw Skill for **A-share (China) intraday reporting + 0-token price alerts**.

This repo packages a reusable workflow for **any single stock symbol** (e.g. `sh600158`, `sz000001`):

- Trading-day **midday report** at **11:45** (data up to 11:30)
- Trading-day **close report** at **15:10**
- Optional best-effort **call auction snapshot** (around **09:26**) to enrich the “竞价/开盘” section
- Optional **0-token intraday trigger alerts** (script polling + send only on trigger)

> Notes
> - Data sources are free/public (Sina + optional Eastmoney). They are best-effort and may rate-limit.
> - The intraday trigger alerts are designed to be **0 token** (no LLM calls).


## Download

Go to Releases and download the `.skill` file:

- Release v0.1.0: https://github.com/jarvis7778-lgtm/ashare-trading-reporter-skill/releases/tag/v0.1.0

File you want:
- `ashare-trading-reporter.skill`


## Install (OpenClaw)

In OpenClaw Control UI (or CLI if you prefer), install/import the `.skill` file.

After installation, the skill name is:
- `ashare-trading-reporter`


## What’s inside

The skill folder contains:

- `SKILL.md` – the instructions the agent uses
- `scripts/`
  - `a_share_intraday_report.py` – generates 11:45 / 15:10 reports
  - `a_share_price_alerts.py` – 0-token trigger alerts (Telegram/Discord)
  - `a_share_auction_snapshot.py` – optional auction snapshot helper
- `references/`
  - `data-sources.md` – endpoints and notes


## Usage overview

### A) Two scheduled reports (11:45 + 15:10)

Recommended approach: create two OpenClaw **cron jobs** that run the report script and post the stdout.

Typical schedules (Asia/Shanghai):
- 11:45 (Mon–Fri): `45 11 * * 1-5`
- 15:10 (Mon–Fri): `10 15 * * 1-5`

Discord delivery reminder:
- Always use `channel:<id>` (threads are channels). Do **not** use bare ids.


### B) 0-token intraday trigger alerts

Recommended approach: system **crontab** runs the polling script every minute, but it only sends a message when a trigger fires.

Example (Telegram):

```cron
* * * * 1-5 cd /home/lyy/.openclaw/workspace && /usr/bin/python3 scripts/a_share_price_alerts.py \
  --symbol sh600158 \
  --channel telegram \
  --target <telegramChatId> \
  --state-dir /home/lyy/.openclaw/workspace/data/ashare/alerts \
  >/dev/null 2>&1
```

Default triggers in the script:
- touch/above 10.00
- touch/above 10.03
- break below 9.86
- VWAP cross

Each trigger fires at most once per trading day (de-dup by local state file).


## Roadmap / ideas

- Per-symbol config file (watch levels, triggers)
- Multiple symbols monitored by one cron
- Cleaner trading-calendar detection (holidays)


## Disclaimer

This project is for **data automation and information** only and does **not** constitute investment advice.
