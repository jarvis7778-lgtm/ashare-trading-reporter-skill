---
name: ashare-trading-reporter
description: Generate reusable A-share (China stock market) intraday reports and alerts for any symbol. Use when the user wants two scheduled trading-day reports (11:45 midday + 15:10 close) with intraday structure (open gap, morning/afternoon/close segments, VWAP, key levels) and optional call auction snapshot (09:25) plus 0-token intraday trigger alerts sent to Telegram/Discord.
---

# A-share trading reporter (reports + alerts)

Use the bundled scripts in `scripts/` to:

1) Generate two **trading-day** reports for a given stock:
- **11:45** midday report (up to 11:30)
- **15:10** close report

2) (Optional) Capture a best-effort **call auction** snapshot around **09:26**.

3) Configure a **0-token** intraday trigger alert loop (system crontab) that polls quotes and only sends a short message when triggers fire.

## Inputs to ask the user (minimal)

- Stock symbol (Sina format): e.g. `sh600158` / `sz000001`
- Watch levels: e.g. `9.5/10.1/9.0/8.5`
- Intraday alert triggers: **configurable per symbol** (generated once, then used by the 0-token alert script)
- Delivery targets:
  - Reports → Discord thread: `channel:<id>`
  - Alerts → Telegram chatId (or Discord if requested)

## Generate reports (one-off / ad-hoc)

Midday:

```bash
cd "$OPENCLAW_WORKSPACE"   # or your OpenClaw workspace
python3 scripts/a_share_intraday_report.py --symbol sh600158 --mode mid --date YYYY-MM-DD
```

Close:

```bash
cd "$OPENCLAW_WORKSPACE"   # or your OpenClaw workspace
python3 scripts/a_share_intraday_report.py --symbol sh600158 --mode close --date YYYY-MM-DD
```

Notes:
- This script is dependency-light and uses free endpoints.
- If call auction data is not available, use **open gap** wording (do not claim exact 09:25 match).

## Optional: call auction snapshot (best-effort)

```bash
cd "$OPENCLAW_WORKSPACE"   # or your OpenClaw workspace
python3 scripts/a_share_auction_snapshot.py --symbol sh600158
```

## Configure scheduled reports (OpenClaw cron)

Create/update two OpenClaw cron jobs (sessionTarget=isolated agentTurn) that:
- `exec` the report script
- send stdout **as-is**

Hard rule (Discord delivery):
- Use `channel:<id>` for Discord threads/channels. Never use bare ids.

Suggested schedules (Asia/Shanghai):
- 11:45 trading days: `45 11 * * 1-5`
- 15:10 trading days: `10 15 * * 1-5`

## Configure 0-token intraday trigger alerts (system crontab)

Use the script `scripts/a_share_price_alerts.py`.

### Step 1) Generate per-symbol trigger config (once)

To avoid hard-coded triggers, generate a per-symbol config **once per stock** (or whenever you want to update it). Suggested approach:
- Use **tvscreener / TradingView** multi-day analysis to propose:
  - key resistance/support levels (recent swing high/low)
  - a downside breakdown level
  - whether VWAP cross alerts are useful for this symbol

Save the result as a JSON file, for example:

`data/ashare/config/sh600158.json`

Schema:
```json
{
  "levels_up": [10.0, 10.03],
  "breakdown": 9.86,
  "vwap_cross": true
}
```

### Step 2) Run the 0-token alert loop

Example (Telegram alerts):

```cron
* * * * 1-5 cd "$OPENCLAW_WORKSPACE" && /usr/bin/python3 scripts/a_share_price_alerts.py \
  --symbol sh600158 \
  --config data/ashare/config/sh600158.json \
  --channel telegram \
  --target <telegramChatId> \
  --state-dir "$OPENCLAW_WORKSPACE/data/ashare/alerts" \
  >/dev/null 2>&1
```

Properties:
- 0-token (no LLM). Uses OpenClaw CLI `openclaw message send`.
- De-dup: each trigger fires at most once per trading day.
- Script internally skips non-trading time windows.

## References

- Data sources: `references/data-sources.md`
