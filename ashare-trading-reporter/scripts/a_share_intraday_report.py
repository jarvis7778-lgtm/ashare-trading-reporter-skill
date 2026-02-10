#!/usr/bin/env python3
"""Generate an intraday A-share report using Sina quote endpoints.

Data sources (public):
- Realtime quote: https://hq.sinajs.cn/list=sh600158
- Kline (5m/1m): https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData

This script is intentionally dependency-light (no pandas).
"""

from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from datetime import datetime, date, time
from typing import Any, Dict, List, Optional, Tuple

import requests


@dataclass
class Ohlc:
    open: float
    high: float
    low: float
    close: float
    vol: float
    amt: float

    @property
    def vwap(self) -> Optional[float]:
        return (self.amt / self.vol) if self.vol else None


def _get(url: str, *, timeout: int = 10, headers: Optional[dict] = None) -> str:
    r = requests.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    return r.text


def fetch_realtime_quote(symbol: str) -> Dict[str, Any]:
    """Fetch realtime quote from Sina hq endpoint.

    symbol: like 'sh600158'
    """
    text = _get(
        f"https://hq.sinajs.cn/list={symbol}",
        headers={"Referer": "https://finance.sina.com.cn"},
    )
    m = re.search(r'"(.*)"', text)
    if not m:
        raise RuntimeError(f"Unexpected quote payload: {text[:200]}")
    arr = m.group(1).split(",")
    if len(arr) < 32:
        raise RuntimeError(f"Unexpected quote fields={len(arr)}: {text[:200]}")

    def f(i: int) -> float:
        try:
            return float(arr[i]) if arr[i] else 0.0
        except Exception:
            return 0.0

    return {
        "name": arr[0],
        "open": f(1),
        "preclose": f(2),
        "price": f(3),
        "high": f(4),
        "low": f(5),
        # volume: shares? sina gives hands? For A-share: vol is shares in this endpoint (commonly shares)
        "volume": f(8),
        "amount": f(9),
        "date": arr[30],
        "time": arr[31],
        "raw": text.strip(),
    }


def fetch_kline(symbol: str, scale: int, datalen: int = 500) -> List[Dict[str, Any]]:
    url = (
        "https://quotes.sina.cn/cn/api/json_v2.php/"
        "CN_MarketDataService.getKLineData"
        f"?symbol={symbol}&scale={scale}&ma=no&datalen={datalen}"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    js = r.json()
    if not isinstance(js, list):
        raise RuntimeError(f"Unexpected kline json: {str(js)[:200]}")
    return js


def parse_dt(s: str) -> datetime:
    # examples: '2026-02-10 15:00:00'
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def to_num(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def summarize_ohlc(rows: List[Dict[str, Any]]) -> Optional[Ohlc]:
    if not rows:
        return None
    o = to_num(rows[0]["open"])
    h = max(to_num(r["high"]) for r in rows)
    l = min(to_num(r["low"]) for r in rows)
    c = to_num(rows[-1]["close"])
    vol = sum(to_num(r.get("volume")) for r in rows)
    amt = sum(to_num(r.get("amount")) for r in rows)
    return Ohlc(open=o, high=h, low=l, close=c, vol=vol, amt=amt)


def pct(a: float, b: float) -> Optional[float]:
    if not b:
        return None
    return (a / b - 1.0) * 100.0


def fmt_pct(x: Optional[float]) -> str:
    if x is None or math.isnan(x):
        return "-"
    return f"{x:+.2f}%"


def fmt_money(x: float) -> str:
    # x in yuan
    if x >= 1e8:
        return f"{x/1e8:.2f}亿"
    if x >= 1e4:
        return f"{x/1e4:.2f}万"
    return f"{x:.0f}"


def fmt_vol(x: float) -> str:
    if x >= 1e8:
        return f"{x/1e8:.2f}亿股"
    if x >= 1e4:
        return f"{x/1e4:.2f}万股"
    return f"{x:.0f}股"


def classify_intraday(open_: float, high: float, low: float, close: float, preclose: float) -> str:
    # Very simple “human” label.
    ch = pct(close, preclose) or 0.0
    rng = (pct(high, low) or 0.0)
    if abs(ch) < 0.3 and rng < 1.5:
        return "震荡"
    if ch > 0.5:
        return "偏强"
    if ch < -0.5:
        return "偏弱"
    return "震荡偏{}".format("强" if ch >= 0 else "弱")


def index_quote(symbol: str) -> Tuple[float, float]:
    # returns (price, preclose)
    q = fetch_realtime_quote(symbol)
    return float(q["price"]), float(q["preclose"])


def build_report(
    *,
    stock_symbol: str,
    stock_name: str,
    report_date: date,
    mode: str,
    scale: int = 5,
) -> str:
    """mode: 'mid' or 'close'"""

    quote = fetch_realtime_quote(stock_symbol)
    preclose = float(quote["preclose"])
    open_ = float(quote["open"])
    high_rt = float(quote["high"])
    low_rt = float(quote["low"])
    last_px = float(quote["price"])

    kl = fetch_kline(stock_symbol, scale=scale, datalen=800)
    rows = [r for r in kl if r.get("day", "").startswith(report_date.isoformat())]
    for r in rows:
        r["_dt"] = parse_dt(r["day"])

    rows.sort(key=lambda r: r["_dt"])

    # trading sessions (A-share)
    morning_end = datetime.combine(report_date, time(11, 30))
    if mode == "mid":
        use_rows = [r for r in rows if r["_dt"] <= morning_end]
    else:
        use_rows = rows

    ohlc = summarize_ohlc(use_rows)
    if not ohlc:
        raise RuntimeError("No kline rows for date; market closed or data unavailable")

    # segment analysis
    def segment(t0: time, t1: time) -> Optional[Ohlc]:
        seg = [r for r in rows if t0 <= r["_dt"].time() <= t1]
        return summarize_ohlc(seg) if seg else None

    seg_open30 = segment(time(9, 30), time(10, 0))
    seg_last30 = segment(time(14, 30), time(15, 0))

    # indices
    sh_px, sh_pre = index_quote("sh000001")
    sz_px, sz_pre = index_quote("sz399001")
    cyb_px, cyb_pre = index_quote("sz399006")

    # output
    if mode == "mid":
        title = f"【午间快报】{report_date.isoformat()} 11:45（截至 11:30 休市）"
    else:
        title = f"【收盘详报】{report_date.isoformat()} 15:10"

    ch_close = pct(ohlc.close, preclose)
    label = classify_intraday(open_, ohlc.high, ohlc.low, ohlc.close, preclose)

    lines: List[str] = []
    lines.append(title)
    lines.append(f"标的：{stock_name}({stock_symbol[-6:]})")
    lines.append("")

    lines.append("1) 盘前/开盘（竞价口径：先用平开/高开/低开+开盘缺口代替）")
    lines.append(f"- 昨收：{preclose:.2f}")
    lines.append(f"- 今开：{open_:.2f}（开盘缺口：{fmt_pct(pct(open_, preclose))}）")

    lines.append("")
    lines.append("2) 盘内走势（用分时区间+结构描述）")
    lines.append(f"- {'上午' if mode=='mid' else '全日'}区间：{ohlc.low:.2f} ~ {ohlc.high:.2f}")
    lines.append(f"- {'午间(11:30)' if mode=='mid' else '收盘'}：{ohlc.close:.2f}（{fmt_pct(ch_close)}，{label}）")

    if seg_open30:
        lines.append(
            f"- 开盘前30分钟(09:30-10:00)：{seg_open30.low:.2f}~{seg_open30.high:.2f}，收于 {seg_open30.close:.2f}"
        )
    if mode != "mid" and seg_last30:
        lines.append(
            f"- 尾盘30分钟(14:30-15:00)：{seg_last30.low:.2f}~{seg_last30.high:.2f}，收于 {seg_last30.close:.2f}"
        )

    lines.append("")
    lines.append("3) 量能/成本（成交额/成交量/VWAP）")
    lines.append(f"- 成交量：{fmt_vol(ohlc.vol)}")
    lines.append(f"- 成交额：{fmt_money(ohlc.amt)}")
    if ohlc.vwap:
        lines.append(f"- VWAP(当日均价)：{ohlc.vwap:.3f}（当前价格{'高于' if ohlc.close>ohlc.vwap else '低于' if ohlc.close<ohlc.vwap else '≈'}均价）")

    lines.append("")
    lines.append("4) 大盘背景（指数涨跌幅）")
    lines.append(f"- 上证：{fmt_pct(pct(sh_px, sh_pre))}")
    lines.append(f"- 深成指：{fmt_pct(pct(sz_px, sz_pre))}")
    lines.append(f"- 创业板：{fmt_pct(pct(cyb_px, cyb_pre))}")

    lines.append("")
    lines.append("5) 关键价位（明日/午后盯盘用）")
    # Use simple key levels: high/low/vwap/round number
    levels = []
    levels.append(("压力", round(ohlc.high, 2)))
    if ohlc.vwap:
        levels.append(("成本(VWAP)", round(ohlc.vwap, 3)))
    levels.append(("支撑", round(ohlc.low, 2)))
    # Add psychological 10.00 if in range
    if ohlc.low <= 10.0 <= ohlc.high:
        levels.insert(1, ("心理关口", 10.00))

    for name, lv in levels[:4]:
        if isinstance(lv, float) and abs(lv - 10.0) < 1e-9:
            lines.append(f"- {name}：10.00")
        else:
            lines.append(f"- {name}：{lv}")

    lines.append("")
    lines.append("备注：本报告为数据解读，不构成投资建议。")

    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="Sina symbol, e.g. sh600158")
    ap.add_argument("--name", default="", help="Optional display name")
    ap.add_argument("--mode", choices=["mid", "close"], required=True)
    ap.add_argument("--date", default="", help="YYYY-MM-DD, default: today")
    ap.add_argument("--scale", type=int, default=5, help="Kline scale minutes (1/5/15/30/60)")
    args = ap.parse_args()

    d = date.today() if not args.date else datetime.strptime(args.date, "%Y-%m-%d").date()

    q = fetch_realtime_quote(args.symbol)
    name = args.name or q.get("name") or args.symbol

    print(build_report(stock_symbol=args.symbol, stock_name=name, report_date=d, mode=args.mode, scale=args.scale))


if __name__ == "__main__":
    main()
