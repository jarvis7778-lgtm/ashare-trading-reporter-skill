#!/usr/bin/env python3
"""Generate a simple, newbie-friendly intraday alert config for an A-share symbol.

Goal
- Produce a per-symbol JSON config consumed by `a_share_price_alerts.py`.
- The config should adapt as price/structure changes, so you can re-run it daily.

Design principles (simple, explainable)
- Pick 2 upside "watch" levels (where people tend to react):
  1) Nearest round-number above current price (e.g. 10.00 / 20.00)
  2) Recent N-day high (default: 20 trading days)
- Pick 1 downside "risk" level:
  - Recent N-day low (default: 20 trading days)
- VWAP cross alert:
  - Enabled by default (can be disabled).

Data source
- Eastmoney daily kline (public): push2his.eastmoney.com

Usage
  python3 scripts/a_share_generate_alert_config.py \
    --symbol sh600158 \
    --out data/ashare/config/sh600158.json \
    --days 20 --breakdown-days 5

Output schema
{
  "levels_up": [10.0, 10.03],
  "breakdown": 9.86,
  "vwap_cross": true,
  "meta": { ... }
}
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


@dataclass
class DailyBar:
    date: str
    open: float
    close: float
    high: float
    low: float


def symbol_to_secid(symbol: str) -> str:
    # symbol like sh600158 / sz000001
    symbol = symbol.lower().strip()
    if symbol.startswith("sh"):
        return f"1.{symbol[2:]}"
    if symbol.startswith("sz"):
        return f"0.{symbol[2:]}"
    raise ValueError("symbol must start with sh or sz")


def fetch_daily_kline(symbol: str, limit: int = 60) -> List[DailyBar]:
    """Fetch daily bars.

    Primary: Eastmoney daily kline.
    Fallback: Sina kline with scale=240 (daily) which is usually very stable.
    """

    # 1) Eastmoney (best-effort)
    try:
        secid = symbol_to_secid(symbol)
        url = (
            "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&klt=101&fqt=1&end=20500101&lmt={limit}"
            "&ut=fa5fd1943c7b386f172d6893dbfba10b"
            "&fields1=f1,f2,f3,f4,f5"
            "&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        )
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        js = r.json()
        data = (js or {}).get("data") or {}
        kl = data.get("klines") or []
        out: List[DailyBar] = []
        for line in kl:
            parts = str(line).split(",")
            if len(parts) < 5:
                continue
            d, o, c, h, l = parts[0], parts[1], parts[2], parts[3], parts[4]
            out.append(DailyBar(date=d, open=float(o), close=float(c), high=float(h), low=float(l)))
        if len(out) >= 5:
            return out
    except Exception:
        pass

    # 2) Sina fallback (stable)
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/"
        "CN_MarketDataService.getKLineData"
        f"?symbol={symbol}&scale=240&ma=no&datalen={limit}"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    js = r.json()
    out: List[DailyBar] = []
    for row in js:
        # day: 'YYYY-MM-DD'
        d = str(row.get("day"))
        o = float(row.get("open"))
        h = float(row.get("high"))
        l = float(row.get("low"))
        c = float(row.get("close"))
        out.append(DailyBar(date=d, open=o, close=c, high=h, low=l))
    return out


def round_step(price: float) -> float:
    # simple tick for round numbers: 0.1 below 10, 0.5 below 50, 1 below 200, 5 above
    if price < 10:
        return 0.1
    if price < 50:
        return 0.5
    if price < 200:
        return 1.0
    return 5.0


def next_round_above(price: float) -> float:
    step = round_step(price)
    return math.ceil(price / step) * step


def uniq_sorted(levels: List[float], *, ndigits: int = 2) -> List[float]:
    seen = set()
    out = []
    for x in sorted(levels):
        v = round(float(x), ndigits)
        if v <= 0:
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="e.g. sh600158")
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--days", type=int, default=20, help="lookback trading days for upside level")
    ap.add_argument("--breakdown-days", type=int, default=5, help="lookback trading days for downside breakdown level")
    ap.add_argument("--vwap-cross", default="true", choices=["true", "false"], help="enable VWAP cross trigger")
    args = ap.parse_args()

    bars = fetch_daily_kline(args.symbol, limit=max(args.days, 20) + 5)
    if len(bars) < 5:
        raise SystemExit("not enough daily bars")

    recent_up = bars[-args.days :]
    recent_dn = bars[-args.breakdown_days :]
    last = bars[-1]

    hi = max(b.high for b in recent_up)
    lo = min(b.low for b in recent_dn)

    # Upside levels: (1) round above last close (2) recent high
    lv1 = next_round_above(last.close)
    lv2 = hi

    levels_up = uniq_sorted([lv1, lv2])
    breakdown = round(lo, 2)

    cfg: Dict[str, Any] = {
        "levels_up": levels_up,
        "breakdown": breakdown,
        "vwap_cross": args.vwap_cross == "true",
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "symbol": args.symbol,
            "lookback_days_up": args.days,
        "lookback_days_down": args.breakdown_days,
            "last_close": round(last.close, 2),
            "recent_high": round(hi, 2),
            "recent_low": round(lo, 2),
            "method": "round_above_last_close + recent_high(N_up) + recent_low(N_down)",
            "data": "eastmoney daily kline (klt=101,fqt=1)",
        },
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
