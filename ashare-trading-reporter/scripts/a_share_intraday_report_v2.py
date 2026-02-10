#!/usr/bin/env python3
"""Generate an intraday A-share report (midday 11:45 / close 15:10) with multi-source data.

Why v2:
- Sina endpoints are convenient but can be rate-limited / occasionally missing fields.
- Eastmoney push2 endpoints are usually more stable for minute/5m Klines.
- We add provider chaining (eastmoney -> sina) and an optional auction snapshot hook.

Data sources used (public, no key):
- Eastmoney quote:  https://push2.eastmoney.com/api/qt/stock/get
- Eastmoney kline:  https://push2his.eastmoney.com/api/qt/stock/kline/get
- Sina quote:       https://hq.sinajs.cn/list=sh600158
- Sina kline:       https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData

Auction (09:25) note:
- Most free public endpoints do NOT expose the 09:25 call-auction final match reliably after-the-fact.
- This script supports reading a pre-captured auction snapshot JSON (saved by a cron around 09:25-09:29).
- If snapshot missing, report falls back to "open gap" wording.

No pandas; dependency-light.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


# -------------------------
# Common data structures
# -------------------------


@dataclass
class Quote:
    name: str
    open: float
    preclose: float
    price: float
    high: float
    low: float
    volume: float  # shares
    amount: float  # yuan
    dt: Optional[datetime] = None
    source: str = ""


@dataclass
class Bar:
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float  # shares
    amount: float  # yuan


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


# -------------------------
# Utils
# -------------------------


def _get(url: str, *, timeout: int = 10, headers: Optional[dict] = None, params: Optional[dict] = None) -> requests.Response:
    headers = headers or {}
    headers.setdefault("User-Agent", "Mozilla/5.0")
    r = requests.get(url, params=params, timeout=timeout, headers=headers)
    r.raise_for_status()
    return r


def to_num(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def pct(a: float, b: float) -> Optional[float]:
    if not b:
        return None
    return (a / b - 1.0) * 100.0


def fmt_pct(x: Optional[float]) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x:+.2f}%"


def fmt_money(x: float) -> str:
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


def parse_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M") if len(s) == 16 else datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def summarize_ohlc(bars: List[Bar]) -> Optional[Ohlc]:
    if not bars:
        return None
    o = bars[0].open
    h = max(b.high for b in bars)
    l = min(b.low for b in bars)
    c = bars[-1].close
    vol = sum(b.volume for b in bars)
    amt = sum(b.amount for b in bars)
    return Ohlc(open=o, high=h, low=l, close=c, vol=vol, amt=amt)


def classify_intraday(open_: float, high: float, low: float, close: float, preclose: float) -> str:
    ch = pct(close, preclose) or 0.0
    rng = pct(high, low) or 0.0
    if abs(ch) < 0.3 and rng < 1.5:
        return "震荡"
    if ch > 0.5:
        return "偏强"
    if ch < -0.5:
        return "偏弱"
    return "震荡偏{}".format("强" if ch >= 0 else "弱")


# -------------------------
# Providers
# -------------------------


class Provider:
    name = "base"

    def quote(self, symbol: str) -> Quote:  # symbol: sh600158 / sz399001
        raise NotImplementedError

    def kline(self, symbol: str, *, scale_min: int, day: date) -> List[Bar]:
        raise NotImplementedError


class EastmoneyProvider(Provider):
    name = "eastmoney"

    @staticmethod
    def _secid(symbol: str) -> str:
        m = re.fullmatch(r"(sh|sz)(\d{6})", symbol)
        if not m:
            raise ValueError(f"Bad symbol: {symbol}")
        ex, code = m.group(1), m.group(2)
        market = "1" if ex == "sh" else "0"
        return f"{market}.{code}"

    def quote(self, symbol: str) -> Quote:
        secid = self._secid(symbol)
        fields = "f58,f43,f44,f45,f46,f60,f47,f48,f86"
        r = _get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": secid, "fields": fields},
            headers={"Referer": "https://quote.eastmoney.com"},
        )
        data = r.json().get("data") or {}
        # Prices are typically scaled by 100 for A-share.
        px = to_num(data.get("f43")) / 100.0
        high = to_num(data.get("f44")) / 100.0
        low = to_num(data.get("f45")) / 100.0
        open_ = to_num(data.get("f46")) / 100.0
        preclose = to_num(data.get("f60")) / 100.0
        vol_lot = to_num(data.get("f47"))
        amt = to_num(data.get("f48"))
        ts = data.get("f86")
        dt = datetime.fromtimestamp(int(ts)) if ts else None
        return Quote(
            name=str(data.get("f58") or symbol),
            open=open_,
            preclose=preclose,
            price=px,
            high=high,
            low=low,
            volume=vol_lot * 100.0,
            amount=amt,
            dt=dt,
            source=self.name,
        )

    def kline(self, symbol: str, *, scale_min: int, day: date) -> List[Bar]:
        secid = self._secid(symbol)
        # klt: 1/5/15/30/60
        klt = int(scale_min)
        ds = day.strftime("%Y%m%d")
        r = _get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "secid": secid,
                "klt": str(klt),
                "fqt": "1",
                "beg": ds,
                "end": ds,
                "lmt": "1000",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
            headers={"Referer": "https://quote.eastmoney.com"},
        )
        js = r.json()
        kl = (js.get("data") or {}).get("klines") or []
        out: List[Bar] = []
        for line in kl:
            # "YYYY-MM-DD HH:MM,open,close,high,low,volume,amount,..."
            parts = str(line).split(",")
            if len(parts) < 7:
                continue
            dt = parse_dt(parts[0])
            o, c, h, l = map(float, parts[1:5])
            vol_lot = to_num(parts[5])
            amt = to_num(parts[6])
            out.append(Bar(dt=dt, open=o, high=h, low=l, close=c, volume=vol_lot * 100.0, amount=amt))
        out.sort(key=lambda b: b.dt)
        return out


class SinaProvider(Provider):
    name = "sina"

    def quote(self, symbol: str) -> Quote:
        text = _get(
            f"https://hq.sinajs.cn/list={symbol}",
            headers={"Referer": "https://finance.sina.com.cn"},
        ).text
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

        dt = None
        try:
            if arr[30] and arr[31]:
                dt = datetime.strptime(f"{arr[30]} {arr[31]}", "%Y-%m-%d %H:%M:%S")
        except Exception:
            dt = None

        return Quote(
            name=arr[0] or symbol,
            open=f(1),
            preclose=f(2),
            price=f(3),
            high=f(4),
            low=f(5),
            volume=f(8),
            amount=f(9),
            dt=dt,
            source=self.name,
        )

    def kline(self, symbol: str, *, scale_min: int, day: date) -> List[Bar]:
        url = (
            "https://quotes.sina.cn/cn/api/json_v2.php/"
            "CN_MarketDataService.getKLineData"
        )
        r = _get(url, params={"symbol": symbol, "scale": str(int(scale_min)), "ma": "no", "datalen": "800"})
        js = r.json()
        if not isinstance(js, list):
            raise RuntimeError(f"Unexpected kline json: {str(js)[:200]}")
        out: List[Bar] = []
        prefix = day.isoformat()
        for row in js:
            s = str(row.get("day") or "")
            if not s.startswith(prefix):
                continue
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            out.append(
                Bar(
                    dt=dt,
                    open=to_num(row.get("open")),
                    high=to_num(row.get("high")),
                    low=to_num(row.get("low")),
                    close=to_num(row.get("close")),
                    volume=to_num(row.get("volume")),
                    amount=to_num(row.get("amount")),
                )
            )
        out.sort(key=lambda b: b.dt)
        return out


class ProviderChain(Provider):
    name = "chain"

    def __init__(self, providers: List[Provider]):
        self.providers = providers

    def quote(self, symbol: str) -> Quote:
        last_err: Optional[Exception] = None
        for p in self.providers:
            try:
                q = p.quote(symbol)
                q.source = p.name
                return q
            except Exception as e:
                last_err = e
        raise RuntimeError(f"All providers failed for quote({symbol}): {last_err}")

    def kline(self, symbol: str, *, scale_min: int, day: date) -> List[Bar]:
        last_err: Optional[Exception] = None
        for p in self.providers:
            try:
                bars = p.kline(symbol, scale_min=scale_min, day=day)
                if bars:
                    return bars
            except Exception as e:
                last_err = e
        raise RuntimeError(f"All providers failed for kline({symbol},{scale_min}m): {last_err}")


# -------------------------
# Auction snapshot (optional)
# -------------------------


def load_auction_snapshot(auction_dir: Path, symbol: str, day: date) -> Optional[dict]:
    # file naming: YYYY-MM-DD_sh600158.json
    fp = auction_dir / f"{day.isoformat()}_{symbol}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


# -------------------------
# Report builder
# -------------------------


def build_report(
    *,
    provider: Provider,
    stock_symbol: str,
    stock_name: str,
    report_date: date,
    mode: str,
    scale: int = 5,
    watch_levels: Optional[List[float]] = None,
    auction_dir: Optional[Path] = None,
) -> str:
    """mode: 'mid' or 'close'"""

    quote = provider.quote(stock_symbol)
    preclose = quote.preclose
    open_ = quote.open

    bars_all = provider.kline(stock_symbol, scale_min=scale, day=report_date)

    # A-share sessions
    morning_end = datetime.combine(report_date, time(11, 30))
    if mode == "mid":
        bars = [b for b in bars_all if b.dt <= morning_end]
    else:
        bars = bars_all

    ohlc = summarize_ohlc(bars)
    if not ohlc:
        raise RuntimeError("No kline rows for date; market closed or data unavailable")

    def segment(t0: time, t1: time) -> Optional[Ohlc]:
        seg = [b for b in bars_all if t0 <= b.dt.time() <= t1]
        return summarize_ohlc(seg)

    seg_open30 = segment(time(9, 30), time(10, 0))
    seg_last30 = segment(time(14, 30), time(15, 0)) if mode != "mid" else None

    # indices (use same provider chain)
    sh = provider.quote("sh000001")
    sz = provider.quote("sz399001")
    cyb = provider.quote("sz399006")

    # auction snapshot (optional)
    auction = load_auction_snapshot(auction_dir, stock_symbol, report_date) if auction_dir else None

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
    lines.append(f"数据源：{quote.source} (kline={type(provider).__name__})")
    lines.append("")

    lines.append("1) 集合竞价/开盘")
    lines.append(f"- 昨收：{preclose:.2f}")

    if auction and auction.get("auction_price"):
        apx = float(auction["auction_price"])
        lines.append(f"- 竞价(09:25)：{apx:.2f}（相对昨收：{fmt_pct(pct(apx, preclose))}）")
        if auction.get("auction_amount"):
            lines.append(f"- 竞价成交额：{fmt_money(float(auction['auction_amount']))}")
        lines.append(f"- 今开(09:30)：{open_:.2f}（开盘缺口：{fmt_pct(pct(open_, preclose))}）")
    else:
        lines.append(f"- 今开(09:30)：{open_:.2f}（开盘缺口：{fmt_pct(pct(open_, preclose))}）")
        lines.append("- 竞价(09:25)：未稳定获取 → 本报告用“开盘缺口”作为替代口径")

    lines.append("")
    lines.append("2) 盘内走势（区间 + 结构）")
    lines.append(f"- {'上午' if mode=='mid' else '全日'}区间：{ohlc.low:.2f} ~ {ohlc.high:.2f}")
    lines.append(f"- {'午间(11:30)' if mode=='mid' else '收盘'}：{ohlc.close:.2f}（{fmt_pct(ch_close)}，{label}）")

    if seg_open30:
        lines.append(
            f"- 开盘前30分钟(09:30-10:00)：{seg_open30.low:.2f}~{seg_open30.high:.2f}，收于 {seg_open30.close:.2f}"
        )
    if seg_last30:
        lines.append(
            f"- 尾盘30分钟(14:30-15:00)：{seg_last30.low:.2f}~{seg_last30.high:.2f}，收于 {seg_last30.close:.2f}"
        )

    lines.append("")
    lines.append("3) 量能/成本（成交额/成交量/VWAP）")
    lines.append(f"- 成交量：{fmt_vol(ohlc.vol)}")
    lines.append(f"- 成交额：{fmt_money(ohlc.amt)}")
    if ohlc.vwap:
        rel = "高于" if ohlc.close > ohlc.vwap else "低于" if ohlc.close < ohlc.vwap else "≈"
        lines.append(f"- VWAP(均价)：{ohlc.vwap:.3f}（当前价格{rel}均价）")

    lines.append("")
    lines.append("4) 大盘背景（指数涨跌幅）")
    lines.append(f"- 上证：{fmt_pct(pct(sh.price, sh.preclose))}")
    lines.append(f"- 深成指：{fmt_pct(pct(sz.price, sz.preclose))}")
    lines.append(f"- 创业板：{fmt_pct(pct(cyb.price, cyb.preclose))}")

    lines.append("")
    lines.append("5) 关键价位（盯盘/复盘用）")
    lines.append(f"- 压力：{ohlc.high:.2f}")
    if ohlc.vwap:
        lines.append(f"- 成本(VWAP)：{ohlc.vwap:.3f}")
    if ohlc.low <= 10.0 <= ohlc.high:
        lines.append("- 心理关口：10.00")
    lines.append(f"- 支撑：{ohlc.low:.2f}")

    if watch_levels:
        lines.append("- 自定义关注价位：" + " / ".join(f"{x:g}" for x in watch_levels))

    lines.append("")
    lines.append("备注：本报告为数据解读，不构成投资建议。")

    return "\n".join(lines)


def _parse_watch(s: str) -> List[float]:
    out: List[float] = []
    for tok in re.split(r"[ ,/]+", s.strip()):
        if not tok:
            continue
        try:
            out.append(float(tok))
        except Exception:
            pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="Symbol like sh600158 / sz000001")
    ap.add_argument("--name", default="", help="Optional display name")
    ap.add_argument("--mode", choices=["mid", "close"], required=True)
    ap.add_argument("--date", default="", help="YYYY-MM-DD, default: today")
    ap.add_argument("--scale", type=int, default=5, help="Kline scale minutes (1/5/15/30/60)")
    ap.add_argument(
        "--source",
        choices=["auto", "eastmoney", "sina"],
        default="auto",
        help="Data source. auto=eastmoney->sina fallback",
    )
    ap.add_argument("--watch", default="", help="Custom watch levels, e.g. '9.5/10.1/9.0/8.5'")
    ap.add_argument(
        "--auction-dir",
        default=str(Path("data/ashare/auction")),
        help="Dir holding optional auction snapshot JSON files",
    )
    args = ap.parse_args()

    d = date.today() if not args.date else datetime.strptime(args.date, "%Y-%m-%d").date()

    if args.source == "eastmoney":
        provider: Provider = EastmoneyProvider()
    elif args.source == "sina":
        provider = SinaProvider()
    else:
        provider = ProviderChain([EastmoneyProvider(), SinaProvider()])

    q = provider.quote(args.symbol)
    name = args.name or q.name or args.symbol

    watch = _parse_watch(args.watch) if args.watch else None
    auction_dir = Path(args.auction_dir)

    print(
        build_report(
            provider=provider,
            stock_symbol=args.symbol,
            stock_name=name,
            report_date=d,
            mode=args.mode,
            scale=args.scale,
            watch_levels=watch,
            auction_dir=auction_dir,
        )
    )


if __name__ == "__main__":
    main()
