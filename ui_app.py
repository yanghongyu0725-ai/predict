#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Tuple

import ccxt
import pandas as pd
import plotly.graph_objects as go
from flask import Flask, jsonify, redirect, render_template_string, request, send_file, url_for

app = Flask(__name__)
ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
PIDS = RUNTIME / "ui_pids.json"
LIVE_LOG = RUNTIME / "ui_live.log"
UI_DEBUG_LOG = RUNTIME / "ui_app_debug.log"

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
EXCHANGES = ["binance", "bybit", "okx"]

HTML = """
<!doctype html><html><head><meta charset='utf-8'><title>策略控制台</title>
<style>
body{font-family:Arial;margin:20px}
button{margin:4px;padding:8px 12px}
pre{background:#111;color:#0f0;padding:10px;white-space:pre-wrap;max-height:320px;overflow:auto}
.msg{padding:8px 12px;background:#eef;border:1px solid #99c;margin-bottom:10px}
iframe{width:100%;height:520px;border:1px solid #ddd}
.toolbar{margin-bottom:8px}
.pill{display:inline-block;padding:6px 10px;margin:2px;border:1px solid #aaa;border-radius:6px;text-decoration:none;color:#000}
.pill.active{background:#333;color:#fff}
</style></head>
<body>
<h2>交易策略控制台</h2>
{% if msg %}<div class="msg">{{msg}}</div>{% endif %}

<div class="toolbar">
  <b>标的:</b>
  {% for s in symbols %}
    <a class="pill {% if s==symbol %}active{% endif %}" href="/?symbol={{s}}&timeframe={{timeframe}}">{{s}}</a>
  {% endfor %}
</div>
<div class="toolbar">
  <b>周期:</b>
  {% for tf in timeframes %}
    <a class="pill {% if tf==timeframe %}active{% endif %}" href="/?symbol={{symbol}}&timeframe={{tf}}">{{tf}}</a>
  {% endfor %}
</div>

<h3>K线图（固定显示）</h3>
<iframe src="/chart?symbol={{symbol}}&timeframe={{timeframe}}"></iframe>

<form method='post' action='/start_once?symbol={{symbol}}&timeframe={{timeframe}}'><button>运行一次策略</button></form>
<form method='post' action='/start_daemon?symbol={{symbol}}&timeframe={{timeframe}}'><button>开启持续测试(daemon)</button></form>
<form method='post' action='/start_auto?symbol={{symbol}}&timeframe={{timeframe}}'><button>开启自动下单(daemon)</button></form>
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


def fetch_market(symbol: str, timeframe: str, limit: int = 200) -> Tuple[pd.DataFrame, str]:
    preferred = (os.getenv("PREFERRED_EXCHANGE", "").strip().lower())
    exchanges = [preferred] + EXCHANGES if preferred else EXCHANGES
    seen = set()
    errors = []

    for ex_name in exchanges:
        if not ex_name or ex_name in seen:
            continue
        seen.add(ex_name)
        try:
            ex_class = getattr(ccxt, ex_name)
            ex = ex_class({"enableRateLimit": True})
            rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not rows:
                errors.append(f"{ex_name}: empty ohlcv")
                continue
            df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            logger.info("Fetched market data symbol=%s timeframe=%s exchange=%s rows=%s", symbol, timeframe, ex_name, len(df))
            return df, ex_name
        except Exception as e:
            logger.warning("Fetch market failed exchange=%s symbol=%s timeframe=%s error=%s", ex_name, symbol, timeframe, e)
            errors.append(f"{ex_name}: {e}")
            continue

    logger.error("All exchanges failed symbol=%s timeframe=%s errors=%s", symbol, timeframe, " ; ".join(errors))
    raise RuntimeError(" ; ".join(errors))


def quick_signal(df: pd.DataFrame) -> Tuple[str, str]:
    close = df["Close"]
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    if ema20.iloc[-1] > ema50.iloc[-1]:
        return "做多", "EMA20>EMA50"
    if ema20.iloc[-1] < ema50.iloc[-1]:
        return "做空", "EMA20<EMA50"
    return "空仓", "EMA20≈EMA50"


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
    timeframe = request.args.get("timeframe", "15m")
    try:
        df, ex_name = fetch_market(symbol, timeframe, 250)
        price = float(df.iloc[-1]["Close"])
        signal, reason = quick_signal(df)
        msg = f"[{ex_name}] {symbol} {timeframe} 当前价={price:.4f} 信号={signal} 原因={reason}"
        append_live_log(msg)
        return jsonify({
            "ok": True,
            "symbol": symbol,
            "timeframe": timeframe,
            "time": str(df.iloc[-1]["ts"]),
            "price": price,
            "signal": signal,
            "reason": reason,
            "exchange": ex_name,
            "message": msg,
        })
    except Exception as e:
        append_live_log(f"ERROR {symbol} {timeframe} 所有交易所不可用: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get('/api/live_log_tail')
def api_live_log_tail():
    return tail_live_log()


@app.post('/start_once')
def start_once():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    subprocess.Popen([sys.executable, "crypto_deep_strategy.py", "--symbol", symbol, "--output_dir", "runtime"], cwd=ROOT)
    append_live_log(f"触发单次策略运行 symbol={symbol}")
    return redirect(url_for("index", msg="已触发单次运行，请稍后刷新查看结果", symbol=symbol, timeframe=timeframe))


@app.post('/start_daemon')
def start_daemon():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    run_proc("daemon", auto_trade=False, symbol=symbol)
    append_live_log(f"启动持续测试 daemon symbol={symbol}")
    return redirect(url_for("index", msg="持续测试已启动（daemon）", symbol=symbol, timeframe=timeframe))


@app.post('/start_auto')
def start_auto():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    run_proc("auto", auto_trade=True, symbol=symbol)
    append_live_log(f"启动自动下单 daemon symbol={symbol}")
    return redirect(url_for("index", msg="自动下单 daemon 已启动（请先配置API Key）", symbol=symbol, timeframe=timeframe))


@app.post('/stop_all')
def stop_all():
    symbol = request.args.get("symbol", "BTC/USDT")
    timeframe = request.args.get("timeframe", "15m")
    data = load_pids()
    for _, pid in data.items():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
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
        df, ex_name = fetch_market(symbol, timeframe, 400)
        fig = go.Figure(data=[
            go.Candlestick(
                x=df["ts"],
                open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
                name=f"{symbol} {timeframe}",
            )
        ])
        fig.update_layout(title=f"[{ex_name}] {symbol} {timeframe} 实时K线", xaxis_rangeslider_visible=False, template="plotly_dark")
        chart_file.parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(str(chart_file), include_plotlyjs="cdn")
        return send_file(chart_file)
    except Exception as e:
        append_live_log(f"图表生成失败 {symbol} {timeframe}（Binance可能受限，已尝试Bybit/OKX）: {e}")
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
