#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

import ccxt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from flask import Flask, jsonify, redirect, render_template_string, request, send_file, url_for
from plotly.subplots import make_subplots

app = Flask(__name__)
ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
PIDS = RUNTIME / "ui_pids.json"
LIVE_LOG = RUNTIME / "ui_live.log"
UI_DEBUG_LOG = RUNTIME / "ui_app_debug.log"
SIGNAL_STATE_FILE = RUNTIME / "signal_state.json"
BACKTEST_STATUS_FILE = RUNTIME / "backtest_status.json"
BACKTEST_REPORT_FILE = RUNTIME / "backtest_report.json"
BACKTEST_REPORT_MD_FILE = RUNTIME / "backtest_report.md"

CN_TZ = ZoneInfo("Asia/Shanghai")
SIGNAL_TIMEFRAME = "4h"  # 运行信号固定4h，与图表周期隔离

RUNTIME.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(UI_DEBUG_LOG, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ui_app")

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAMES = ["1m", "15m", "1h", "4h", "1d", "1w"]
EXCHANGES = ["bybit", "okx", "binance"]
MARKET_CACHE: Dict[Tuple[str, str, int], Dict] = {}
BACKTEST_LOCK = threading.Lock()
BACKTEST_THREAD: threading.Thread | None = None


@dataclass
class SignalPlan:
    signal: str
    reason: str
    signal_ts: str
    entry: float
    stop_loss: float
    take_profit: float


HTML = """
<!doctype html><html><head><meta charset='utf-8'><title>策略控制台</title>
<style>
body{font-family:Arial;margin:20px}
button{margin:4px;padding:8px 12px}
pre{background:#111;color:#0f0;padding:10px;white-space:pre-wrap;max-height:380px;overflow:auto}
.msg{padding:8px 12px;background:#eef;border:1px solid #99c;margin-bottom:10px}
iframe{width:100%;height:760px;border:1px solid #ddd}
.toolbar{margin-bottom:8px}
.pill{display:inline-block;padding:6px 10px;margin:2px;border:1px solid #aaa;border-radius:6px;text-decoration:none;color:#000}
.pill.active{background:#333;color:#fff}
.note{padding:8px 12px;background:#fff7d6;border:1px solid #d9b100;margin-bottom:10px}
.panel{padding:10px;border:1px solid #ddd;background:#fafafa;margin-bottom:12px}
select{padding:4px}
</style></head>
<body>
<h2>交易策略控制台</h2>
<div class="note"><b>运行策略固定4h:</b> EMA89/144/169 Cross入场 + MACD背离止盈；图表周期切换只影响展示。</div>
{% if msg %}<div class="msg">{{msg}}</div>{% endif %}

<div class="toolbar"><b>标的:</b>
{% for s in symbols %}<a class="pill {% if s==symbol %}active{% endif %}" href="/?symbol={{s}}&timeframe={{timeframe}}">{{s}}</a>{% endfor %}
</div>
<div class="toolbar"><b>图表周期:</b>
{% for tf in timeframes %}<a class="pill {% if tf==timeframe %}active{% endif %}" href="/?symbol={{symbol}}&timeframe={{tf}}">{{tf}}</a>{% endfor %}
</div>

<div class="panel">
  <h3>全历史4h回测（多指标 + 仓位管理优化）</h3>
  <form method='post' action='/run_backtest?symbol={{symbol}}&timeframe={{timeframe}}'>
    <label>回测标的：</label>
    <select name="backtest_symbol">
      <option value="BTC/USDT">BTC/USDT</option>
      <option value="ETH/USDT">ETH/USDT</option>
      <option value="BOTH" selected>BTC + ETH</option>
    </select>
    <label>分析强度：</label>
    <select name="profile"><option value="standard">standard</option><option value="deep" selected>deep</option></select>
    <button>启动全历史回测</button>
  </form>
  <pre id="backtest_status">加载中...</pre>
</div>

<h3>K线图（带 EMA/MACD/VOL）</h3>
<iframe src="/chart?symbol={{symbol}}&timeframe={{timeframe}}"></iframe>

<form method='post' action='/start_auto?symbol={{symbol}}&timeframe={{timeframe}}'><button>开启自动下单(固定4h信号)</button></form>
<form method='post' action='/stop_all?symbol={{symbol}}&timeframe={{timeframe}}'><button>停止后台任务</button></form>

<h3>实时状态</h3><pre id="live_status">加载中...</pre>
<h3>历史记录条数: {{history_count}}</h3>
<pre>{{payload}}</pre>
<h3>实时日志</h3><pre id="live_log">{{live_log}}</pre>

<script>
async function refreshLive(){
  const symbol = {{ symbol|tojson }}; const timeframe = {{ timeframe|tojson }};
  const s = await (await fetch(`/api/market_status?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`)).json();
  document.getElementById('live_status').textContent = JSON.stringify(s, null, 2);
  document.getElementById('live_log').textContent = await (await fetch('/api/live_log_tail')).text();
  document.getElementById('backtest_status').textContent = JSON.stringify(await (await fetch('/api/backtest_status')).json(), null, 2);
}
refreshLive(); setInterval(refreshLive, 10000);
</script></body></html>
"""


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_live_log(line: str) -> None:
    with LIVE_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(CN_TZ).isoformat()} {line}\n")


def tail_live_log(max_lines: int = 120) -> str:
    if not LIVE_LOG.exists():
        return "暂无实时日志"
    return "\n".join(LIVE_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-max_lines:])


def load_pids() -> dict:
    return load_json(PIDS, {})


def save_pids(data: dict) -> None:
    save_json(PIDS, data)


def load_signal_state() -> dict:
    return load_json(SIGNAL_STATE_FILE, {})


def save_signal_state(state: dict) -> None:
    save_json(SIGNAL_STATE_FILE, state)


def get_history_count() -> int:
    db = RUNTIME / "history.db"
    if not db.exists():
        return 0
    con = sqlite3.connect(db)
    try:
        return int(con.execute("SELECT COUNT(1) FROM signal_history").fetchone()[0])
    finally:
        con.close()


def run_proc(name: str, auto_trade: bool, symbol: str) -> None:
    cmd = [sys.executable, "crypto_deep_strategy.py", "--symbol", symbol, "--daemon", "--interval_minutes", "15", "--output_dir", "runtime"]
    if auto_trade:
        cmd.append("--auto_trade")
    p = subprocess.Popen(cmd, cwd=ROOT)
    pids = load_pids(); pids[name] = p.pid; save_pids(pids)


def _resolve_exchanges() -> list[str]:
    preferred = os.getenv("PREFERRED_EXCHANGE", "").strip().lower()
    items = [preferred] + EXCHANGES if preferred else EXCHANGES
    out, seen = [], set()
    for x in items:
        if x and x not in seen:
            seen.add(x); out.append(x)
    return out


def _timeframe_ms(tf: str) -> int:
    return int(tf[:-1]) * {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}[tf[-1]]


def fetch_market(symbol: str, timeframe: str, limit: int = 600, ttl_s: float = 0.0) -> Tuple[pd.DataFrame, str]:
    key = (symbol, timeframe, limit)
    now = time.time()
    c = MARKET_CACHE.get(key)
    if c and ttl_s > 0 and now - c["ts"] <= ttl_s:
        return c["df"].copy(), c["exchange"]
    errors = []
    for ex_name in _resolve_exchanges():
        try:
            ex = getattr(ccxt, ex_name)({"enableRateLimit": True})
            rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert(CN_TZ).dt.tz_localize(None)
            MARKET_CACHE[key] = {"ts": now, "df": df.copy(), "exchange": ex_name}
            return df, ex_name
        except Exception as e:
            errors.append(f"{ex_name}: {e}")
    raise RuntimeError(" ; ".join(errors))


def fetch_full_ohlcv(symbol: str, timeframe: str = "4h") -> tuple[pd.DataFrame, str]:
    start_since = int(datetime(2017, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    tf_ms = _timeframe_ms(timeframe)
    errors = []
    for ex_name in _resolve_exchanges():
        try:
            ex = getattr(ccxt, ex_name)({"enableRateLimit": True})
            since, rows_all = start_since, []
            for _ in range(2500):
                rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
                if not rows:
                    break
                if rows_all and rows[0][0] <= rows_all[-1][0]:
                    rows = [r for r in rows if r[0] > rows_all[-1][0]]
                if not rows:
                    break
                rows_all.extend(rows)
                since = rows[-1][0] + tf_ms
                if len(rows) < 1000:
                    break
                time.sleep(ex.rateLimit / 1000.0)
            if not rows_all:
                raise RuntimeError("empty")
            df = pd.DataFrame(rows_all, columns=["ts", "Open", "High", "Low", "Close", "Volume"]).drop_duplicates("ts").sort_values("ts")
            df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert(CN_TZ).dt.tz_localize(None)
            return df, ex_name
        except Exception as e:
            errors.append(f"{ex_name}: {e}")
    raise RuntimeError(" ; ".join(errors))


def _apply_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ema89"] = d["Close"].ewm(span=89, adjust=False).mean()
    d["ema144"] = d["Close"].ewm(span=144, adjust=False).mean()
    d["ema169"] = d["Close"].ewm(span=169, adjust=False).mean()
    d["ema20"] = d["Close"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["Close"].ewm(span=50, adjust=False).mean()

    ema12 = d["Close"].ewm(span=12, adjust=False).mean(); ema26 = d["Close"].ewm(span=26, adjust=False).mean()
    d["macd"] = ema12 - ema26; d["macd_signal"] = d["macd"].ewm(span=9, adjust=False).mean(); d["macd_hist"] = d["macd"] - d["macd_signal"]

    tr = pd.concat([(d["High"] - d["Low"]), (d["High"] - d["Close"].shift(1)).abs(), (d["Low"] - d["Close"].shift(1)).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14).mean().bfill()

    delta = d["Close"].diff(); gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean().replace(0, 1e-9)
    d["rsi14"] = 100 - 100 / (1 + gain / loss)

    d["vol_sma20"] = d["Volume"].rolling(20).mean(); d["vol_ratio"] = d["Volume"] / d["vol_sma20"].replace(0, np.nan)
    d["highest_20"] = d["High"].rolling(20).max(); d["lowest_20"] = d["Low"].rolling(20).min()
    d["obv"] = (np.sign(d["Close"].diff().fillna(0)) * d["Volume"]).cumsum(); d["obv_ema20"] = d["obv"].ewm(span=20, adjust=False).mean()
    return d


def _ema_cross_signal(row: pd.Series, prev: pd.Series) -> int:
    bull = prev["ema89"] <= prev["ema144"] and row["ema89"] > row["ema144"]
    bear = prev["ema89"] >= prev["ema144"] and row["ema89"] < row["ema144"]
    if bull and row["Close"] > row["ema89"] > row["ema144"] and row["Close"] > row["ema169"]:
        return 1
    if bear and row["Close"] < row["ema89"] < row["ema144"] and row["Close"] < row["ema169"]:
        return -1
    return 0


def _macd_divergence_points(df: pd.DataFrame, lookback: int = 60) -> Tuple[List[int], List[int]]:
    bull, bear = [], []
    for i in range(lookback + 5, len(df)):
        w = df.iloc[i - lookback : i + 1]
        highs = w["High"].rolling(5, center=True).max(); lows = w["Low"].rolling(5, center=True).min()
        ph = w.index[w["High"] == highs]; pl = w.index[w["Low"] == lows]
        if len(ph) >= 2:
            i1, i2 = ph[-2], ph[-1]
            if w.loc[i2, "High"] > w.loc[i1, "High"] and w.loc[i2, "macd"] < w.loc[i1, "macd"]:
                bear.append(i)
        if len(pl) >= 2:
            i1, i2 = pl[-2], pl[-1]
            if w.loc[i2, "Low"] < w.loc[i1, "Low"] and w.loc[i2, "macd"] > w.loc[i1, "macd"]:
                bull.append(i)
    return bull, bear


def build_signal_plan(df_4h_raw: pd.DataFrame) -> SignalPlan:
    d = _apply_indicators(df_4h_raw)
    row, prev = d.iloc[-2], d.iloc[-3]  # 已收盘4h
    cross = _ema_cross_signal(row, prev)
    bull_div, bear_div = _macd_divergence_points(d, lookback=60)
    idx = len(d) - 2
    div = -1 if idx in set(bear_div) else 1 if idx in set(bull_div) else 0
    entry = float(row["Close"])
    dist = max(float(row["atr14"]) * 1.2, entry * 0.004)
    if cross == 1:
        return SignalPlan("做多", "EMA89上穿EMA144且站上EMA169(4h收盘确认)", str(row["ts"]), entry, entry - dist, entry + dist * 1.8)
    if cross == -1:
        return SignalPlan("做空", "EMA89下穿EMA144且跌破EMA169(4h收盘确认)", str(row["ts"]), entry, entry + dist, entry - dist * 1.8)
    if div == -1:
        return SignalPlan("止盈", "4h MACD顶背离", str(row["ts"]), entry, entry, entry)
    if div == 1:
        return SignalPlan("止盈", "4h MACD底背离", str(row["ts"]), entry, entry, entry)
    return SignalPlan("空仓", "无新信号", str(row["ts"]), entry, entry, entry)


def maybe_log_signal(symbol: str, plan: SignalPlan, chart_timeframe: str, chart_ex: str, sig_ex: str, price: float) -> None:
    state = load_signal_state(); key = f"{symbol}:{SIGNAL_TIMEFRAME}"; last = state.get(key, {})
    if last.get("signal_ts") == plan.signal_ts and last.get("signal") == plan.signal:
        return
    append_live_log(
        f"[SIGNAL] {symbol} tf={SIGNAL_TIMEFRAME} signal={plan.signal} reason={plan.reason} "
        f"entry={plan.entry:.4f} sl={plan.stop_loss:.4f} tp={plan.take_profit:.4f} signal_ex={sig_ex} chart_tf={chart_timeframe} chart_ex={chart_ex} price={price:.4f}"
    )
    state[key] = {"signal": plan.signal, "signal_ts": plan.signal_ts, "entry": round(plan.entry, 6), "stop_loss": round(plan.stop_loss, 6), "take_profit": round(plan.take_profit, 6)}
    save_signal_state(state)


def _simulate_trades(df: pd.DataFrame, signal_fn, stop_mult: float, hold_max: int, bull_set: set[int], bear_set: set[int], fee: float = 0.0008) -> List[float]:
    """Position lifecycle backtest:
    - entry by strategy signal
    - exits by ATR stop / MACD divergence / reverse signal / timeout
    - reverse signal closes and immediately reopens opposite position on next bar open
    """
    rets: List[float] = []
    pos = 0
    entry = sl = 0.0
    entry_i = 0

    for i in range(200, len(df) - 1):
        row, prev, nxt = df.iloc[i], df.iloc[i - 1], df.iloc[i + 1]
        sig = signal_fn(df, i, row, prev)

        if pos == 0:
            if sig == 0:
                continue
            pos = sig
            entry = float(nxt["Open"])
            d = max(float(row["atr14"]) * stop_mult, entry * 0.004)
            sl = entry - d if pos == 1 else entry + d
            entry_i = i + 1
            continue

        exit_px = float(nxt["Close"])
        exit_flag = None
        reverse_to = 0

        # hard stop
        if pos == 1 and float(nxt["Low"]) <= sl:
            exit_flag, exit_px = "sl", sl
        elif pos == -1 and float(nxt["High"]) >= sl:
            exit_flag, exit_px = "sl", sl

        if exit_flag is None:
            div_sig = -1 if i in bear_set else 1 if i in bull_set else 0
            if div_sig == (-1 if pos == 1 else 1):
                exit_flag = "macd_div_tp"
            elif sig == -pos:
                exit_flag = "reverse"
                reverse_to = sig
            elif i - entry_i >= hold_max:
                exit_flag = "timeout"

        if exit_flag:
            rets.append(((exit_px - entry) / entry) * pos - fee)
            pos = 0

            if reverse_to != 0:
                pos = reverse_to
                entry = float(nxt["Open"])
                d = max(float(row["atr14"]) * stop_mult, entry * 0.004)
                sl = entry - d if pos == 1 else entry + d
                entry_i = i + 1

    return rets


def _metrics(returns: List[float], exposure: float = 1.0) -> dict:
    if not returns:
        return {"trades": 0, "win_rate": 0.0, "return_rate": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0}
    sr = pd.Series(np.array(returns) * exposure)
    eq = (1 + sr).cumprod(); dd = (eq - eq.cummax()) / eq.cummax()
    gain = float(sr[sr > 0].sum()); loss = float(abs(sr[sr <= 0].sum()))
    return {
        "trades": int(len(sr)),
        "win_rate": float((sr > 0).mean()),
        "return_rate": float(eq.iloc[-1] - 1),
        "profit_factor": float(gain / loss) if loss > 0 else float("inf"),
        "max_drawdown": float(dd.min()),
    }


def _exposure_for_dd(returns: List[float], dd_limit: float = 0.20) -> float:
    if not returns:
        return 0.0
    lo, hi = 0.01, 5.0
    for _ in range(28):
        mid = (lo + hi) / 2
        m = _metrics(returns, mid)
        if abs(m["max_drawdown"]) <= dd_limit:
            lo = mid
        else:
            hi = mid
    return round(lo, 4)


def _recommend_leverage_and_margin(exposure: float) -> tuple[float, float]:
    if exposure <= 0:
        return 1.0, 0.0
    # exposure ~= leverage * margin_pct
    # pick small leverage preference
    best = None
    for lev in [1, 2, 3, 4, 5, 6, 8, 10]:
        margin = min(exposure / lev, 1.0)
        eff = lev * margin
        gap = abs(eff - exposure) + lev * 0.01
        cand = (gap, float(lev), round(float(margin), 4))
        if best is None or cand < best:
            best = cand
    return best[1], best[2]


def _strategy_signal_factory(name: str):
    def sig_ema(df, i, row, prev):
        return _ema_cross_signal(row, prev)

    def sig_confluence(df, i, row, prev):
        ema_trend = 1 if row["ema20"] > row["ema50"] > row["ema89"] else -1 if row["ema20"] < row["ema50"] < row["ema89"] else 0
        macd = 1 if row["macd_hist"] > 0 else -1 if row["macd_hist"] < 0 else 0
        rsi = 1 if row["rsi14"] > 55 else -1 if row["rsi14"] < 45 else 0
        vol = 1 if row["vol_ratio"] > 1.2 and row["obv"] > row["obv_ema20"] else -1 if row["vol_ratio"] > 1.2 and row["obv"] < row["obv_ema20"] else 0
        score = ema_trend + macd + rsi + vol
        return 1 if score >= 3 else -1 if score <= -3 else 0

    def sig_breakout(df, i, row, prev):
        if row["Close"] > row["highest_20"] and row["vol_ratio"] > 1.3:
            return 1
        if row["Close"] < row["lowest_20"] and row["vol_ratio"] > 1.3:
            return -1
        return 0

    return {"ema_cross_89_144_169": sig_ema, "multi_confluence": sig_confluence, "volume_breakout": sig_breakout}[name]


def _profile_grid(profile: str):
    if profile == "deep":
        return [(s, h) for s in [0.9, 1.1, 1.3, 1.5, 1.8] for h in [24, 36, 48, 72, 96]]
    return [(s, h) for s in [1.0, 1.2, 1.4] for h in [24, 48, 72]]


def _backtest_engine(df: pd.DataFrame, profile: str) -> dict:
    strategies = ["ema_cross_89_144_169", "multi_confluence", "volume_breakout"]
    grid = _profile_grid(profile)
    out = {}
    bull_div, bear_div = _macd_divergence_points(df, lookback=60)
    bull_set, bear_set = set(bull_div), set(bear_div)

    for st in strategies:
        sig_fn = _strategy_signal_factory(st)
        best = None
        for (s, h) in grid:
            base_returns = _simulate_trades(df, sig_fn, s, h, bull_set, bear_set)
            exposure = _exposure_for_dd(base_returns, dd_limit=0.20)
            lev, margin = _recommend_leverage_and_margin(exposure)
            m = _metrics(base_returns, exposure)
            m["recommended_exposure"] = exposure
            m["recommended_leverage"] = lev
            m["recommended_margin_pct"] = margin
            m["params"] = {"stop_mult": s, "hold_bars_max": h}
            m["tested_param_sets"] = len(grid)
            if best is None or (m["return_rate"], m["profit_factor"], m["win_rate"]) > (best["return_rate"], best["profit_factor"], best["win_rate"]):
                best = m
        out[st] = best

    ranked = sorted(out.items(), key=lambda kv: (kv[1]["return_rate"], kv[1]["profit_factor"]), reverse=True)
    return {
        "strategies": {k: v for k, v in out.items()},
        "ranked": [{"strategy": k, **v} for k, v in ranked],
        "best": {"strategy": ranked[0][0], **ranked[0][1]},
    }


def _render_report_md(report: dict) -> str:
    lines = ["# 全历史4h回测报告", "", f"- 生成时间(中国时区): {report['generated_at_cn']}", f"- 分析强度: {report['profile']}", "- 回撤约束: 目标最大回撤 <= 20%，通过历史收益序列反推推荐杠杆和仓位", ""]
    for sym, d in report["symbols"].items():
        lines += [f"## {sym}", f"- 数据源: {d['exchange']}", f"- K线数量: {d['rows']}", f"- 时间范围: {d['from']} -> {d['to']}", "", "| 策略 | 参数组合数 | 推荐杠杆 | 推荐仓位 | 交易次数 | 胜率 | 收益率 | 盈亏比(PF) | 最大回撤 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for item in d["engine"]["ranked"]:
            lines.append(f"| {item['strategy']} | {item['tested_param_sets']} | {item['recommended_leverage']:.1f}x | {item['recommended_margin_pct']:.2%} | {item['trades']} | {item['win_rate']:.2%} | {item['return_rate']:.2%} | {item['profit_factor']:.3f} | {item['max_drawdown']:.2%} |")
        lines.append("")
    return "\n".join(lines)


def _analyze_symbol(symbol: str, profile: str) -> dict:
    df, ex_name = fetch_full_ohlcv(symbol, timeframe="4h")
    di = _apply_indicators(df)
    engine = _backtest_engine(di, profile)
    return {"exchange": ex_name, "rows": int(len(di)), "from": str(di.iloc[0]["ts"]), "to": str(di.iloc[-1]["ts"]), "engine": engine}


def _run_backtest_job(symbol_choice: str, profile: str) -> None:
    save_json(BACKTEST_STATUS_FILE, {"running": True, "status": "running", "stage": f"start {symbol_choice}/{profile}", "updated_at": datetime.now(CN_TZ).isoformat()})
    try:
        syms = ["BTC/USDT", "ETH/USDT"] if symbol_choice == "BOTH" else [symbol_choice]
        report = {"generated_at_cn": datetime.now(CN_TZ).isoformat(), "profile": profile, "signal_timeframe": "4h", "symbols": {}}
        for s in syms:
            save_json(BACKTEST_STATUS_FILE, {"running": True, "status": "running", "stage": f"analyzing {s}", "updated_at": datetime.now(CN_TZ).isoformat()})
            report["symbols"][s] = _analyze_symbol(s, profile)
        save_json(BACKTEST_REPORT_FILE, report)
        BACKTEST_REPORT_MD_FILE.write_text(_render_report_md(report), encoding="utf-8")
        save_json(BACKTEST_STATUS_FILE, {"running": False, "status": "done", "stage": "完成", "report_file": str(BACKTEST_REPORT_FILE), "report_md_file": str(BACKTEST_REPORT_MD_FILE), "updated_at": datetime.now(CN_TZ).isoformat()})
        append_live_log("全历史回测完成")
    except Exception as e:
        save_json(BACKTEST_STATUS_FILE, {"running": False, "status": "failed", "stage": "异常", "error": str(e), "updated_at": datetime.now(CN_TZ).isoformat()})
        append_live_log(f"回测失败: {e}")


@app.get("/")
def index():
    symbol = request.args.get("symbol", SYMBOLS[0]); timeframe = request.args.get("timeframe", "15m")
    if symbol not in SYMBOLS: symbol = SYMBOLS[0]
    if timeframe not in TIMEFRAMES: timeframe = "15m"
    latest = RUNTIME / "latest_signal.json"
    payload = latest.read_text(encoding="utf-8") if latest.exists() else "暂无数据"
    return render_template_string(HTML, payload=payload, history_count=get_history_count(), msg=request.args.get("msg", ""), symbol=symbol, timeframe=timeframe, symbols=SYMBOLS, timeframes=TIMEFRAMES, live_log=tail_live_log())


@app.get("/api/market_status")
def api_market_status():
    symbol = request.args.get("symbol", SYMBOLS[0]); tf = request.args.get("timeframe", "15m")
    try:
        chart_df, chart_ex = fetch_market(symbol, tf, 600, ttl_s=4.0)
        sig_df, sig_ex = fetch_market(symbol, SIGNAL_TIMEFRAME, 1800, ttl_s=20.0)
        plan = build_signal_plan(sig_df)
        px = float(chart_df.iloc[-1]["Close"])
        maybe_log_signal(symbol, plan, tf, chart_ex, sig_ex, px)
        return jsonify({"ok": True, "symbol": symbol, "chart_timeframe": tf, "signal_timeframe": SIGNAL_TIMEFRAME, "signal_ts": plan.signal_ts, "price": round(px, 2), "signal": plan.signal, "reason": plan.reason, "entry": round(plan.entry, 2), "stop_loss": round(plan.stop_loss, 2), "take_profit": round(plan.take_profit, 2), "exchange_chart": chart_ex, "exchange_signal": sig_ex})
    except Exception as e:
        append_live_log(f"ERROR market_status: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/backtest_status")
def api_backtest_status():
    s = load_json(BACKTEST_STATUS_FILE, {"running": False, "status": "idle", "updated_at": None})
    if BACKTEST_REPORT_FILE.exists(): s["report"] = load_json(BACKTEST_REPORT_FILE, {})
    if BACKTEST_REPORT_MD_FILE.exists(): s["report_markdown"] = BACKTEST_REPORT_MD_FILE.read_text(encoding="utf-8")
    return jsonify(s)


@app.get("/api/live_log_tail")
def api_live_log_tail():
    return tail_live_log()


@app.post("/run_backtest")
def run_backtest():
    symbol = request.args.get("symbol", SYMBOLS[0]); timeframe = request.args.get("timeframe", "15m")
    selected = request.form.get("backtest_symbol", "BOTH"); profile = request.form.get("profile", "deep")
    if selected not in {"BTC/USDT", "ETH/USDT", "BOTH"}: selected = "BOTH"
    if profile not in {"standard", "deep"}: profile = "deep"

    global BACKTEST_THREAD
    with BACKTEST_LOCK:
        if BACKTEST_THREAD is not None and BACKTEST_THREAD.is_alive():
            return redirect(url_for("index", msg="回测任务已在运行", symbol=symbol, timeframe=timeframe))
        BACKTEST_THREAD = threading.Thread(target=_run_backtest_job, args=(selected, profile), daemon=True)
        BACKTEST_THREAD.start()
    return redirect(url_for("index", msg=f"已启动回测 symbol={selected}, profile={profile}（固定4h内核）", symbol=symbol, timeframe=timeframe))


@app.post("/start_auto")
def start_auto():
    symbol = request.args.get("symbol", SYMBOLS[0]); timeframe = request.args.get("timeframe", "15m")
    run_proc("auto", auto_trade=True, symbol=symbol)
    append_live_log(f"启动自动下单 daemon symbol={symbol} signal_tf={SIGNAL_TIMEFRAME}")
    return redirect(url_for("index", msg="自动下单已启动（固定4h信号）", symbol=symbol, timeframe=timeframe))


@app.post("/stop_all")
def stop_all():
    symbol = request.args.get("symbol", SYMBOLS[0]); timeframe = request.args.get("timeframe", "15m")
    for _, pid in load_pids().items():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False) if os.name == "nt" else subprocess.run(["kill", "-9", str(pid)], check=False)
        except Exception:
            pass
    save_pids({}); append_live_log("停止所有后台任务")
    return redirect(url_for("index", msg="后台任务已停止", symbol=symbol, timeframe=timeframe))


@app.get("/chart")
def chart():
    symbol = request.args.get("symbol", SYMBOLS[0]); tf = request.args.get("timeframe", "15m")
    out = RUNTIME / "chart_live.html"
    try:
        df, ex = fetch_market(symbol, tf, 700, ttl_s=4.0)
        di = _apply_indicators(df)
        sig_df, _ = fetch_market(symbol, SIGNAL_TIMEFRAME, 1800, ttl_s=20.0)
        plan = build_signal_plan(sig_df)
        bull_div, bear_div = _macd_divergence_points(di, lookback=60)

        fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.62, 0.20, 0.18], vertical_spacing=0.03)
        fig.add_trace(go.Candlestick(x=di["ts"], open=di["Open"], high=di["High"], low=di["Low"], close=di["Close"], name=f"{symbol} {tf}"), row=1, col=1)
        fig.add_trace(go.Scatter(x=di["ts"], y=di["ema89"], name="EMA89", line=dict(color="white", width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=di["ts"], y=di["ema144"], name="EMA144", line=dict(color="yellow", width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=di["ts"], y=di["ema169"], name="EMA169", line=dict(color="blue", width=1.5)), row=1, col=1)
        fig.add_trace(go.Bar(x=di["ts"], y=di["Volume"], name="VOL", marker_color="#888"), row=2, col=1)
        fig.add_trace(go.Scatter(x=di["ts"], y=di["macd"], name="MACD", line=dict(color="#00bcd4", width=1.2)), row=3, col=1)
        fig.add_trace(go.Scatter(x=di["ts"], y=di["macd_signal"], name="MACD_SIGNAL", line=dict(color="#ff9800", width=1.0)), row=3, col=1)
        fig.add_trace(go.Bar(x=di["ts"], y=di["macd_hist"], name="MACD_HIST", marker_color="#9c27b0", opacity=0.4), row=3, col=1)

        if bull_div:
            fig.add_trace(go.Scatter(x=di.iloc[bull_div]["ts"], y=di.iloc[bull_div]["macd"], mode="markers", marker=dict(color="#00e676", size=8, symbol="triangle-up"), name="MACD底背离"), row=3, col=1)
        if bear_div:
            fig.add_trace(go.Scatter(x=di.iloc[bear_div]["ts"], y=di.iloc[bear_div]["macd"], mode="markers", marker=dict(color="#ff5252", size=8, symbol="triangle-down"), name="MACD顶背离"), row=3, col=1)

        cp = float(di.iloc[-1]["Close"])
        fig.add_hline(y=cp, line_dash="dot", line_color="#f7b500", annotation_text=f"当前价:{cp:.2f}", row=1, col=1)
        if plan.signal in {"做多", "做空", "止盈"}:
            fig.add_hline(y=plan.stop_loss, line_dash="dash", line_color="#ff4d4f", annotation_text="止损", row=1, col=1)
            fig.add_hline(y=plan.take_profit, line_dash="dash", line_color="#00c853", annotation_text="止盈", row=1, col=1)

        fig.update_layout(title=f"[{ex}] {symbol} {tf} 展示 | 运行策略固定4h", template="plotly_dark", dragmode="pan", margin=dict(l=20, r=20, t=60, b=20), modebar_add=["drawline", "drawopenpath", "drawrect", "drawcircle", "eraseshape"], modebar_remove=["lasso2d", "select2d"])
        fig.update_xaxes(rangeslider_visible=True, showspikes=True, spikemode="across", spikesnap="cursor")
        fig.update_yaxes(side="left", tickformat=".2f", row=1, col=1)

        out.parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(str(out), include_plotlyjs="cdn", config={"scrollZoom": True, "displaylogo": False, "doubleClick": "reset"})
        return send_file(out)
    except Exception as e:
        append_live_log(f"图表失败: {e}")
        return f"<html><body><h3>图表生成失败: {e}</h3></body></html>"


if __name__ == "__main__":
    try:
        app.run(host="127.0.0.1", port=8501)
    except Exception as exc:
        append_live_log(f"UI启动失败: {exc}")
        with UI_DEBUG_LOG.open("a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
        raise
