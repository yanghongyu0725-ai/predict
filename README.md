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

如果你运行时报 `ModuleNotFoundError`（例如缺少 `ccxt` / `tensorflow`），按你所在系统执行：

### Windows CMD

> 先确认在项目根目录（能看到 `crypto_deep_strategy.py` 和 `scripts` 文件夹）。

```bat
dir
setup_env.bat
call .venv\Scripts\activate.bat
python scripts/check_env.py
```

如果仍报“不是内部或外部命令”，请先：

```bat
git pull
dir scripts
```

并尝试显式路径：

```bat
.\scripts\setup_env.bat
```

### Windows PowerShell

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_env.ps1
.\.venv\Scripts\Activate.ps1
python scripts/check_env.py
```

### Linux / macOS / Git-Bash

```bash
bash scripts/setup_env.sh
. .venv/bin/activate
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


## 一键部署（本地电脑实验）

### Windows

```bat
one_click_deploy.bat
```

或 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\one_click_deploy.ps1
```

该命令会：
1. 自动初始化环境（`setup_env`）
2. 自动检查依赖（`python scripts/check_env.py`）
3. 若缺失依赖会尝试自动补装
4. 启动本地 UI（`http://127.0.0.1:8501`）

UI 中提供按钮：
- 运行一次策略
- 开启持续测试（一直跑）
- 开启自动下单
- 停止后台任务
- 查看 K 线图


UI 新增：
- 支持 BTC/ETH 切换
- 支持 1m / 15m / 1h / 4h / 1d / 1w 切换
- 每10秒自动刷新当前价格与信号状态
- 增加实时日志窗口，记录“是否触发策略/当前运行状态”
- K线图在页面顶部固定显示（不需要跳转）


另外我提供了操作指南文件：`OPERATION_GUIDE.txt`。


## 关机后历史数据会清空吗？

不会。只要你**不删除项目目录的 runtime 文件夹**，历史会一直保留。

当前会持久化两份历史：
- `runtime/signal_history.jsonl`（追加文本日志）
- `runtime/history.db`（SQLite结构化历史库，便于后续查询统计）

重启电脑后再次启动程序，会继续在上述文件基础上追加，不会自动清空。


补充：UI 里 K线图会固定显示在页面顶部；未生成时会显示提示，运行一次策略后自动可见。


## 代码冲突不会处理怎么办（自动覆盖方案）

如果你希望“每次都以远端代码为准，自动覆盖本地冲突”，可以使用强制同步脚本：

### Windows CMD

```bat
force_sync.bat main
```

### Windows PowerShell

```powershell
powershell -ExecutionPolicy Bypass -File .\force_sync.ps1 -Branch main
```

### Linux/macOS/Git-Bash

```bash
bash scripts/force_sync.sh main
```

说明：
- 该操作会执行 `reset --hard` + `clean -fd`，会清除本地未提交改动。
- 脚本会自动创建一个本地快照分支 `backup-before-force-sync-时间戳` 供回滚。


说明：如果你所在地区访问 Binance 返回 451，UI 会自动回退尝试 Bybit 和 OKX 数据源（可通过环境变量 `PREFERRED_EXCHANGE` 指定优先交易所）。
