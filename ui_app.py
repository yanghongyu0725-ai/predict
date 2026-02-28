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
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

import ccxt
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

RUNTIME.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(UI_DEBUG_LOG, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("ui_app")
logger.info("UI app module initialized")

SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAMES = ["1m", "15m", "1h", "4h", "1d", "1w"]
SIGNAL_TIMEFRAME = "4h"
EXCHANGES = ["bybit", "okx", "binance"]
MARKET_CACHE: Dict[Tuple[str, str, int], Dict] = {}
BACKTEST_LOCK = threading.Lock()
BACKTEST_THREAD: threading.Thread | None = None

HTML = """
<!doctype html><html><head><meta charset='utf-8'><title>策略控制台</title>
<style>
body{font-family:Arial;margin:20px}
button{margin:4px;padding:8px 12px}
pre{background:#111;color:#0f0;padding:10px;white-space:pre-wrap;max-height:360px;overflow:auto}
.msg{padding:8px 12px;background:#eef;border:1px solid #99c;margin-bottom:10px}
iframe{width:100%;height:620px;border:1px solid #ddd}
.toolbar{margin-bottom:8px}
.pill{display:inline-block;padding:6px 10px;margin:2px;border:1px solid #aaa;border-radius:6px;text-decoration:none;color:#000}
.pill.active{background:#333;color:#fff}
.note{padding:8px 12px;background:#fff7d6;border:1px solid #d9b100;margin-bottom:10px}
.panel{padding:10px;border:1px solid #ddd;background:#fafafa;margin-bottom:12px}
</style></head>
<body>
<h2>交易策略控制台</h2>
<div class="note"><b>策略信号固定周期:</b> 4h（图表切换仅影响展示，不影响策略信号）</div>
{% if msg %}<div class="msg">{{msg}}</div>{% endif %}

<div class="toolbar">
  <b>标的:</b>
  {% for s in symbols %}
    <a class="pill {% if s==symbol %}active{% endif %}" href="/?symbol={{s}}&timeframe={{timeframe}}">{{s}}</a>
  {% endfor %}
</div>
<div class="toolbar">
  <b>图表周期:</b>
  {% for tf in timeframes %}
    <a class="pill {% if tf==timeframe %}active{% endif %}" href="/?symbol={{symbol}}&timeframe={{tf}}">{{tf}}</a>
  {% endfor %}
</div>

<div class="panel">
  <h3>长周期回测分析（BTC/ETH 4h，全历史）</h3>
  <form method='post' action='/run_backtest?symbol={{symbol}}&timeframe={{timeframe}}'>
    <button>启动全历史回测分析</button>
  </form>
  <pre id="backtest_status">加载中...</pre>
</div>

<h3>K线图（实时，增强交互）</h3>
<iframe src="/chart?symbol={{symbol}}&timeframe={{timeframe}}"></iframe>

<form method='post' action='/start_once?symbol={{symbol}}&timeframe={{timeframe}}'><button>运行一次策略（固定4h）</button></form>
<form method='post' action='/start_daemon?symbol={{symbol}}&timeframe={{timeframe}}'><button>开启持续测试(daemon, 固定4h)</button></form>
<form method='post' action='/start_auto?symbol={{symbol}}&timeframe={{timeframe}}'><button>开启自动下单(daemon, 固定4h)</button></form>
<form method='post' action='/stop_all?symbol={{symbol}}&timeframe={{timeframe}}'><button>停止后台任务</button></form>

<h3>实时状态（每10秒刷新）</h3>
<pre id="live_status">加载中...</pre>

<h3>历史记录条数: {{history_count}}</h3>
<h3>最新信号/持仓/策略统计</h3>
<pre>{{payload}}</pre>

<h3>实时日志窗口</h3>
<pre id="live_log">{{live_log}}</pre>

<script>
async function refreshLive(){
  const symbol = {{ symbol|tojson }};
  const timeframe = {{ timeframe|tojson }};
  const r = await fetch(`/api/market_status?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`);
  const data = await r.json();
  document.getElementById('live_status').textContent = JSON.stringify(data, null, 2);

  const l = await fetch('/api/live_log_tail');
  const txt = await l.text();
  document.getElementById('live_log').textContent = txt;

  const b = await fetch('/api/backtest_status');
  const backtestData = await b.json();
  document.getElementById('backtest_status').textContent = JSON.stringify(backtestData, null, 2);
}
refreshLive();
setInterval(refreshLive, 10000);
</script>
</body></html>
"""


def load_pids() -> dict:
    if not PIDS.exists():
        return {}
    return json.loads(PIDS.read_text(encoding="utf-8"))


def save_pids(data: dict) -> None:
    PIDS.parent.mkdir(parents=True, exist_ok=True)
    PIDS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_live_log(line: str) -> None:
    LIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with LIVE_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} {line}\n")


def tail_live_log(max_lines: int = 60) -> str:
    if not LIVE_LOG.exists():
        return "暂无实时日志"
    lines = LIVE_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-max_lines:])


def load_signal_state() -> dict:
    if not SIGNAL_STATE_FILE.exists():
        return {}
    try:
        return json.loads(SIGNAL_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_signal_state(state: dict) -> None:
    SIGNAL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SIGNAL_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_backtest_status() -> dict:
    if not BACKTEST_STATUS_FILE.exists():
        return {"running": False, "status": "idle", "updated_at": None}
    try:
        return json.loads(BACKTEST_STATUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"running": False, "status": "status_file_corrupted", "updated_at": None}


def save_backtest_status(payload: dict) -> None:
    payload["updated_at"] = datetime.utcnow().isoformat()
    BACKTEST_STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def maybe_log_signal(symbol: str, plan: dict, chart_timeframe: str, chart_ex: str, sig_ex: str, price: float) -> None:
    state = load_signal_state()
    key = f"{symbol}:{SIGNAL_TIMEFRAME}"
    current = {
        "signal": plan["signal"],
        "signal_ts": str(plan["signal_ts"]),
        "entry": round(float(plan["entry"]), 6),
        "stop_loss": round(float(plan["stop_loss"]), 6),
        "take_profit": round(float(plan["take_profit"]), 6),
    }
    if state.get(key) == current:
        return
    msg = (
        f"[SIGNAL] {symbol} tf={SIGNAL_TIMEFRAME} signal={plan['signal']} reason={plan['reason']} "
        f"entry={plan['entry']:.4f} sl={plan['stop_loss']:.4f} tp={plan['take_profit']:.4f} "
        f"signal_ex={sig_ex} chart_tf={chart_timeframe} chart_ex={chart_ex} price={price:.4f}"
    )
    append_live_log(msg)
    state[key] = current
    save_signal_state(state)


def get_history_count() -> int:
    db = RUNTIME / "history.db"
    if not db.exists():
        return 0
    con = sqlite3.connect(db)
    try:
        cur = con.execute("SELECT COUNT(1) FROM signal_history")
        return int(cur.fetchone()[0])
    finally:
        con.close()


def run_proc(name: str, auto_trade: bool, symbol: str) -> None:
    cmd = [sys.executable, "crypto_deep_strategy.py", "--symbol", symbol, "--daemon", "--interval_minutes", "15", "--output_dir", "runtime"]
    if auto_trade:
        cmd.append("--auto_trade")
    logger.info("Starting process name=%s auto_trade=%s symbol=%s", name, auto_trade, symbol)
    p = subprocess.Popen(cmd, cwd=ROOT)
    data = load_pids()
    data[name] = p.pid
    save_pids(data)


def _resolve_exchanges() -> list[str]:
    preferred = (os.getenv("PREFERRED_EXCHANGE", "").strip().lower())
    exchanges = [preferred] + EXCHANGES if preferred else EXCHANGES
    seen = set()
    out = []
    for ex in exchanges:
        if ex and ex not in seen:
            seen.add(ex)
            out.append(ex)
    return out


def fetch_market(symbol: str, timeframe: str, limit: int = 200, ttl_s: float = 0.0) -> Tuple[pd.DataFrame, str]:
    cache_key = (symbol, timeframe, limit)
    now = time.time()
    cached = MARKET_CACHE.get(cache_key)
    if cached and ttl_s > 0 and now - cached["ts"] <= ttl_s:
        return cached["df"].copy(), cached["exchange"]

    errors = []
    for ex_name in _resolve_exchanges():
        try:
            ex_class = getattr(ccxt, ex_name)
            ex = ex_class({"enableRateLimit": True})
            rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not rows:
                errors.append(f"{ex_name}: empty ohlcv")
                continue
            df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            MARKET_CACHE[cache_key] = {"ts": now, "df": df.copy(), "exchange": ex_name}
            logger.info("Fetched market data symbol=%s timeframe=%s exchange=%s rows=%s", symbol, timeframe, ex_name, len(df))
            return df, ex_name
        except Exception as e:
            logger.warning("Fetch market failed exchange=%s symbol=%s timeframe=%s error=%s", ex_name, symbol, timeframe, e)
            errors.append(f"{ex_name}: {e}")

    logger.error("All exchanges failed symbol=%s timeframe=%s errors=%s", symbol, timeframe, " ; ".join(errors))
    raise RuntimeError(" ; ".join(errors))


def _timeframe_ms(timeframe: str) -> int:
    unit = timeframe[-1]
    num = int(timeframe[:-1])
    if unit == "m":
        return num * 60 * 1000
    if unit == "h":
        return num * 60 * 60 * 1000
    if unit == "d":
        return num * 24 * 60 * 60 * 1000
    if unit == "w":
        return num * 7 * 24 * 60 * 60 * 1000
    raise ValueError(f"unsupported timeframe: {timeframe}")


def fetch_full_ohlcv(symbol: str, timeframe: str = "4h", per_call_limit: int = 1000) -> tuple[pd.DataFrame, str]:
    tf_ms = _timeframe_ms(timeframe)
    errors = []
    for ex_name in _resolve_exchanges():
        try:
            ex_class = getattr(ccxt, ex_name)
            ex = ex_class({"enableRateLimit": True})
            since = None
            all_rows = []
            rounds = 0
            while rounds < 400:
                rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=per_call_limit)
                if not rows:
                    break
                if all_rows and rows[0][0] <= all_rows[-1][0]:
                    rows = [r for r in rows if r[0] > all_rows[-1][0]]
                if not rows:
                    break
                all_rows.extend(rows)
                since = rows[-1][0] + tf_ms
                rounds += 1
                if len(rows) < per_call_limit:
                    break
                time.sleep(ex.rateLimit / 1000.0)

            if not all_rows:
                raise RuntimeError("empty ohlcv")
            df = pd.DataFrame(all_rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
            df = df.drop_duplicates(subset=["ts"]).sort_values("ts")
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            logger.info("Fetched full history symbol=%s timeframe=%s exchange=%s rows=%s", symbol, timeframe, ex_name, len(df))
            return df, ex_name
        except Exception as exc:
            errors.append(f"{ex_name}: {exc}")

    raise RuntimeError(" ; ".join(errors))


def _apply_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = out["Close"].ewm(span=20, adjust=False).mean()
    out["ema50"] = out["Close"].ewm(span=50, adjust=False).mean()
    out["ema200"] = out["Close"].ewm(span=200, adjust=False).mean()

    delta = out["Close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean().replace(0, 1e-9)
    rs = gain / loss
    out["rsi14"] = 100 - 100 / (1 + rs)

    ema12 = out["Close"].ewm(span=12, adjust=False).mean()
    ema26 = out["Close"].ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    tr = pd.concat([
        (out["High"] - out["Low"]),
        (out["High"] - out["Close"].shift(1)).abs(),
        (out["Low"] - out["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    out["atr14"] = tr.rolling(14).mean().bfill()
    out["highest_20"] = out["High"].rolling(20).max()
    out["lowest_20"] = out["Low"].rolling(20).min()
    out["ret"] = out["Close"].pct_change().fillna(0.0)
    return out


def _signal_for_strategy(row: pd.Series, strategy: str) -> int:
    if strategy == "ema_trend":
        if row["ema20"] > row["ema50"] > row["ema200"]:
            return 1
        if row["ema20"] < row["ema50"] < row["ema200"]:
            return -1
        return 0
    if strategy == "rsi_reversion":
        if row["rsi14"] < 30:
            return 1
        if row["rsi14"] > 70:
            return -1
        return 0
    if strategy == "macd_momentum":
        if row["macd_hist"] > 0:
            return 1
        if row["macd_hist"] < 0:
            return -1
        return 0
    if strategy == "breakout_20":
        if row["Close"] > row["highest_20"]:
            return 1
        if row["Close"] < row["lowest_20"]:
            return -1
        return 0
    if strategy == "hybrid_vote":
        votes = [
            _signal_for_strategy(row, "ema_trend"),
            _signal_for_strategy(row, "macd_momentum"),
            _signal_for_strategy(row, "rsi_reversion"),
        ]
        score = sum(votes)
        if score >= 2:
            return 1
        if score <= -2:
            return -1
        return 0
    return 0


def _backtest_strategy(df: pd.DataFrame, strategy: str, horizon: int = 6, fee: float = 0.0008, risk_reward: float = 1.8) -> dict:
    returns = []
    wins = 0
    trades = 0
    for i in range(220, len(df) - horizon):
        row = df.iloc[i]
        sig = _signal_for_strategy(row, strategy)
        if sig == 0:
            continue

        entry = float(row["Close"])
        stop_dist = max(float(row["atr14"]) * 1.2, entry * 0.006)
        sl = entry - stop_dist if sig == 1 else entry + stop_dist
        tp = entry + stop_dist * risk_reward if sig == 1 else entry - stop_dist * risk_reward

        fwd = df.iloc[i + 1 : i + 1 + horizon]
        exit_px = float(fwd.iloc[-1]["Close"])
        for _, rr in fwd.iterrows():
            px = float(rr["Close"])
            if (sig == 1 and px <= sl) or (sig == -1 and px >= sl):
                exit_px = sl
                break
            if (sig == 1 and px >= tp) or (sig == -1 and px <= tp):
                exit_px = tp
                break

        ret = ((exit_px - entry) / entry) * sig - fee
        returns.append(ret)
        trades += 1
        if ret > 0:
            wins += 1

    if trades == 0:
        return {"trades": 0, "win_rate": 0.0, "return_rate": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0}

    ser = pd.Series(returns)
    eq = (1 + ser).cumprod()
    dd = (eq - eq.cummax()) / eq.cummax()
    gain = float(ser[ser > 0].sum())
    loss = float(abs(ser[ser <= 0].sum()))

    return {
        "trades": int(trades),
        "win_rate": float(wins / trades),
        "return_rate": float(eq.iloc[-1] - 1),
        "profit_factor": float(gain / loss) if loss > 0 else float("inf"),
        "max_drawdown": float(dd.min()),
    }


def _analyze_symbol(symbol: str) -> dict:
    df, ex_name = fetch_full_ohlcv(symbol, timeframe="4h", per_call_limit=1000)
    dfi = _apply_indicators(df)
    strategies = ["ema_trend", "rsi_reversion", "macd_momentum", "breakout_20", "hybrid_vote"]
    metrics = {name: _backtest_strategy(dfi, name) for name in strategies}
    ranked = sorted(metrics.items(), key=lambda kv: (kv[1]["return_rate"], kv[1]["win_rate"]), reverse=True)
    return {
        "exchange": ex_name,
        "rows": int(len(df)),
        "from": str(df.iloc[0]["ts"]),
        "to": str(df.iloc[-1]["ts"]),
        "ranked": [{"strategy": k, **v} for k, v in ranked],
        "best": {"strategy": ranked[0][0], **ranked[0][1]} if ranked else None,
    }


def _run_backtest_job() -> None:
    save_backtest_status({"running": True, "status": "running", "stage": "准备拉取BTC/ETH全历史4h数据"})
    append_live_log("开始执行 BTC/ETH 全历史 4h 回测分析任务")
    try:
        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "timeframe": "4h",
            "symbols": {},
            "summary": {},
        }
        for sym in ["BTC/USDT", "ETH/USDT"]:
            save_backtest_status({"running": True, "status": "running", "stage": f"分析 {sym}"})
            report["symbols"][sym] = _analyze_symbol(sym)

        summary = {}
        for sym, data in report["symbols"].items():
            best = data.get("best") or {}
            summary[sym] = {
                "best_strategy": best.get("strategy"),
                "win_rate": best.get("win_rate"),
                "return_rate": best.get("return_rate"),
                "trades": best.get("trades"),
                "max_drawdown": best.get("max_drawdown"),
            }
        report["summary"] = summary
        BACKTEST_REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        save_backtest_status({"running": False, "status": "done", "stage": "完成", "report_file": str(BACKTEST_REPORT_FILE)})
        append_live_log("BTC/ETH 全历史4h回测任务完成")
    except Exception as exc:
        save_backtest_status({"running": False, "status": "failed", "stage": "异常", "error": str(exc)})
        append_live_log(f"回测任务失败: {exc}")


def build_signal_plan(df_4h: pd.DataFrame) -> dict:
    close = df_4h["Close"]
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    tr = pd.concat([
        (df_4h["High"] - df_4h["Low"]),
        (df_4h["High"] - close.shift(1)).abs(),
        (df_4h["Low"] - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean().bfill()

    entry = float(close.iloc[-1])
    atr = float(atr14.iloc[-1]) if len(atr14) else max(entry * 0.01, 1e-8)
    stop_dist = max(atr * 1.2, entry * 0.006)

    if ema20.iloc[-1] > ema50.iloc[-1]:
        signal, reason = "做多", "EMA20>EMA50(4h)"
        sl = entry - stop_dist
        tp = entry + stop_dist * 1.8
    elif ema20.iloc[-1] < ema50.iloc[-1]:
        signal, reason = "做空", "EMA20<EMA50(4h)"
        sl = entry + stop_dist
        tp = entry - stop_dist * 1.8
    else:
        signal, reason = "空仓", "EMA20≈EMA50(4h)"
        sl = entry
        tp = entry

    return {
        "signal": signal,
        "reason": reason,
        "entry": entry,
        "stop_loss": float(sl),
        "take_profit": float(tp),
        "signal_ts": df_4h.iloc[-1]["ts"],
    }


@app.get("/")
def index():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    if symbol not in SYMBOLS:
        symbol = SYMBOLS[0]
    if timeframe not in TIMEFRAMES:
        timeframe = TIMEFRAMES[1]

    latest = RUNTIME / "latest_signal.json"
    payload = latest.read_text(encoding="utf-8") if latest.exists() else "暂无数据（请先点击“运行一次策略”或开启daemon）"
    msg = request.args.get("msg", "")
    return render_template_string(
        HTML,
        payload=payload,
        history_count=get_history_count(),
        msg=msg,
        symbol=symbol,
        timeframe=timeframe,
        symbols=SYMBOLS,
        timeframes=TIMEFRAMES,
        live_log=tail_live_log(),
    )


@app.get('/api/market_status')
def api_market_status():
    symbol = request.args.get("symbol", "BTC/USDT")
    chart_timeframe = request.args.get("timeframe", "15m")
    try:
        chart_df, chart_ex = fetch_market(symbol, chart_timeframe, 180, ttl_s=4.0)
        sig_df, sig_ex = fetch_market(symbol, SIGNAL_TIMEFRAME, 320, ttl_s=10.0)
        plan = build_signal_plan(sig_df)
        price = float(chart_df.iloc[-1]["Close"])
        msg = (
            f"[{sig_ex}] {symbol} signal_tf={SIGNAL_TIMEFRAME} signal={plan['signal']} reason={plan['reason']} "
            f"entry={plan['entry']:.4f} sl={plan['stop_loss']:.4f} tp={plan['take_profit']:.4f} "
            f"| chart_tf={chart_timeframe} price={price:.4f} src={chart_ex}"
        )
        maybe_log_signal(symbol, plan, chart_timeframe, chart_ex, sig_ex, price)
        return jsonify({
            "ok": True,
            "symbol": symbol,
            "chart_timeframe": chart_timeframe,
            "signal_timeframe": SIGNAL_TIMEFRAME,
            "time": str(chart_df.iloc[-1]["ts"]),
            "price": price,
            "signal": plan["signal"],
            "reason": plan["reason"],
            "entry": plan["entry"],
            "stop_loss": plan["stop_loss"],
            "take_profit": plan["take_profit"],
            "exchange_chart": chart_ex,
            "exchange_signal": sig_ex,
            "message": msg,
        })
    except Exception as e:
        append_live_log(f"ERROR {symbol} 所有交易所不可用: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get('/api/backtest_status')
def api_backtest_status():
    status = load_backtest_status()
    if BACKTEST_REPORT_FILE.exists():
        try:
            status["report"] = json.loads(BACKTEST_REPORT_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            status["report_error"] = str(exc)
    return jsonify(status)


@app.get('/api/live_log_tail')
def api_live_log_tail():
    return tail_live_log()


@app.post('/run_backtest')
def run_backtest():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    global BACKTEST_THREAD
    with BACKTEST_LOCK:
        if BACKTEST_THREAD is not None and BACKTEST_THREAD.is_alive():
            return redirect(url_for("index", msg="回测任务已在运行，请稍后查看状态", symbol=symbol, timeframe=timeframe))
        BACKTEST_THREAD = threading.Thread(target=_run_backtest_job, daemon=True)
        BACKTEST_THREAD.start()
    return redirect(url_for("index", msg="已启动BTC/ETH全历史4h回测任务，结果将自动更新", symbol=symbol, timeframe=timeframe))


@app.post('/start_once')
def start_once():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    subprocess.Popen([sys.executable, "crypto_deep_strategy.py", "--symbol", symbol, "--output_dir", "runtime"], cwd=ROOT)
    append_live_log(f"触发单次策略运行 symbol={symbol} signal_tf={SIGNAL_TIMEFRAME}")
    return redirect(url_for("index", msg="已触发单次运行（策略固定4h），请稍后刷新查看结果", symbol=symbol, timeframe=timeframe))


@app.post('/start_daemon')
def start_daemon():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    run_proc("daemon", auto_trade=False, symbol=symbol)
    append_live_log(f"启动持续测试 daemon symbol={symbol} signal_tf={SIGNAL_TIMEFRAME}")
    return redirect(url_for("index", msg="持续测试已启动（策略固定4h）", symbol=symbol, timeframe=timeframe))


@app.post('/start_auto')
def start_auto():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    run_proc("auto", auto_trade=True, symbol=symbol)
    append_live_log(f"启动自动下单 daemon symbol={symbol} signal_tf={SIGNAL_TIMEFRAME}")
    return redirect(url_for("index", msg="自动下单 daemon 已启动（策略固定4h，请先配置API Key）", symbol=symbol, timeframe=timeframe))


@app.post('/stop_all')
def stop_all():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    data = load_pids()
    for _, pid in data.items():
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
            else:
                subprocess.run(["kill", "-9", str(pid)], check=False)
        except Exception:
            pass
    save_pids({})
    append_live_log("停止所有后台任务")
    return redirect(url_for("index", msg="后台任务已停止", symbol=symbol, timeframe=timeframe))


@app.get('/chart')
def chart():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    chart_file = RUNTIME / "chart_live.html"
    try:
        limit_map = {"1m": 300, "15m": 320, "1h": 320, "4h": 320, "1d": 220, "1w": 140}
        limit = limit_map.get(timeframe, 320)
        df, ex_name = fetch_market(symbol, timeframe, limit, ttl_s=4.0)
        sig_df, _ = fetch_market(symbol, SIGNAL_TIMEFRAME, 320, ttl_s=10.0)
        plan = build_signal_plan(sig_df)

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22], vertical_spacing=0.03)
        fig.add_trace(
            go.Candlestick(
                x=df["ts"],
                open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
                name=f"{symbol} {timeframe}",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(go.Bar(x=df["ts"], y=df["Volume"], name="Volume", marker_color="#888"), row=2, col=1)

        sig_ts = pd.to_datetime(plan["signal_ts"])
        nearest_idx = df[df["ts"] <= sig_ts].index
        if len(nearest_idx) > 0 and plan["signal"] in {"做多", "做空"}:
            i = nearest_idx[-1]
            marker_y = float(df.iloc[i]["Close"])
            marker_color = "#00ff7f" if plan["signal"] == "做多" else "#ff4d4f"
            marker_symbol = "triangle-up" if plan["signal"] == "做多" else "triangle-down"
            fig.add_trace(go.Scatter(
                x=[df.iloc[i]["ts"]],
                y=[marker_y],
                mode="markers+text",
                marker=dict(size=14, color=marker_color, symbol=marker_symbol),
                text=[f"{plan['signal']}\\nSL:{plan['stop_loss']:.2f}\\nTP:{plan['take_profit']:.2f}"],
                textposition="top center",
                name="策略信号(4h)",
            ), row=1, col=1)
            fig.add_hline(y=plan["stop_loss"], line_dash="dash", line_color="#ff4d4f", annotation_text="止损", row=1, col=1)
            fig.add_hline(y=plan["take_profit"], line_dash="dash", line_color="#00c853", annotation_text="止盈", row=1, col=1)

        fig.update_layout(
            title=f"[{ex_name}] {symbol} {timeframe} 图表（信号固定{SIGNAL_TIMEFRAME}）",
            template="plotly_dark",
            margin=dict(l=20, r=20, t=60, b=20),
            dragmode="pan",
            modebar_add=["drawline", "drawopenpath", "drawrect", "drawcircle", "eraseshape"],
            modebar_remove=["lasso2d", "select2d"],
        )
        fig.update_xaxes(
            rangeslider_visible=True,
            rangeselector=dict(
                buttons=[
                    dict(count=1, label="1D", step="day", stepmode="backward"),
                    dict(count=7, label="1W", step="day", stepmode="backward"),
                    dict(count=1, label="1M", step="month", stepmode="backward"),
                    dict(step="all", label="ALL"),
                ]
            ),
            showspikes=True,
            spikemode="across",
            spikesnap="cursor",
            spikedash="solid",
        )
        fig.update_yaxes(showspikes=True, spikemode="across", row=1, col=1)

        chart_file.parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(
            str(chart_file),
            include_plotlyjs="cdn",
            config={"scrollZoom": True, "displaylogo": False, "doubleClick": "reset"},
        )
        return send_file(chart_file)
    except Exception as e:
        append_live_log(f"图表生成失败 {symbol} {timeframe}: {e}")
        return f"<html><body style='font-family:Arial'><h3>图表生成失败: {e}</h3></body></html>"


if __name__ == "__main__":
    try:
        logger.info("Starting Flask server on 127.0.0.1:8501")
        app.run(host="127.0.0.1", port=8501)
    except Exception as exc:
        logger.exception("Flask server crashed: %s", exc)
        append_live_log(f"UI启动失败: {exc}")
        with UI_DEBUG_LOG.open("a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
        raise
