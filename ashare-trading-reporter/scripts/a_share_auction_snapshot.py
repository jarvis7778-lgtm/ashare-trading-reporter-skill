#!/usr/bin/env python3
"""Capture A-share call-auction snapshot around 09:25-09:29 and save to JSON.

This is a best-effort helper to support the 11:45 / 15:10 reports.

IMPORTANT:
- Free public quote endpoints often don't guarantee a true 09:25 final call-auction match.
- This snapshot should be scheduled to run at ~09:26 (Asia/Shanghai) on trading days.
- If the endpoint is already in continuous trading (>=09:30), the saved data is NOT auction anymore.

Output schema (consumed by a_share_intraday_report_v2.py):
{
  "date": "YYYY-MM-DD",
  "symbol": "sh600158",
  "auction_price": 9.91,
  "auction_amount": 12345678.0,
  "source": "sina_quote",
  "captured_at": "2026-02-10T09:26:10+08:00",
  "note": "best-effort"
}
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import requests


def _get(url: str, *, timeout: int = 10) -> str:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
    r.raise_for_status()
    return r.text


def to_num(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def fetch_sina(symbol: str) -> dict:
    text = _get(f"https://hq.sinajs.cn/list={symbol}")
    m = re.search(r'"(.*)"', text)
    if not m:
        raise RuntimeError(f"Unexpected quote payload: {text[:200]}")
    arr = m.group(1).split(",")
    if len(arr) < 32:
        raise RuntimeError(f"Unexpected quote fields={len(arr)}: {text[:200]}")

    # Fields: name, open, preclose, price, high, low, ... vol, amount, ..., date, time
    name = arr[0]
    price = to_num(arr[3])
    amount = to_num(arr[9])
    d = arr[30]
    t = arr[31]
    dt = None
    try:
        if d and t:
            dt = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S")
    except Exception:
        dt = None

    return {
        "name": name,
        "price": price,
        "amount": amount,
        "quote_dt": dt.isoformat() if dt else None,
        "raw": text.strip(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="Sina symbol like sh600158")
    ap.add_argument("--date", default="", help="YYYY-MM-DD, default today")
    ap.add_argument("--outdir", default="data/ashare/auction", help="Output directory")
    args = ap.parse_args()

    d = date.today() if not args.date else datetime.strptime(args.date, "%Y-%m-%d").date()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    snap = fetch_sina(args.symbol)
    now = datetime.now().astimezone()

    payload = {
        "date": d.isoformat(),
        "symbol": args.symbol,
        "auction_price": snap.get("price"),
        "auction_amount": snap.get("amount"),
        "source": "sina_quote",
        "captured_at": now.isoformat(),
        "note": "best-effort; schedule this around 09:25-09:29",
    }

    fp = outdir / f"{d.isoformat()}_{args.symbol}.json"
    fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print a one-liner for cron logs
    print(f"saved {fp} price={payload['auction_price']} amt={payload['auction_amount']}")


if __name__ == "__main__":
    main()
