#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from flask import Flask, redirect, render_template_string, request, send_file, url_for

app = Flask(__name__)
ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
PIDS = ROOT / "runtime" / "ui_pids.json"

HTML = """
<!doctype html><html><head><meta charset='utf-8'><title>策略控制台</title>
<style>body{font-family:Arial;margin:20px}button{margin:4px;padding:8px 12px}pre{background:#111;color:#0f0;padding:10px;white-space:pre-wrap}</style></head>
<body>
<h2>交易策略控制台</h2>
<form method='post' action='/start_once'><button>运行一次策略</button></form>
<form method='post' action='/start_daemon'><button>开启持续测试(daemon)</button></form>
<form method='post' action='/start_auto'><button>开启自动下单(daemon)</button></form>
<form method='post' action='/stop_all'><button>停止后台任务</button></form>
<p><a href='/chart'>查看K线图</a></p>
<h3>最新信号/持仓/策略统计</h3>
<pre>{{payload}}</pre>
</body></html>
"""


def load_pids() -> dict:
    if not PIDS.exists():
        return {}
    return json.loads(PIDS.read_text(encoding="utf-8"))


def save_pids(data: dict) -> None:
    PIDS.parent.mkdir(parents=True, exist_ok=True)
    PIDS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_proc(name: str, auto_trade: bool) -> None:
    cmd = [sys.executable, "crypto_deep_strategy.py", "--daemon", "--interval_minutes", "15", "--output_dir", "runtime"]
    if auto_trade:
        cmd.append("--auto_trade")
    p = subprocess.Popen(cmd, cwd=ROOT)
    data = load_pids()
    data[name] = p.pid
    save_pids(data)


@app.get("/")
def index():
    latest = RUNTIME / "latest_signal.json"
    payload = latest.read_text(encoding="utf-8") if latest.exists() else "暂无数据"
    return render_template_string(HTML, payload=payload)


@app.post('/start_once')
def start_once():
    subprocess.Popen([sys.executable, "crypto_deep_strategy.py", "--output_dir", "runtime"], cwd=ROOT)
    return redirect(url_for("index"))


@app.post('/start_daemon')
def start_daemon():
    run_proc("daemon", auto_trade=False)
    return redirect(url_for("index"))


@app.post('/start_auto')
def start_auto():
    run_proc("auto", auto_trade=True)
    return redirect(url_for("index"))


@app.post('/stop_all')
def stop_all():
    data = load_pids()
    for _, pid in data.items():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
        except Exception:
            pass
    save_pids({})
    return redirect(url_for("index"))


@app.get('/chart')
def chart():
    chart_file = RUNTIME / "chart_15m.html"
    if not chart_file.exists():
        return "尚未生成图表，请先运行策略。"
    return send_file(chart_file)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8501)
