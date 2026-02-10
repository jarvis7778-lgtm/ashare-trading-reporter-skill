#!/usr/bin/env python3
"""0-token A-share intraday alerts.

Designed to be run from system crontab (e.g., every minute). It does NOT call any LLM.
It polls free quote endpoints (Sina) and only sends a Discord message when a trigger fires.

Triggers (default):
- Touch/above 10.00
- Touch/above 10.03
- Break below 9.86
- Cross above/below intraday VWAP

De-dup: each trigger fires at most once per trading day.

Usage:
  python3 scripts/a_share_price_alerts.py \
    --symbol sh600158 --target channel:1470480529609588888

Env:
  none (uses openclaw CLI installed on host)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


@dataclass
class Quote:
    name: str
    open: float
    preclose: float
    price: float
    high: float
    low: float
    volume: float
    amount: float
    date: str
    time: str


def _get(url: str, *, timeout: int = 10, headers: Optional[dict] = None) -> str:
    r = requests.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    return r.text


def fetch_sina_quote(symbol: str) -> Quote:
    text = _get(
        f"https://hq.sinajs.cn/list={symbol}",
        headers={"Referer": "https://finance.sina.com.cn"},
    )
    # var hq_str_sh600158="...";
    try:
        payload = text.split('"', 2)[1]
    except Exception:
        raise RuntimeError(f"Unexpected quote payload: {text[:200]}")
    arr = payload.split(',')
    if len(arr) < 32:
        raise RuntimeError(f"Unexpected quote fields={len(arr)}: {text[:200]}")

    def f(i: int) -> float:
        try:
            return float(arr[i]) if arr[i] else 0.0
        except Exception:
            return 0.0

    return Quote(
        name=arr[0],
        open=f(1),
        preclose=f(2),
        price=f(3),
        high=f(4),
        low=f(5),
        volume=f(8),
        amount=f(9),
        date=arr[30],
        time=arr[31],
    )


def fetch_sina_kline(symbol: str, scale: int = 1, datalen: int = 800) -> List[Dict[str, Any]]:
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


def to_num(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def compute_vwap(symbol: str, yyyy_mm_dd: str) -> Optional[float]:
    # Minute kline may start at 09:31; it's fine for a practical intraday VWAP.
    rows = fetch_sina_kline(symbol, scale=1, datalen=1000)
    rows = [r for r in rows if str(r.get('day', '')).startswith(yyyy_mm_dd)]
    if not rows:
        return None
    vol = sum(to_num(r.get('volume')) for r in rows)
    amt = sum(to_num(r.get('amount')) for r in rows)
    if not vol:
        return None
    return amt / vol


def is_trading_time(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    t = now.time()
    # A-share sessions: 09:30-11:30, 13:00-15:00
    if time(9, 30) <= t <= time(11, 30):
        return True
    if time(13, 0) <= t <= time(15, 0):
        return True
    return False


def pct(a: float, b: float) -> float:
    if not b:
        return 0.0
    return (a / b - 1.0) * 100.0


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


def send_message(channel: str, target: str, message: str) -> None:
    """Send via OpenClaw CLI (no LLM)."""
    cmd = [
        "openclaw",
        "message",
        "send",
        "--channel",
        channel,
        "--target",
        target,
        "--message",
        message,
    ]
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="Sina symbol, e.g. sh600158")
    ap.add_argument("--channel", default="telegram", help="Message channel: telegram|discord")
    ap.add_argument("--target", required=True, help="Recipient: Telegram <chatId> or Discord channel:<id>")
    ap.add_argument("--config", default="", help="Optional JSON config file with per-symbol triggers")
    ap.add_argument("--levels", default="10.00,10.03", help="Comma levels for upside touch (used if --config not set)")
    ap.add_argument("--breakdown", default="9.86", help="Break below level (used if --config not set)")
    ap.add_argument("--state-dir", default="data/ashare/alerts", help="Directory to store state")
    args = ap.parse_args()

    now = datetime.now()
    if not is_trading_time(now):
        return

    cfg = {}
    if args.config:
        try:
            cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
        except Exception:
            cfg = {}

    q = fetch_sina_quote(args.symbol)
    day = q.date  # YYYY-MM-DD
    state_path = Path(args.state_dir) / f"{day}_{args.symbol}.json"
    state = load_state(state_path)

    # reset if date changed (safety)
    if state.get("date") != day:
        state = {"date": day, "fired": {}}

    fired: Dict[str, bool] = state.setdefault("fired", {})

    vwap = None
    try:
        vwap = compute_vwap(args.symbol, day)
    except Exception:
        vwap = None

    last_rel = state.get("vwap_rel")  # 'above'|'below'|None
    rel = None
    if vwap:
        rel = "above" if q.price > vwap else "below" if q.price < vwap else "equal"

    change = pct(q.price, q.preclose)
    ts = f"{q.date} {q.time}"

    def fire(key: str, text: str) -> None:
        if fired.get(key):
            return
        fired[key] = True
        state["last_fire_at"] = ts
        save_state(state_path, state)
        send_message(args.channel, args.target, text)

    # Resolve triggers (config overrides CLI defaults)
    # Config schema example:
    # {
    #   "levels_up": [10.0, 10.03],
    #   "breakdown": 9.86,
    #   "vwap_cross": true
    # }
    try:
        levels = cfg.get('levels_up') if isinstance(cfg.get('levels_up'), list) else None
        if levels is None:
            levels = [float(x.strip()) for x in args.levels.split(',') if x.strip()]
        else:
            levels = [float(x) for x in levels]
    except Exception:
        levels = []
    for lv in levels:
        key = f"touch_up_{lv:.2f}"
        if q.price >= lv and not fired.get(key):
            msg = (
                f"【盘中提醒】{q.name}({args.symbol[-6:]}) 触达 {lv:.2f}\n"
                f"- 时间：{ts}\n"
                f"- 现价：{q.price:.2f}（{change:+.2f}%）\n"
                + (f"- VWAP：{vwap:.3f}（{'上方' if q.price>vwap else '下方' if q.price<vwap else '≈'}）\n" if vwap else "")
                + "（当日该条件仅提醒一次）"
            )
            fire(key, msg)
            return

    # Breakdown
    try:
        bd = float(cfg.get('breakdown')) if cfg.get('breakdown') is not None else float(args.breakdown)
    except Exception:
        bd = float(args.breakdown)
    key_bd = f"break_dn_{bd:.2f}"
    if q.price < bd and not fired.get(key_bd):
        msg = (
            f"【盘中提醒】{q.name}({args.symbol[-6:]}) 跌破 {bd:.2f}\n"
            f"- 时间：{ts}\n"
            f"- 现价：{q.price:.2f}（{change:+.2f}%）\n"
            + (f"- VWAP：{vwap:.3f}\n" if vwap else "")
            + "（当日该条件仅提醒一次）"
        )
        fire(key_bd, msg)
        return

    # VWAP cross (can be disabled)
    vwap_cross_enabled = bool(cfg.get('vwap_cross', True))
    if vwap_cross_enabled and vwap and rel in ("above", "below"):
        if last_rel and last_rel != rel:
            key = f"vwap_cross_{rel}"
            if not fired.get(key):
                direction = "上穿" if rel == "above" else "下穿"
                msg = (
                    f"【盘中提醒】{q.name}({args.symbol[-6:]}) {direction} VWAP\n"
                    f"- 时间：{ts}\n"
                    f"- 现价：{q.price:.2f}（{change:+.2f}%）\n"
                    f"- VWAP：{vwap:.3f}\n"
                    "（当日该条件仅提醒一次）"
                )
                fire(key, msg)
                state["vwap_rel"] = rel
                save_state(state_path, state)
                return
        state["vwap_rel"] = rel
        save_state(state_path, state)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never crash noisy in cron
        sys.exit(0)
