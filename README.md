# Binance 多策略 LSTM 引擎（24/7 持续验证 + 工业级下单流程参考）

你提的两点已落地：

1. **验证数据不考虑停机/熔断，一直跑**：后台持续输出多指标策略的胜率、盈亏率等统计。
2. **工业级自动下单流程参考**：加入重试、交易所精度/最小下单量校验、保护单尝试、状态落盘。

## 持续验证口径

- 回测统计口径固定为：**不考虑停机、熔断、手续费、滑点**。
- 多策略持续输出：
  - 交易数 `trades`
  - 胜率 `win_rate`
  - 盈亏因子 `profit_factor`
  - 收益率 `return_rate`
  - 最大回撤 `max_drawdown`

## 24 小时后台运行

```bash
python crypto_deep_strategy.py \
  --symbol BTC/USDT \
  --daemon \
  --interval_minutes 15 \
  --output_dir runtime \
  --heartbeat_seconds 30
```

后台运行会持续写入：

- `runtime/latest_signal.json`：最新信号、多策略统计、执行状态
- `runtime/strategy_metrics_latest.json`：当前多策略指标快照
- `runtime/signal_history.jsonl`：历史追加记录
- `runtime/heartbeat.jsonl`：心跳
- `runtime/errors.jsonl`：异常日志

## 工业级自动下单流程（参考）

开启自动下单：

```bash
export BINANCE_API_KEY="你的key"
export BINANCE_API_SECRET="你的secret"
python crypto_deep_strategy.py --symbol ETH/USDT --daemon --auto_trade --testnet --order_type market --output_dir runtime_eth
```

流程要点：

1. 读取市场元数据（最小下单量、最小名义价值、精度）
2. 按风险上限反推仓位：单笔最大理论亏损 <= 本金 5%
3. 主订单下单（市价/限价）
4. 尝试保护单（止损/止盈）
5. 所有执行状态与错误都落盘，便于审计与复盘

## 推荐部署

- 先用 testnet 验证
- 再小资金实盘
- 用 `nohup`/`systemd`/`pm2` 守护进程

`nohup` 示例：

```bash
nohup python crypto_deep_strategy.py --symbol BTC/USDT --daemon --interval_minutes 15 --output_dir runtime > runtime/daemon.log 2>&1 &
```

## 缺少环境怎么办

如果你运行时报 `ModuleNotFoundError`（例如缺少 `ccxt` / `tensorflow`），按下面做：

```bash
bash scripts/setup_env.sh
source .venv/bin/activate
python scripts/check_env.py
```

然后再启动策略：

```bash
python crypto_deep_strategy.py --symbol BTC/USDT --daemon --interval_minutes 15 --output_dir runtime
```



## 电脑关机后还能运行吗？

结论：**不能在你关机的本机继续运行**。程序需要运行在“持续有电、有网络”的主机上。

你有 3 种可行方案：

1. **云服务器（推荐）**：阿里云/腾讯云/AWS 上 24h 运行。
2. **家里小主机/NAS**：设备不断电即可。
3. **托管容器平台**：例如 Railway/Render/Fly.io（注意网络与合规）。

### 推荐做法（云服务器 + systemd）

仓库已提供 `deploy/systemd/crypto-strategy.service` 模板。你可以：

```bash
# 1) 上传代码到服务器
# 2) 安装环境
bash scripts/setup_env.sh

# 3) 复制并安装服务
sudo cp deploy/systemd/crypto-strategy.service /etc/systemd/system/crypto-strategy.service
sudo systemctl daemon-reload
sudo systemctl enable crypto-strategy
sudo systemctl start crypto-strategy

# 4) 查看状态与日志
sudo systemctl status crypto-strategy
journalctl -u crypto-strategy -f
```

> 注意：需要把 service 里的 `User`、`WorkingDirectory`、`BINANCE_API_KEY/SECRET` 改成你自己的。

### 你这台电脑可以关机吗？

- 如果程序部署在**云服务器**上：你本机可以关机，不影响运行。
- 如果程序只跑在**你本机**：关机后程序必然停止。
