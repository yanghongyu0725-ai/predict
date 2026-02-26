#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import ccxt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from sklearn.preprocessing import StandardScaler
from tensorflow.keras import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.optimizers import Adam


@dataclass
class StrategyParams:
    symbol: str = "BTC/USDT"
    sequence_len: int = 64
    predict_horizon: int = 6
    threshold_long: float = 0.60
    threshold_short: float = 0.40
    risk_reward: float = 1.6
    stop_atr_mult: float = 1.3
    max_risk_per_trade: float = 0.05


@dataclass
class EngineConfig:
    output_dir: str = "runtime"
    heartbeat_seconds: int = 30
    max_retries: int = 3
    retry_delay_seconds: int = 2


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    rs = up.ewm(alpha=1 / period, adjust=False).mean() / down.ewm(alpha=1 / period, adjust=False).mean().replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
    dif = ema(close, 12) - ema(close, 26)
    dea = ema(dif, 9)
    return dif, dea, dif - dea


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"] - df["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=df.index)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"] - df["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    trn = tr.rolling(period).sum().replace(0, np.nan)
    pdi = 100 * plus_dm.rolling(period).sum() / trn
    mdi = 100 * minus_dm.rolling(period).sum() / trn
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.rolling(period).mean()


def obv(close: pd.Series, vol: pd.Series) -> pd.Series:
    return (np.sign(close.diff().fillna(0)) * vol).fillna(0).cumsum()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema20"] = ema(out["Close"], 20)
    out["ema50"] = ema(out["Close"], 50)
    out["ema200"] = ema(out["Close"], 200)
    _, _, hist = macd(out["Close"])
    out["macd_hist"] = hist
    out["rsi14"] = rsi(out["Close"], 14)
    out["atr14"] = atr(out, 14)
    out["adx14"] = adx(out, 14)
    out["obv"] = obv(out["Close"], out["Volume"])
    out["obv_ema20"] = ema(out["obv"], 20)
    out["vol_sma20"] = out["Volume"].rolling(20).mean()
    out["vol_ratio"] = out["Volume"] / out["vol_sma20"].replace(0, np.nan)
    return out


def get_exchange(args: argparse.Namespace, with_keys: bool = False) -> ccxt.binance:
    cfg = {"enableRateLimit": True}
    if with_keys:
        cfg["apiKey"] = os.getenv("BINANCE_API_KEY", "")
        cfg["secret"] = os.getenv("BINANCE_API_SECRET", "")
    ex = ccxt.binance(cfg)
    if args.testnet:
        ex.set_sandbox_mode(True)
    return ex


def fetch_ohlcv(ex: ccxt.binance, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    rows = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert(None)
    return df.set_index("ts").sort_index().astype(float)


def load_features(ex: ccxt.binance, symbol: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    h4 = add_indicators(fetch_ohlcv(ex, symbol, "4h", 1500)).add_prefix("h4_")
    d1 = add_indicators(fetch_ohlcv(ex, symbol, "1d", 1200)).add_prefix("d_")
    w1 = add_indicators(fetch_ohlcv(ex, symbol, "1w", 600)).add_prefix("w_")
    m15 = add_indicators(fetch_ohlcv(ex, symbol, "15m", 2000)).add_prefix("m15_")
    if h4.empty or d1.empty or w1.empty or m15.empty:
        return pd.DataFrame(), pd.DataFrame()

    feat4 = pd.concat([h4, d1.reindex(h4.index, method="ffill"), w1.reindex(h4.index, method="ffill")], axis=1).dropna()
    feat15 = pd.concat([m15, h4.reindex(m15.index, method="ffill"), d1.reindex(m15.index, method="ffill"), w1.reindex(m15.index, method="ffill")], axis=1).dropna()
    return feat4, feat15


def build_dataset(feat4: pd.DataFrame, seq_len: int, horizon: int) -> Tuple[np.ndarray, np.ndarray, List[pd.Timestamp]]:
    future_ret = feat4["h4_Close"].shift(-horizon) / feat4["h4_Close"] - 1
    y = (future_ret > 0).astype(int)
    cols = [
        "h4_Close", "h4_ema20", "h4_ema50", "h4_ema200", "h4_macd_hist", "h4_rsi14", "h4_adx14", "h4_atr14", "h4_vol_ratio",
        "d_ema20", "d_ema50", "d_ema200", "d_macd_hist", "d_rsi14", "d_adx14",
        "w_ema20", "w_ema50", "w_macd_hist", "w_rsi14",
    ]
    xdf = feat4[cols].dropna()
    y = y.loc[xdf.index]
    x_scaled = StandardScaler().fit_transform(xdf)

    xs, ys, idx = [], [], []
    for i in range(seq_len, len(x_scaled) - horizon):
        xs.append(x_scaled[i - seq_len : i])
        ys.append(y.iloc[i])
        idx.append(xdf.index[i])
    return np.asarray(xs, np.float32), np.asarray(ys, np.float32), idx


def build_model(input_shape: Tuple[int, int]) -> Sequential:
    model = Sequential([
        LSTM(64, return_sequences=True, input_shape=input_shape),
        Dropout(0.25),
        LSTM(32),
        Dropout(0.25),
        Dense(16, activation="relu"),
        Dense(1, activation="sigmoid"),
    ])
    model.compile(optimizer=Adam(0.001), loss="binary_crossentropy", metrics=["accuracy"])
    return model


def signal_ema(row: pd.Series, p: str = "h4") -> int:
    if row[f"{p}_ema20"] > row[f"{p}_ema50"] > row[f"{p}_ema200"]:
        return 1
    if row[f"{p}_ema20"] < row[f"{p}_ema50"] < row[f"{p}_ema200"]:
        return -1
    return 0


def signal_rsi(row: pd.Series, p: str = "h4") -> int:
    if row[f"{p}_rsi14"] > 55:
        return 1
    if row[f"{p}_rsi14"] < 45:
        return -1
    return 0


def signal_macd(row: pd.Series, p: str = "h4") -> int:
    return 1 if row[f"{p}_macd_hist"] > 0 else -1 if row[f"{p}_macd_hist"] < 0 else 0


def signal_adx_trend(row: pd.Series, p: str = "h4") -> int:
    if row[f"{p}_adx14"] < 18:
        return 0
    return signal_ema(row, p)


def signal_volume_obv(row: pd.Series, p: str = "h4") -> int:
    if row[f"{p}_vol_ratio"] <= 1.0:
        return 0
    return 1 if row[f"{p}_obv"] > row[f"{p}_obv_ema20"] else -1


def signal_multi_confluence(row: pd.Series) -> int:
    cond_long = (
        signal_ema(row, "h4") == 1 and signal_macd(row, "h4") == 1 and signal_rsi(row, "h4") == 1 and
        signal_volume_obv(row, "h4") == 1 and signal_macd(row, "w") == 1
    )
    cond_short = (
        signal_ema(row, "h4") == -1 and signal_macd(row, "h4") == -1 and signal_rsi(row, "h4") == -1 and
        signal_volume_obv(row, "h4") == -1 and signal_macd(row, "w") == -1
    )
    if cond_long:
        return 1
    if cond_short:
        return -1
    return 0


def signal_lstm_confluence(row: pd.Series) -> int:
    if row["model_prob"] >= row["th_long"] and signal_multi_confluence(row) == 1:
        return 1
    if row["model_prob"] <= row["th_short"] and signal_multi_confluence(row) == -1:
        return -1
    return 0


def evaluate_strategy(data: pd.DataFrame, signal_fn: Callable[[pd.Series], int], horizon: int, risk_reward: float, stop_atr_mult: float) -> Dict[str, float]:
    rets = []
    for i in range(len(data) - horizon):
        row = data.iloc[i]
        sig = signal_fn(row)
        if sig == 0:
            continue
        entry = row["h4_Close"]
        stop_dist = max(row["h4_atr14"] * stop_atr_mult, entry * 0.005)
        sl = entry - stop_dist if sig == 1 else entry + stop_dist
        tp = entry + stop_dist * risk_reward if sig == 1 else entry - stop_dist * risk_reward
        fwd = data.iloc[i + 1 : i + 1 + horizon]
        exit_price = fwd.iloc[-1]["h4_Close"]
        for _, rr in fwd.iterrows():
            px = rr["h4_Close"]
            if (sig == 1 and px <= sl) or (sig == -1 and px >= sl):
                exit_price = sl
                break
            if (sig == 1 and px >= tp) or (sig == -1 and px <= tp):
                exit_price = tp
                break
        rets.append(((exit_price - entry) / entry) * sig)

    if not rets:
        return {"trades": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "return_rate": 0.0, "max_drawdown": 0.0}

    tr = pd.Series(rets)
    eq = (1 + tr).cumprod()
    dd = (eq - eq.cummax()) / eq.cummax()
    wins = tr[tr > 0].sum()
    losses = tr[tr <= 0].sum()
    return {
        "trades": float(len(tr)),
        "win_rate": float((tr > 0).mean()),
        "profit_factor": float(wins / abs(losses)) if losses != 0 else float("inf"),
        "return_rate": float(eq.iloc[-1] - 1),
        "max_drawdown": float(dd.min()),
    }


def latest_15m_trigger(feat15: pd.DataFrame, direction: int, lookback: int = 300) -> Tuple[str, float]:
    if direction == 0:
        return "无触发", float("nan")

    def sig15(r: pd.Series) -> int:
        if signal_ema(r, "m15") == 1 and signal_macd(r, "m15") == 1 and signal_rsi(r, "m15") == 1 and signal_volume_obv(r, "m15") == 1:
            return 1
        if signal_ema(r, "m15") == -1 and signal_macd(r, "m15") == -1 and signal_rsi(r, "m15") == -1 and signal_volume_obv(r, "m15") == -1:
            return -1
        return 0

    recent = feat15.tail(lookback).copy()
    recent["s15"] = recent.apply(sig15, axis=1)
    hit = recent[recent["s15"] == direction]
    if hit.empty:
        return "无触发", float("nan")
    return str(hit.index[-1]), float(hit.iloc[-1]["m15_Close"])


def create_chart(feat15: pd.DataFrame, output_path: Path, symbol: str) -> None:
    d = feat15.tail(400)
    fig = go.Figure(data=[go.Candlestick(x=d.index, open=d["m15_Open"], high=d["m15_High"], low=d["m15_Low"], close=d["m15_Close"], name="15m")])
    fig.add_trace(go.Scatter(x=d.index, y=d["m15_ema20"], name="EMA20"))
    fig.add_trace(go.Scatter(x=d.index, y=d["m15_ema50"], name="EMA50"))
    fig.update_layout(title=f"{symbol} 15m K线", template="plotly_dark", xaxis_rangeslider_visible=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(output_path), include_plotlyjs="cdn")


def append_jsonl(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def safe_api_call(fn: Callable, cfg: EngineConfig):
    last_err: Optional[Exception] = None
    for _ in range(cfg.max_retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            time.sleep(cfg.retry_delay_seconds)
    raise RuntimeError(f"API调用失败(重试{cfg.max_retries}次): {last_err}")


def calculate_order_qty_with_exchange_rules(ex: ccxt.binance, symbol: str, entry: float, stop_loss: float, max_risk_fraction: float, usdt_free: float) -> float:
    stop_pct = max(abs(entry - stop_loss) / entry, 1e-6)
    max_loss_usdt = usdt_free * max_risk_fraction
    raw_qty = max_loss_usdt / (entry * stop_pct)

    market = ex.market(symbol)
    amount_min = market.get("limits", {}).get("amount", {}).get("min") or 0
    cost_min = market.get("limits", {}).get("cost", {}).get("min") or 0
    precision = market.get("precision", {}).get("amount", 6)

    qty = max(raw_qty, float(amount_min))
    min_by_cost = cost_min / entry if cost_min else 0
    qty = max(qty, min_by_cost)

    step = 10 ** (-precision) if isinstance(precision, int) else 1e-6
    qty = math.floor(qty / step) * step
    qty = float(ex.amount_to_precision(symbol, qty))
    if qty <= 0:
        raise RuntimeError("计算出的下单数量无效(<=0)")
    return qty


def place_order_industrial(plan: Dict[str, float | str], params: StrategyParams, args: argparse.Namespace, cfg: EngineConfig) -> Dict:
    state = {"timestamp": datetime.utcnow().isoformat(), "state": "SKIPPED", "reason": "", "order_id": None, "protective": {}}
    if not args.auto_trade:
        state["reason"] = "auto_trade_disabled"
        return state
    if plan["direction"] not in {"做多", "做空"}:
        state["reason"] = "no_trade_signal"
        return state

    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    if not api_key or not api_secret:
        state["reason"] = "missing_api_keys"
        return state

    ex = get_exchange(args, with_keys=True)
    safe_api_call(ex.load_markets, cfg)
    usdt_free = float(safe_api_call(lambda: ex.fetch_balance().get("USDT", {}).get("free", 0.0), cfg))
    if usdt_free <= 0:
        state["reason"] = "insufficient_balance"
        return state

    entry = float(plan["entry"])
    stop_loss = float(plan["stop_loss"])
    take_profit = float(plan["take_profit"])
    qty = calculate_order_qty_with_exchange_rules(ex, params.symbol, entry, stop_loss, params.max_risk_per_trade, usdt_free)

    side = "buy" if plan["direction"] == "做多" else "sell"
    order_type = args.order_type
    main_order = safe_api_call(lambda: ex.create_order(params.symbol, order_type, side, qty, None if order_type == "market" else entry), cfg)

    state["state"] = "MAIN_ORDER_FILLED_OR_ACCEPTED"
    state["order_id"] = main_order.get("id")
    state["main_order"] = main_order
    state["position"] = {"symbol": params.symbol, "side": side, "qty": qty, "entry": entry}

    reduce_side = "sell" if side == "buy" else "buy"
    try:
        sl_order = safe_api_call(lambda: ex.create_order(params.symbol, "stop_market", reduce_side, qty, None, {"stopPrice": stop_loss, "reduceOnly": True}), cfg)
        state["protective"]["stop_loss"] = {"status": "ok", "order": sl_order}
    except Exception as e:
        state["protective"]["stop_loss"] = {"status": "failed", "error": str(e)}

    try:
        tp_order = safe_api_call(lambda: ex.create_order(params.symbol, "take_profit_market", reduce_side, qty, None, {"stopPrice": take_profit, "reduceOnly": True}), cfg)
        state["protective"]["take_profit"] = {"status": "ok", "order": tp_order}
    except Exception as e:
        state["protective"]["take_profit"] = {"status": "failed", "error": str(e)}

    return state


def select_best_strategy(results: Dict[str, Dict[str, float]], latest_row: pd.Series) -> Tuple[str, int]:
    positive = {k: v for k, v in results.items() if v["return_rate"] > 0}
    if not positive:
        return "禁用(全部策略历史收益<=0)", 0
    best_name = max(positive.keys(), key=lambda n: positive[n]["return_rate"])
    signal_map: Dict[str, Callable[[pd.Series], int]] = {
        "EMA单指标": lambda r: signal_ema(r, "h4"),
        "RSI单指标": lambda r: signal_rsi(r, "h4"),
        "MACD单指标": lambda r: signal_macd(r, "h4"),
        "ADX趋势": lambda r: signal_adx_trend(r, "h4"),
        "量能OBV": lambda r: signal_volume_obv(r, "h4"),
        "多指标共振": signal_multi_confluence,
        "LSTM+多指标共振": signal_lstm_confluence,
    }
    return best_name, signal_map[best_name](latest_row)


def run_once(args: argparse.Namespace, params: StrategyParams, cfg: EngineConfig) -> Dict:
    ex = get_exchange(args, with_keys=False)
    feat4, feat15 = load_features(ex, params.symbol)
    if feat4.empty or feat15.empty:
        raise RuntimeError("数据拉取失败")

    x, y, idx = build_dataset(feat4, params.sequence_len, params.predict_horizon)
    if len(x) < 200:
        raise RuntimeError("可训练样本不足")

    split = int(len(x) * 0.8)
    model = build_model((x.shape[1], x.shape[2]))
    model.fit(x[:split], y[:split], epochs=args.epochs, batch_size=32, verbose=0, validation_split=0.2)
    probs = model.predict(x[split:], verbose=0).reshape(-1)

    eval_df = feat4.loc[idx[split:]].copy()
    eval_df["model_prob"] = probs
    eval_df["th_long"] = params.threshold_long
    eval_df["th_short"] = params.threshold_short

    strategies: Dict[str, Callable[[pd.Series], int]] = {
        "EMA单指标": lambda r: signal_ema(r, "h4"),
        "RSI单指标": lambda r: signal_rsi(r, "h4"),
        "MACD单指标": lambda r: signal_macd(r, "h4"),
        "ADX趋势": lambda r: signal_adx_trend(r, "h4"),
        "量能OBV": lambda r: signal_volume_obv(r, "h4"),
        "多指标共振": signal_multi_confluence,
        "LSTM+多指标共振": signal_lstm_confluence,
    }
    results = {name: evaluate_strategy(eval_df, fn, params.predict_horizon, params.risk_reward, params.stop_atr_mult) for name, fn in strategies.items()}

    latest = eval_df.iloc[-1]
    best_strategy_name, sig = select_best_strategy(results, latest)
    direction = "做多" if sig == 1 else "做空" if sig == -1 else "空仓"

    entry = float(latest["h4_Close"])
    stop_dist = max(float(latest["h4_atr14"]) * params.stop_atr_mult, entry * 0.005)
    sl = entry - stop_dist if sig == 1 else entry + stop_dist if sig == -1 else float("nan")
    tp = entry + stop_dist * params.risk_reward if sig == 1 else entry - stop_dist * params.risk_reward if sig == -1 else float("nan")
    t15, p15 = latest_15m_trigger(feat15, sig)

    plan = {
        "timestamp": datetime.utcnow().isoformat(),
        "symbol": params.symbol,
        "selected_strategy": best_strategy_name,
        "model_prob_up": float(latest["model_prob"]),
        "direction": direction,
        "signal_time_4h": str(eval_df.index[-1]),
        "trigger_time_15m": t15,
        "trigger_price_15m": p15,
        "entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "risk_reward": params.risk_reward,
    }

    execution = place_order_industrial(plan, params, args, cfg)

    chart_path = Path(cfg.output_dir) / "chart_15m.html"
    create_chart(feat15, chart_path, params.symbol)

    payload = {"plan": plan, "strategies": results, "execution": execution, "chart": str(chart_path)}
    return payload




def write_history_sqlite(payload: Dict, cfg: EngineConfig) -> None:
    db = Path(cfg.output_dir) / "history.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT,
                direction TEXT,
                model_prob_up REAL,
                entry REAL,
                stop_loss REAL,
                take_profit REAL,
                risk_reward REAL,
                trigger_time_15m TEXT,
                raw_json TEXT NOT NULL
            )
            """
        )
        plan = payload.get("plan", {})
        con.execute(
            """
            INSERT INTO signal_history (
                ts, symbol, strategy, direction, model_prob_up, entry, stop_loss, take_profit,
                risk_reward, trigger_time_15m, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.get("timestamp"),
                plan.get("symbol"),
                plan.get("selected_strategy"),
                plan.get("direction"),
                plan.get("model_prob_up"),
                plan.get("entry"),
                plan.get("stop_loss"),
                plan.get("take_profit"),
                plan.get("risk_reward"),
                plan.get("trigger_time_15m"),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        con.commit()
    finally:
        con.close()


def write_runtime_files(payload: Dict, cfg: EngineConfig) -> None:
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest_signal.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "strategy_metrics_latest.json").write_text(json.dumps(payload.get("strategies", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    append_jsonl(out_dir / "signal_history.jsonl", payload)
    write_history_sqlite(payload, cfg)


def run_daemon(args: argparse.Namespace, params: StrategyParams, cfg: EngineConfig) -> None:
    next_heartbeat = time.time()
    while True:
        now = time.time()
        if now >= next_heartbeat:
            append_jsonl(Path(cfg.output_dir) / "heartbeat.jsonl", {"timestamp": datetime.utcnow().isoformat(), "status": "alive"})
            next_heartbeat = now + cfg.heartbeat_seconds
        try:
            payload = run_once(args, params, cfg)
            write_runtime_files(payload, cfg)
            print(f"[{datetime.now().isoformat()}] 更新完成")
        except Exception as e:
            append_jsonl(Path(cfg.output_dir) / "errors.jsonl", {"timestamp": datetime.utcnow().isoformat(), "error": str(e)})
            print(f"运行失败: {e}")
        time.sleep(max(60, args.interval_minutes * 60))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Binance 多策略 + LSTM 引擎")
    p.add_argument("--symbol", default="BTC/USDT")
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--risk_reward", type=float, default=1.6)
    p.add_argument("--auto_trade", action="store_true")
    p.add_argument("--order_type", choices=["market", "limit"], default="market")
    p.add_argument("--testnet", action="store_true")
    p.add_argument("--daemon", action="store_true")
    p.add_argument("--interval_minutes", type=int, default=15)
    p.add_argument("--output_dir", default="runtime")
    p.add_argument("--heartbeat_seconds", type=int, default=30)
    p.add_argument("--max_retries", type=int, default=3)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    params = StrategyParams(symbol=args.symbol, risk_reward=max(args.risk_reward, 1.2), max_risk_per_trade=0.05)
    cfg = EngineConfig(output_dir=args.output_dir, heartbeat_seconds=args.heartbeat_seconds, max_retries=args.max_retries)
    if args.daemon:
        run_daemon(args, params, cfg)
        return
    payload = run_once(args, params, cfg)
    write_runtime_files(payload, cfg)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
