# 美股期权量化多策略扫描器 (Options Quant Strategy Scanner)

美股期权量化研究与筛选扫描器，支持两大核心策略家族：**LEAPS 远月看涨期权**（买方代正股，$\text{DTE} \ge 250$）与 **Cash-Secured Put (CSP 现金担保卖 Put)**（卖方系统化权利金收割，$\text{DTE } \le 45$）。**不是** 自动下单交易系统。

[English](README.md) | [简体中文](README.zh-CN.md)

## 策略矩阵

### 1. LEAPS 看涨期权家族（买方代正股策略，$\text{DTE} \ge 250$）

| 看板 | 核心逻辑 | 筛选规则与风控 |
|---|---|---|
| **1. 深度实值** | 代正股 / PMCC 底仓 | 行权价 0.65S–0.85S，$\Delta$ 0.70–0.85 为 PASS，$\Delta > 0.90$ 扫描前剔除。持有磨损 = 时间价值 / ($P_{exec} \times T$) + 股息拖累。深实值流动性优先看点差与 OI，日成交量不作一票否决（要求 `ask_size` $\ge 5$）。单日 $\theta / P_{exec}$ 强门槛拦截。 |
| **2. 波动率洼地** | 便宜波动率做多 Vega | 累积存满 $\ge 90$ 天平值 ATM IV 历史后启用真实 IV 分位。此前公开延迟行情以 HV20 历史波动率分位代替，**最高评级封顶 WATCH**。 |
| **3. 蓝筹超跌共振** | 核心资产均值回归 | RSI / 200 日均线乖离 / 52 周位置 / 短线反弹得分共振。要求必须属于核心股票池且具备 $\ge 200$ 根日线数据。 |

### 2. Cash-Secured Put (CSP) 家族（卖方收益策略，$\text{DTE } \le 45$）

| 看板 | 核心逻辑 | 筛选规则与风控 |
|---|---|---|
| **1. 权利金年化收割** | 系统化年化高收益率筛选 | $\Delta \in [-0.30, -0.15]$，DTE ≤ 45，四档用户区间（<7 / 7–14 / 14–28 / 28–45），按年化资本回报率（AROC）排序。买一挂单为零（Zero-Bid）或买卖盘倒挂立即一票否决。 |
| **2. Wheel 轮动抄底** | 优质折扣建仓与安全垫 | $\Delta \in [-0.45, -0.30]$，向下安全缓冲 $\ge 5\%$，标的 RSI $\le 55$，现价距 200 日均线 $\le 1.05$。 |
| **3. 高 IV Rank 溢价** | 波动率均值回归与压制 | IV Rank $\ge 50\%$，$\Delta \in [-0.35, -0.15]$，专门收割高溢价波动率暴跌回归红利。 |

#### 🛡️ CSP 卖方风控与指标模型
- **卖方执行价滑点 ($P_{exec}$)**：保守卖方成交价 $P_{exec} = mid - \alpha \times (mid - bid)$（等价于 $mid - \alpha \times half\_spread$）；当盘口 `bid_size` 小于目标张数时将 $\alpha$ 至少抬升至 0.85；并保证 $bid \le P_{exec} \le mid$。无买盘（$\text{Bid} \le 0$）一票否决。
- **DTE 四档筛选与 Gamma 惩罚**：用户可选 `<7`、`7–14`、`14–28`、`28–45`。短 DTE 不再硬拒绝；AROC 使用 $\text{DTE}_{safe} = \max(\text{DTE}, 1)$，并在 21 天以内额外衰减。
- **POP 胜率与向下安全缓冲**：基于 Delta 线性逼近的获利概率估计（$1 - |\Delta|$，附带免责声明）；安全缓冲 $(S - K) / S$。
- **资金占用与最大亏损**：名义担保金（$K \times 100$），最大可能亏损（$K \times 100 - P_{exec} \times 100$），支持现金池分配，并强制单标的最高占用总资金 25% 敞口上限。
- **-15% 极端暴跌压力测试**：测算标的急跌 15% 时每张合约的净盈亏与组合抗压能力。
- **财报日预警**：显式标注 DTE 内财报风险（`🚨 位于 DTE 内` 或 `⚠️ 财报待核`）。

## 数据源

| 模式 | 行情源 | 密钥配置 | 说明 |
|---|---|---|---|
| **公开延迟 (默认 Default)** | Yahoo 日线 + Nasdaq OPRA 延迟期权链 | 无（约 15 分钟延迟，非真实 NBBO） | 真实市场行情（如 AVGO 现价 $339.51），无需任何 API 密钥。系统开箱默认使用。 |
| **Webull 实盘** | Webull OpenAPI 直连 | App Key + Secret | 纯驻留内存，绝不写盘，可选的实时行情通道。 |
| **沙盒 (仅供单测)** | 本地纯静态离线测试样本 | 无 | 严格保留给本地气密单元测试使用（`--offline` 参数触发）。生产与日常运行界面中已彻底屏蔽沙盘假数据。 |

请勿将 Webull 密钥提交至 Git 或贴在聊天记录中，`.env` 已被全局 gitignore。

每次扫描提取的 ATM IV 均自动追加至 `data/iv_history.json`，在积累约 90 个交易日后策略二即可解除 HV 代理封顶。

公开延迟行情支持多标的并发抓取（可通过 `LEAPS_FETCH_WORKERS` / `LEAPS_FETCH_MIN_INTERVAL` 调节）。Yahoo 日线按美股交易日统一缓存本地（`data/daily_bars.json`，不入库）。LEAPS 与 CSP 的扫描管道完全独立隔离，互不产生数据争用与冗余消耗。

## 运行

运行环境要求 Python 3.9+。

```bash
cd Leaps-call-production
pip install -r requirements.txt

# 启动 Web 量化看板（默认公开延迟行情；默认绑定 127.0.0.1）
PYTHONPATH=. python -m src.leaps_scanner.cli --serve --port 8000

# 离线沙盒看板（气密测试桩）
PYTHONPATH=. python -m src.leaps_scanner.cli --offline --serve --port 8000

# 可选：绑定全部网卡（仅实验室环境；含 Webull 密钥的 POST 仍限本机）
PYTHONPATH=. python -m src.leaps_scanner.cli --serve --host 0.0.0.0 --port 8000
```

打开浏览器看板：
- 默认主看板：`http://127.0.0.1:8000/`
- 直达 CSP 看板：`http://127.0.0.1:8000/csp` 或携带参数 `?family=csp`

### 无界面命令行 (CLI)

```bash
# 扫描 LEAPS Call 远月看涨（使用真实延迟行情）
PYTHONPATH=. python -m src.leaps_scanner.cli --family leaps --symbols SPY,QQQ --alpha 0.5
PYTHONPATH=. python -m src.leaps_scanner.cli --strategy deep_itm --universe etfs

# 扫描 Cash-Secured Put 卖方（使用真实延迟行情）
PYTHONPATH=. python -m src.leaps_scanner.cli --family csp --symbols AAPL,MSFT,NVDA,AVGO
PYTHONPATH=. python -m src.leaps_scanner.cli --family csp --strategy harvest --universe etfs

# （可选）强制使用离线沙盒测试桩进行极速本地验证
PYTHONPATH=. python -m src.leaps_scanner.cli --offline --family csp --symbols AAPL,SPY
```

### 看板核心功能
- **策略家族切换器**：顶栏一键在 LEAPS Call 与 Cash-Secured Put 之间无缝切换。
- **纯内存毫秒级重排**：实时拖动 $\alpha$ 滑条、输入可用现金池与单标的敞口上限、点选过滤胶囊（AROC、安全缓冲、IV Rank、POP、财报、流动性）以及四个到期区间（0–7 / 7–14 / 14–28 / 28–45），零网络请求瞬间过滤看板。
- **中英双语切换**：右上角 **EN / 中文** 随时切换，选择持久化保存在 `localStorage`。
- **标的分级池**：**ETF**（12 只）· **道指**（30 只）· **标普 100** · **纳指 100** · **核心并集**。
- **后台自动静默扫描**：服务启动即刻在后台执行真实行情扫描，支持实时进度指示与轮询自动载入。

## 测试与校验

```bash
# 执行全部 Python 单元测试与无网络气密性测试（171 项单测）
PYTHONPATH=. python -m unittest discover -s tests/python -q

# 执行 JS 研发工具链与 Pre-push 安全门禁测试
npm test
```

所有单元测试完全气密隔离：禁止网络 Socket 连接。

## 定价模型与希腊字母

- **美式期权定价**：Bjerksund–Stensland (2002) 解析模型，美式 Put 采用严格的看跌-看涨对称性变换（$AmericanPut(S, K, T, r, q, \sigma) = AmericanCall(K, S, T, q, r, \sigma)$）。
- **物理无套利上下界**：$K e^{-rT} \le \text{EuropeanPut} \le \text{AmericanPut} \le K$。
- **模型 Greeks**：解析导数 Black-Scholes / Bjerksund-Stensland 希腊字母。Put Delta 严格约束在负数区间 $[-1.0, 0.0]$。
- **执行价滑点计算**：
  - LEAPS 买方：$P_{exec} = mid + \alpha \times half\_spread$（`ask_size` 不足目标张数时 $\alpha \ge 0.75$）
  - CSP 卖方：$P_{exec} = mid - \alpha \times (mid - bid)$（`bid_size` 不足目标张数时 $\alpha \ge 0.85$；夹紧到 $[bid, mid]$）

模型 Greeks 与估算指标属于量化筛选参考值，并非交易所实时行情。PASS 与 WATCH 仅作为备选初筛池，下单前请务必在券商交易终端核对实时盘口。

## 许可协议

MIT，详见 [LICENSE](LICENSE)。

