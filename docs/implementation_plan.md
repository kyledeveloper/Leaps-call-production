# LEAPS Call 期权量化扫描系统 (LEAPS Call Quant Scanner)

## 系统概述与目标 (Overview & Objectives)

构建一个专用于美股远期看涨期权（LEAPS Call，通常为到期日 $\ge 250$ 天至 2~3 年的期权）的量化机会扫描与决策分析系统。系统通过对接 **Webull OpenAPI** 获取实时行情与期权链，利用 **Python (NumPy / SciPy)** 搭建高性能美式期权（American Options）与 Greeks 定价内核，实现四大专业量化期权策略的实时打分与排序，并提供极简、现代化的响应式 **Web 看板** 与 **终端 CLI**。

本修订版吸收实盘阈值审计结论：四个策略方向保留，但 **阈值改为分档而非单一硬门闩**；漏斗行权价窗 **按策略分叉**（避免切掉 $\Delta\approx 0.85$ 的代正股合约）；策略二依赖的 **历史 IV 仓库** 升为先决模块；策略四在无 tick 数据前 **降级为弱信号**。系统定位为研究/扫描器，不为实盘自动下单。

架构可视化交付物已完成，可点击查看交互式架构图：
- 🇨🇳 中文架构全景：[arch-zh.html](arch-zh.html)
- 🇺🇸 英文架构全景：[arch-en.html](arch-en.html)

### 全局不变量 (Global Invariants)

1. **决策价一律用 $P_{exec}$**，禁止用 Mid 计算外在价值、杠杆、磨损率或排名。
2. **决策 Greeks / IV 一律用本地引擎**；券商链上自带 Delta/IV 仅作对照列，不进策略阈值。
3. **同一扫描共享一个 `asof` 快照时钟**（标的价 $S$、期权链、利率、分红、历史 IV 对齐到同一估值时刻）。
4. **时间年化全局统一**：$T = \mathrm{DTE}/365.25$（配置可改，但全链路同一常数）；权利金单位为「每股报价」，美元暴露一律 $\times 100$。
5. **非标准合约隔离**：乘数 $\neq 100$、adjusted/拆股合约、未知 OCC 符号一律标记 `NON_STANDARD` 并剔除出排名。
6. **$\alpha$ 滑块只重算内存快照**，禁止回源 Webull。

---

## 修订对照 (What Changed vs v1)

| 主题 | v1 | 本修订 |
|---|---|---|
| 策略一 Delta | $[0.70, 0.85]$ 一刀切 | 代正股核档 $[0.75, 0.85]$；$[0.70, 0.75)$ 降为杠杆看涨分档 |
| 外在磨损 | 硬阈值 $<5\%$ | 优质 / 可交易 / 观察 三档；计入放弃股息 |
| 价差 | $(Ask-Bid)/\mathrm{Mid}\le 15\%$ | 相对 + 绝对双阈值，按权利金分层 |
| 流动性 | $OI\ge 200,\ Vol\ge 10$ | OI 地板 + 20 日均成交/盘口张数；单日 Vol=10 不再单独放行 |
| 策略二 | 暴跌 ∧ 低 IV Rank 绑在一起 | 拆成「筑底低 IV」与「急跌后相对便宜」两个子状态 |
| IV 锚 | 未指定用哪条 IV | 优先该到期 ATM IV；IV Rank 无历史则策略二降级 |
| HV 对比 | $\mathrm{IV}/\mathrm{HV}_{90}$ | 期限对齐：长端 IV vs $\mathrm{HV}_{252}$ 或已实现年化 |
| 策略三 RSI | $\le 40$ 单独可过 | $\le 30$ 为核档；$\le 40$ 必须叠加 200DMA/回撤共振 |
| 200DMA | 固定 $-10\%$ | mega-cap 固定阈值 + 高波动龙头 $z$-score |
| 策略四 | Vol/OI≥1.5 + 买方主力 | Vol/OI 与美元成交额分层；买压标签无 tick 则禁用 |
| Level 3 窗 | 全局 $[0.65S, 1.35S]$ | **按策略分叉**，代正股下沿至 $0.50S$ |
| 美式上界 | $C < S e^{-qT}$ | 美式上界 $C \le S$；深实值允许无 IV |
| 利率 | 写死 4.2/4.0/3.9 | 期限结构插值；写死表仅作离线 fallback |
| 分红 | 仅连续 $q$ | 连续 $q$ 作主路径；离散股息树作高息/除息窗口校验 |
| 排名 | 未规定 | 默认分榜；综合分可选；流动性一票否决在打分前 |
| 防御条款 | 6 条 | 增补条款 7（公司行为）、8（快照时钟与 IV 仓） |

---

## 核心业务逻辑与四维量化策略 (Core Quant Strategies)

系统针对远期 LEAPS 期权的特性，构建四大互补的量化筛选模型（均基于有效执行价格 $P_{exec}$ 计算）。阈值分三档：

- **Pass（核档）**：进入主榜
- **Watch（观察档）**：进入副榜，降低权重
- **Reject**：不进入排名（仍保留原始盘口供下钻）

流动性安全门在策略打分 **之前** 执行，四个策略共用。

### 共用流动性与盘口护栏 (Shared Liquidity Guardrail)

在策略逻辑之前执行。不满足 **Reject** 档的合约不得进入任何策略榜。

| 规则 | Pass | Watch | Reject |
|---|---|---|---|
| 价差（相对） | $(Ask-Bid)/\mathrm{Mid} \le 6\%$ | $\le 10\%$ | $>10\%$ 且不满足绝对上限豁免 |
| 价差（绝对半价差） | $\le \$0.75$ | $\le \$1.50$ | $>\$1.50$ |
| 相对/绝对关系 | 相对或绝对任一进入 Pass 即可 Pass；两者都只到 Watch 则 Watch | — | 相对与绝对均超 Watch |
| 未平仓量 OI | $\ge 300$ | $\ge 100$ | $<100$ |
| 活跃度 | 当日 Vol $\ge 50$ **或** 20 日均 Vol $\ge 20$ **或** Bid/Ask size $\ge 10$ | 当日 Vol $\ge 10$ 且 OI $\ge 100$ | 其余 |
| 报价时效 | quote age $\le 15\text{s}$（RTH）/ 明确标记为官方结算价（盘后） | 盘后可交易但需标注 `STALE_NBBO` | 无时间戳或 age 超阈值且非结算价 |
| 合约规范 | multiplier $=100$，非 adjusted | — | 非标准乘数 / adjusted / 解析失败 |

说明：原 15% 相对价差作为唯一门槛过松（$\$50$ 权利金允许 $\$7.5$ 价差）。深实值 LEAPS 价差天然更宽，故用 **相对 + 绝对** 双闸，避免高价合约被相对阈值误杀、低价合约被相对阈值放行。

盘后附加：若 $Bid=0$ 或价差比 $>50\%$ 且 $OI<100$，直接 Reject（沿用原清洗器）。

---

### 1. 策略一：深度实值代正股 (Deep ITM Stock Replacement / PMCC 底仓)

- **核心逻辑**：以约 20%~35% 的资本占用获得约 75%~85% 的现货涨幅收益（有效杠杆 $2.5\times \sim 4.5\times$），基于美式期权模型扣除股息贴水后，筛选外在价值与放弃股息之和仍然可控的远期深实值 Call，作为股票替代或 PMCC 底仓。
- **定价依赖**：本地美式 Delta；**允许 IV 不可用**。深实值 Vega 极小，禁止因 `IV_UNAVAILABLE` 剔除本策略候选人。
- **量化阈值**：

| 指标 | Pass | Watch | Reject |
|---|---|---|---|
| 本地美式 $\Delta$ | $[0.75, 0.85]$ | $[0.70, 0.75)$ 或 $(0.85, 0.90]$ | $<0.70$ 或 $>0.90$ |
| 内在价值占比 $(S-K)^+/P_{exec}$ | $\ge 80\%$ | $\ge 70\%$ | $<70\%$ |
| 有效杠杆 $\Delta\cdot S / P_{exec}$ | $[2.5, 4.5]$ | $(4.5, 5.5]$ | $>5.5$ 或 $<2.0$ |
| 综合持有成本（见下） | $<5\%$ | $<8\%$ | $\ge 8\%$ |
| DTE | $\ge 300$ | $[250, 300)$ | $<250$ |

- **综合持有成本**（替代原「纯外在磨损 $<5\%$」）：

$$
\mathrm{Carry} = \frac{P_{exec} - (S-K)^+}{P_{exec}\cdot T} + q_{\mathrm{div}}
$$

其中 $q_{\mathrm{div}}$ 为前瞻 12 个月股息率（连续或离散折现到年化）。只卡外在价值会把高息蓝筹误判为“便宜”。

- **行权价窗（漏斗 Level 3 本策略切片）**：$K \in [0.50S,\ 0.95S]$。
- **前端展示**：同时输出内在/外在拆分、放弃股息美元、盈亏平衡点 $K + P_{exec}$、以及 $\alpha$ 变化后的 Carry 敏感度。

---

### 2. 策略二：波动率洼地 (Volatility Discount)

- **核心逻辑**：在 **权利金处于该标的自身历史低位** 时买入远期 Vega。不再把「暴跌」与「低 IV Rank」绑成同一事件——急跌后 IV Rank 通常升高，与洼地互斥。
- **两个互斥子状态**（同一合约只归入一个）：
  1. **Regime A — 筑底洼地**：价格非急跌（20 日跌幅 $> -15\%$ 且不创新低加速），且波动率处于历史低位。
  2. **Regime B — 急跌后相对便宜**：20 日急跌或距 52 周高点回撤 $\ge 20\%$，但当前长端 IV 仍低于该标的历史危机分位（IV Percentile $<40\%$ 或 IV z-score $<0$）。Regime B 不要求 IV Rank $<20\%$。
- **IV 定义（强制）**：
  - 策略比较用 **该到期日 ATM 附近（$\Delta\in[0.45,0.55]$）的本地 IV 中位数**，不是单点深实值 IV，也不是前月 7–30DTE IV。
  - IV Rank / IV Percentile 窗口默认 **252 个交易日**，优先 IV Percentile（抗单次尖峰）。
  - 期限对齐：长端 IV 对比 $\mathrm{HV}_{252}$（或已实现年化），**禁止**用 $\mathrm{HV}_{90}$ 作为 300DTE+ 合约的唯一锚。$\mathrm{IV}/\mathrm{HV}_{90}$ 仅作辅助列。
- **量化阈值（Regime A）**：

| 指标 | Pass | Watch | Reject |
|---|---|---|---|
| IV Percentile（优先）或 IV Rank | $<20\%$ | $<30\%$ | $\ge 30\%$ |
| 长端 $\mathrm{IV}/\mathrm{HV}_{252}$ | $<0.85$ | $<1.00$ | $\ge 1.00$ 且分位也不到 Watch |
| DTE | $\ge 300$ | $[250, 300)$ | $<250$ |
| 历史 IV 覆盖 | $\ge 180$ 个有效交易日 | $[90, 180)$ | $<90$ 日 → 整策略 **降级禁用**，不得用假 Rank |

- **数据先决**：无历史 IV 仓库时，本策略在 API 中返回 `STRATEGY_DEGRADED`，不得用当前截面 IV 伪造 Rank。
- **事件过滤**：未来 10 个交易日内有财报 / 已知重大事件的标的，Regime A 降为 Watch，并标注 `EVENT_WINDOW`。
- **行权价窗**：$K \in [0.70S,\ 1.25S]$（本策略买的是远期 Vega，不需要最深实值）。

---

### 3. 策略三：蓝筹龙头超跌共振 (Blue-Chip Oversold Confluence)

- **核心逻辑**：仅在 **高流动性蓝筹/核心 ETF** 池内，用多个中期技术信号共振筛选「跌了但结构未坏」的 LEAPS 标的，再叠加策略一或二的合约质量过滤。本策略先筛 **标的**，再在通过护栏的远月 Call 上打标。
- **标的池（硬约束）**：S&P 500 / Nasdaq-100 高流动性成分 + 核心 ETF（SPY, QQQ, IWM, DIA 及行业龙头清单，可配置）。池外默拒。
- **量化阈值（标的层，需共振）**：

| 信号 | 核信号（计 1.0） | 弱信号（计 0.5） |
|---|---|---|
| RSI(14) | $\le 30$ | $\le 40$ |
| 相对 200DMA | mega-cap / ETF：$\le -10\%$；高波动龙头（HV20 年化 $>40\%$）：标准化 $z\le -1.0$ | mega-cap $\le -6\%$ |
| 距 52 周高点回撤 | $\ge 15\%$ | $\ge 10\%$ |
| 距 52 周低点 | 已离开低点 $\ge 3\%$（避免接飞刀） | 触及低点附近 |

Pass：加权分 $\ge 2.0$ 且至少 1 个核信号。Watch：分 $\in [1.0, 2.0)$。Reject：$<1.0$ 或非标的池。

- **合约层附加**：必须同时通过共用流动性护栏，且 $DTE\ge 250$。优先叠加策略一质量（内在占比、Carry）——超跌但权利金极贵的合约不得因「跌了」排到榜首。
- **事件过滤**：财报窗口内标注，不自动 Reject（超跌常发生在财报后），但排名降权。
- **行权价窗**：与策略一或二的并集，按用户当前主策略切换；默认 $[0.50S,\ 1.25S]$。

---

### 4. 策略四：远期异动 (Unusual Far-Dated Options Flow)

- **核心逻辑**：在远月合约上捕捉 **新开仓规模异常**。Webull 链快照 **不能可靠识别主动买盘**，因此「成交价偏向买方主力」降为 **可选增强**，默认关闭。
- **主信号（仅用链上可证字段）**：

| 指标 | Pass | Watch | Reject / 忽略 |
|---|---|---|---|
| $\mathrm{Vol}/\mathrm{OI}$ | $\ge 3.0$ 且 OI $\ge 50$ | $\ge 2.0$ | $<2.0$（原 1.5 只作内部调试，不进榜） |
| 美元成交额 $V_{\$}=P_{\mathrm{mid}}\times 100\times \mathrm{Vol}$ | 单名 $\ge \$250{,}000$；ETF 指数 $\ge \$1{,}000{,}000$ | 单名 $\ge \$100{,}000$；ETF $\ge \$250{,}000$ | 低于 Watch |
| 张数（分层） | 单名 $\ge 500$；ETF $\ge 2{,}000$ | 单名 $\ge 200$；ETF $\ge 500$ | 其余 |
| 相对自身 20 日均成交 | $\ge 3\times$ | $\ge 2\times$ | $<2\times$ |

Pass 需要「比率或倍数」与「美元成交额」同时达到对应档，避免廉价虚值刷榜。

- **次日确认（强烈建议，v1.1）**：下一交易日若 OI 上升，标记 `OPENING_CONFIRMED` 并提升权重；OI 下降标记 `CLOSING` 并移出主榜。v1 可先输出「待确认」。
- **买压标签（默认关闭）**：仅当数据源提供 time & sales / aggressor / sweep 时启用。禁止用 `last vs mid` 冒充主动买盘。
- **行权价窗**：$K \in [0.70S,\ 1.35S]$。极深实值大单另计，不与虚值彩票单混榜。
- **解释性限制（必须在 UI/CLI 展示）**：远月异动包含展期、税损、结构化产品对冲，预测力弱于近月 UOA。本策略是提示，不是方向证明。

---

### 评分与榜单 (Scoring & Ranking)

`scoring/ranker.py` 默认 **分榜输出**（四个策略四张表），避免互斥逻辑被加总稀释。

可选综合分（需在配置显式打开）：

$$
\mathrm{Score} = \sum_i w_i \cdot \mathrm{Quality}_i \cdot \mathrm{LiquidityPenalty}
$$

约束：

- 流动性护栏未 Pass 的合约 $w$ 清零（一票否决在打分前）。
- 同一合约可打多个策略标签，但综合榜只取最高策略分，避免重复计数。
- $\alpha$ 变化时只重排内存结果，权重配置写入结果元数据以便复现。

---

## 模块结构与实现清单 (Proposed Implementation Structure)

```
src/leaps_scanner/
├── __init__.py
├── api/
│   ├── __init__.py
│   ├── server.py              # FastAPI 路由 (/api/v1/scan, /api/v1/chains, /api/v1/config)
│   ├── tasks.py               # 异步后台长任务与状态管理（单次扫描幂等、部分失败降级）
│   └── static/
│       └── index.html         # 响应式看板（α 滑块只重算内存；分榜切换）
├── core/
│   ├── __init__.py
│   ├── american_pricing.py    # Bjerksund-Stensland (2002) 主引擎；CRR/LR 树仅校验
│   ├── tree_pricer.py         # 离散股息 CRR/Leisen-Reimer（除息窗口/高息校验）
│   ├── greeks.py              # 美式数值差分 Greeks + 欧式闭式对照（向量化）
│   ├── iv_solver.py           # Newton–Raphson + Brent；美式边界；深实值可放弃 IV
│   ├── rates.py               # 期限结构插值（Treasury/SOFR）；离线 fallback 表
│   ├── dividends.py           # 连续 q 与离散分红日程；前瞻 12M 股息率
│   ├── calendar.py            # 美股交易日、提前收盘、DTE、到期类型（PM 股票 vs 指数）
│   └── metrics.py             # P_exec、Carry、杠杆、IV Rank/Percentile、HV 期限对齐
├── data/
│   ├── __init__.py
│   ├── base.py                # OptionDataProvider 抽象（必须带 asof / quote_ts）
│   ├── webull.py              # Webull 客户端：Single-Flight Token、令牌桶、缓存 TTL
│   ├── mock.py                # 离线沙盒数据（含历史 IV 与脏数据夹具）
│   ├── funnel.py              # 三级漏斗；Level 3 按策略分叉行权价窗
│   ├── universe.py            # SP500 / QQQ / 核心蓝筹 / 自定义 Watchlist
│   ├── corporate_actions.py   # 拆股、特殊分红、adjusted 合约、乘数校验
│   ├── chain_normalize.py     # OCC 符号解析、标准/非标准隔离
│   └── store/
│       ├── __init__.py
│       ├── prices.py          # 日线（RSI、200DMA、HV、52w）
│       └── iv_history.py      # 固定期限 / 到期 ATM IV 历史（策略二先决）
├── strategies/
│   ├── __init__.py
│   ├── base.py                # BaseStrategy：输入快照 + 护栏结果；输出 Pass/Watch/Reject
│   ├── guards.py              # 共用流动性 / 盘口 / 规范护栏
│   ├── deep_itm.py            # 策略一
│   ├── vol_discount.py        # 策略二（Regime A/B + 历史不足降级）
│   ├── oversold.py            # 策略三（标的共振 + 合约质量）
│   └── unusual_flow.py        # 策略四（无 tick 时关闭买压）
├── scoring/
│   ├── __init__.py
│   └── ranker.py              # 分榜默认；可选综合分；α 内存重排
├── config/
│   ├── __init__.py
│   └── thresholds.yaml       # 阈值版本化（策略档、漏斗窗、α 默认、利率 fallback）
└── cli.py                     # Rich 表格：分榜、降级原因、asof
tests/python/
├── conftest.py                # Socket Disable Guard，强制 100% 离线
├── fixtures/
│   └── bs2002_goldens.json    # 论文/MATLAB 金标向量
├── test_american_pricing.py   # BS2002 金标 + 欧式下界 + 提前行权溢价
├── test_tree_dividends.py     # 离散股息树 vs 连续 q 差异上限
├── test_iv_solver.py          # 美式上界、深实值放弃 IV、套利标记
├── test_slippage_metrics.py   # P_exec、×100 单位、往返滑点、Carry 含股息
├── test_rates_calendar.py     # 曲线插值、DTE 年化常数一致性
├── test_webull_adapter.py     # Token 单飞竞态、429、TTL 缓存、schema
├── test_funnel.py             # 策略一分叉窗不得丢掉 0.85Δ 合约
├── test_corporate_actions.py  # 拆股乘数、adjusted 剔除
├── test_strategies.py         # 四策略分档；策略二无历史降级
├── test_ranker.py             # 分榜、一票否决、α 重排不回源
├── test_adversarial_chaos.py  # 表驱动脏数据
├── test_concurrency.py        # single-flight 与限流竞态
└── test_api.py                # 部分失败、STRATEGY_DEGRADED、asof 元数据
```

### 扫描编排 (Pipeline)

```
universe(L1) → expirations(L2, DTE≥250)
            → strategy-aware strikes(L3)
            → normalize + corporate-action gate
            → shared liquidity guard
            → threadpool: American price / optional IV / metrics
            → strategy flags (Pass/Watch/Reject)
            → ranker (per-board)
            → persist snapshot (asof, thresholds version)
```

任意单票失败不得打崩整次扫描：该票标记 `UNDERLYING_FAILED`，其余继续。结果必须带回：`asof`、阈值版本、数据完整度、各过滤原因计数。

Webull 缓存 TTL：RTH 链 15–60s（可配），标的日线/历史 IV 日级。`Semaphore(5)` + 令牌桶保留；Greeks/IV 只用 **线程池 + 向量化**，默认不开进程池。

---

## 验证与测试计划 (Verification Plan)

### 自动化测试与红线要求 (Automated Tests)

- **网络硬拦截隔离**：`tests/python/conftest.py` socket 猴子补丁，禁止真实外部连接。
- **BS2002 金标**：对齐公开算例 / MATLAB `optstockbybjs` 向量，价格与敏感度误差上限写入夹具（建议价格相对误差 $<10^{-3}$ 量级，极端参数单独放宽并注明）。
- **欧式下界**：美式 Call $\ge$ 同参数欧式 BSM；无分红时美式 Call $=$ 欧式 Call（数值容差内）。
- **离散 vs 连续分红**：高息、临近除息样本上，连续 $q$ 与离散树的价差若超过配置上限，连续路径必须降级为「仅筛选、标注 `DIV_MODEL_DIVERGENCE`」，不得静默作为唯一真值。
- **漏斗 vs 策略一回归**：构造 $\Delta\approx 0.85$、$K\approx 0.58S$ 的夹具合约，断言 Level 3 在策略一模式下保留该合约。
- **单位红线**：$P_{exec}$、Carry、杠杆断言同时覆盖「每股」与「每合约 ×100」；年化常数全链路一致。
- **策略二降级**：历史 IV $<90$ 日时 API/CLI 必须 `STRATEGY_DEGRADED`，且不输出伪造 Rank。
- **α 滑块**：单测保证重排函数零网络、零磁盘（除只读内存快照）。
- **竞态**：并发 401 刷新只触发一次真实 refresh；令牌桶在模拟时钟下不超发。
- **混沌表驱动**（`test_adversarial_chaos.py` 最低用例集）：
  - $Bid=0$ / $Ask=0$ / $Bid>Ask$ / 负 DTE
  - 深实值 Vega≈0 导致 IV 无解
  - 美式价 $> S e^{-qT}$ 但 $\le S$（不得标套利违规）
  - 盘后宽价差 + 低 OI
  - multiplier=1000 或 adjusted 符号
  - 缺 quote timestamp
- **执行命令**：
  ```bash
  pytest tests/python/ -v --cov=src/leaps_scanner
  ```

### 手动验证 (Manual Verification)

- 启动服务：`uvicorn src.leaps_scanner.api.server:app --port 8000`
- 浏览器访问 `http://localhost:8000`：
  - 四个策略分榜切换，综合榜默认关闭；
  - 拖动 **买入滑点压力测试滑块**，确认排名变化 **不触发** 新的 Webull 请求（可用网络面板验证）；
  - 人为清空 IV 历史后，策略二展示降级横幅而非空 Rank 或 0%；
  - 抽查一只高息蓝筹，确认 Carry 含股息、不只展示外在磨损。

### 性能预算 (Performance Budget)

- 50 标的、每标的 2–4 个远月、策略分叉后的行权价切片，单次全量扫描 p95 $< 8s$（不含冷启动鉴权），其中定价/Greeks 在内存向量化完成。
- α 重排 p95 $< 100ms$（纯内存）。

---

## Red-Team Adversarial Inquest & Defensive Clauses (红队质询回应与防御性条款)

> **蓝队公开裁决声明**：蓝队完全接受独立红队审计员出具的《方案红队质询函》中的 3 项 BLOCKER 与 4 项 CONCERN，并接受后续实盘阈值审计补充的漏斗/IV/滑点死角。以下 8 项工程级强制防御性条款纳入实现规范。

### Defensive Clause 1: 美式定价、利率曲线与分红模型 (BLOCKER 01)

- **主模型**：`american_pricing.py` 实现 **Bjerksund-Stensland (2002)**。无分红或 $q\approx 0$ 时回退到欧式闭式，避免无意义的提前行权分支。
- **校验模型**：CRR / Leisen-Reimer 仅用于单测与除息窗口抽检，不进默认热路径。
- **连续 $q$**：用于日常扫描热路径；除息窗口计算 BS2002 的 $I_1,I_2$ 边界。
- **离散股息**：`dividends.py` + `tree_pricer.py` 在前瞻 12M 股息率 $\ge 2.5\%$ 或距除息 $\le 14$ 个交易日时启用对照。发散超过阈值则标注，不以连续 $q$ 独断提前行权溢价。
- **动态利率**：`rates.py` 按期权到期 $T$ 插值国债或 SOFR OIS 曲线。配置中的 1Y/2Y/3Y 表 **只作离线 fallback**，禁止作为生产唯一利率源。记录本次扫描使用的曲线 `asof`。

### Defensive Clause 2: 保守执行价、往返滑点与单位 (BLOCKER 02)

- 严禁仅用 $\mathrm{Mid}$ 做策略评估。
- 有效买入价：
  $$P_{exec} = \mathrm{Mid} + \alpha \cdot \frac{\mathrm{Ask}-\mathrm{Bid}}{2},\quad \alpha\in[0,1],\ \alpha_{\mathrm{default}}=0.5$$
- 当 Ask 侧 displayed size $<$ 拟交易张数（默认 5）时，自动把 $\alpha$ 抬升一级（至少 0.75），避免「1 张 Ask」造成的虚假可成交。
- **往返成本**单独计算并展示：$P_{sell}=\mathrm{Mid}-\alpha_{\mathrm{exit}}\cdot\text{half-spread}$，默认 $\alpha_{\mathrm{exit}}=\alpha$。排名主列仍用买入 $P_{exec}$，但 Watch 提示「退出滑点」。
- 所有磨损、杠杆、Carry、美元成交额对 **每股 vs 每合约 ×100** 有显式字段，禁止隐式混用。
- Web 滑块只重算内存快照。

### Defensive Clause 3: 三级漏斗、策略分叉切片与流控 (BLOCKER 03)

- **Level 1**：日线 + 市值/流动性/池归属，先把标的收到约 20–50 只（策略三更严）。
- **Level 2**：只拉到期列表，丢弃 $DTE<250$。
- **Level 3（修订）**：禁止全局 $[0.65S,1.35S]$。按当前启用策略取并集：
  - 策略一：$[0.50S,\ 0.95S]$
  - 策略二：$[0.70S,\ 1.25S]$
  - 策略三：默认 $[0.50S,\ 1.25S]$
  - 策略四：$[0.70S,\ 1.35S]$
- 异步生成器 + `asyncio.Semaphore(5)` + 令牌桶；单票链缓存 TTL，避免 50 标的重复打满 QPS。
- 单票 429/超时：指数退避后标记失败并继续。

### Defensive Clause 4: 混合 IV 求解器与正确的美式边界 (CONCERN 01)

- **边界预检（修订）**：
  - 下界：$\max\bigl(0,\ S-K,\ Se^{-qT}-Ke^{-rT}\bigr)$（内在价值与欧式下界取大）
  - 上界：**美式 Call $C \le S$**（不得使用 $Se^{-qT}$ 作为美式上界）
  - 违背上/下界：`ARBITRAGE_VIOLATION`，不求解 IV
- 算法：Newton–Raphson → Vega $<10^{-6}$ 或 20 次未收敛则 Brent；再失败则 `IV_UNAVAILABLE` 或深实值场景的 `IV_UNIDENTIFIED`。
- **策略一在 IV 缺失时必须可运行**（用内在 + $P_{exec}$ 外在）。
- 同到期 IV 微笑出现明显倒挂时标注 `IV_SURFACE_ANOMALY`，不阻断扫描。

### Defensive Clause 5: Single-Flight Token、盘后清洗与陈旧盘口 (CONCERN 02)

- `asyncio.Lock` 单飞刷新，401/临过期只允许一个协程打 refresh。
- 清洗：剔除 $Ask\le 0$、$Bid<0$、$Bid>Ask$。
- 盘后 $Bid=0$ 且价差比 $>50\%$ 且 $OI<100$：流动性安全门剔除。
- **新增**：无 `quote_ts` 的盘口不得进 Pass；RTH 超时盘口降为 Watch 或 Reject（见共用护栏）。

### Defensive Clause 6: 计算隔离、缓存与离线硬拦截 (CONCERN 03 & 04)

- Greeks/IV 经 `run_in_threadpool` 向量化计算；**默认不使用 ProcessPoolExecutor**（闭式近似的序列化成本高于收益）。
- 定价结果按 `(symbol, expiry, strike, S, P_exec, r, q, asof, model_ver)` 缓存于本次扫描。
- 测试 socket 硬拦截；混沌集见测试计划。

### Defensive Clause 7: 公司行为与非标准合约隔离 (新增)

- 解析 OCC 符号；`multiplier != 100`、adjusted、拆股未完成调整的合约标记 `NON_STANDARD` 并剔除排名。
- 扫描日若标的发生拆股/特殊分红，当日该标的策略结果全部降为 Watch 或暂停，直至链与日线对齐。
- 夹具必须覆盖非 100 乘数，防止 $P_{exec}$ 与杠杆错一个数量级。

### Defensive Clause 8: 快照时钟、历史 IV 仓库与策略降级 (新增)

- 每次扫描写入单一 `asof`。禁止用「今日收盘 S + 昨日链 + 上周 IV」拼出 Pass。
- `data/store/iv_history.py` 持久化固定期限或到期 ATM IV。策略二覆盖不足 90 个交易日 → `STRATEGY_DEGRADED`，禁用 Rank 阈值。
- 日线不足 200 根时，策略三的 200DMA / RSI 信号降级或禁用，不得用短样本冒充。
- 阈值与模型版本写入结果，保证同一快照可复现。

---

## 明确非目标 (Non-Goals, v1)

- 自动下单、智能路由、仓位管理、Kelly 资金配置。
- 在无 tick/sweep 数据时声称识别「主力主动买盘」。
- 用进程池或整链全行权价暴力拉取作为性能方案。
- 把券商链上 Delta 当作策略一的准入依据。

---

## 实施顺序建议 (Build Order)

1. 快照模型 + $P_{exec}$ + 共用护栏 + mock 数据（含脏数据）。
2. BS2002 + 利率插值 + 连续 $q$；金标测试先绿。
3. 策略分叉漏斗；锁定「0.85Δ 不被切掉」回归。
4. 策略一（无 IV 也可运行）+ α 内存重排看板。
5. 日线仓 + 策略三。
6. IV 历史仓 + 策略二（含降级路径）。
7. 策略四（无买压标签）+ OI 次日确认字段预留。
8. 离散股息对照、公司行为隔离、并发/限流竞态测试。
