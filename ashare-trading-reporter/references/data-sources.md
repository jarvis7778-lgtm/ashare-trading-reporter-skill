# Data sources (free)

This skill uses free/public quote endpoints. They are **best-effort** and may rate-limit.

## Sina realtime quote

- Endpoint: `https://hq.sinajs.cn/list=<symbol>`
- Examples:
  - Stock: `https://hq.sinajs.cn/list=sh600158`
  - Index: `https://hq.sinajs.cn/list=sh000001`
- Notes:
  - Provides: name, open, preclose, last, high, low, volume, amount, timestamp.

## Sina Kline (1m/5m/etc)

- Endpoint: `https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData`
- Params:
  - `symbol=<symbol>`
  - `scale=<minutes>` (1/5/15/30/60)
  - `datalen=<n>`

## Eastmoney call auction / pre-open ticks (optional)

- Endpoint (ticks/details):
  - `https://push2.eastmoney.com/api/qt/stock/details/get?secid=<market>.<code>&pos=0`
- Example:
  - `secid=1.600158` (SH)
- Notes:
  - Often includes records like `09:25:00,...`.
  - Free sources are not guaranteed to provide the exact final auction match after the fact.
  - For best-effort stability, capture a snapshot around **09:26** and reuse it in later reports.
