# LEAPS Call 期权量化扫描系统 (LEAPS Call Quant Scanner)

> **归档版本**：v1.0 (通过红蓝对抗前置质询)  
> **归档时间**：2026-09-15  
> **架构状态**：Showcase Passed (9/9)

---

## 系统概述与目标 (Overview & Objectives)

构建一个专用于美股远期看涨期权（LEAPS Call，通常为到期日 $\ge 250$ 天至 2~3 年的期权）的量化机会扫描与决策分析系统。系统通过对接 **Webull OpenAPI** 获取实时行情与期权链，利用 **Python (NumPy / SciPy)** 搭建高性能美式期权（American Options）与 Greeks 定价内核，实现四大专业量化期权策略的实时打分与排序，并提供极简、现代化的响应式 **Web 看板** 与 **终端 CLI**。

架构可视化交付物已完成，可点击查看交互式架构图：
- 🇨🇳 中文架构全景：[docs/arch-zh.html](../docs/arch-zh.html)
- 🇺🇸 英文架构全景：[docs/arch-en.html](../docs/arch-en.html)

---

## 核心业务逻辑与四维量化策略 (Core Quant Strategies)

系统针对远期 LEAPS 期权的特性，构建四大互补的量化筛选模型（均基于有效执行价格 $P_{exec}$ 计算）：

1. **策略一：深度实值代正股 (Deep ITM Stock Replacement / PMCC 底仓)**
   - **核心逻辑**：以 20%~35% 的资本占用获得约 70%~85% 的现货涨幅收益（有效杠杆 $2.5\times \sim 4.5\times$），基于美式期权模型扣除股息贴水后，严格筛选年化外在价值磨损极低的标的。
   - **量化阈值**：
     - $\Delta \in [0.70, 0.85]$
     - 保守年化外在价值磨损率 $< 5\%$（基于买入滑点执行价 $P_{exec}$ 计算）
     - 买卖价差保护：$\frac{\text{Ask} - \text{Bid}}{\text{Mid}} \le 15\%$
     - 流动性硬门槛：未平仓量 $\text{OI} \ge 200$，成交量 $\text{Vol} \ge 10$

2. **策略二：波动率洼地超跌反转 (Volatility Discount / IV Rank Reversal)**
   - **核心逻辑**：在标的暴跌或长期筑底后，当隐含波动率处于历史极度低估区间时建仓 LEAPS，享受标的上涨与远期 Vega 扩张的双重收益。
   - **量化阈值**：
     - $\text{IV Rank} < 20\%$ 或 $\text{IV} / \text{HV}_{90\text{d}} < 0.85$
     - $\text{DTE} \ge 300$ 天，做多廉价远期 Vega，最小化波动率踩空风险

3. **策略三：蓝筹龙头超跌共振 (Blue-Chip Oversold Confluence)**
   - **核心逻辑**：针对标普 500 / 纳斯达克 100 核心权重或行业龙头（如 AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA 等），结合技术面超跌信号（RSI 极值、200 日均线乖离率、52 周低点反弹），精选估值修复的 LEAPS 标的。
   - **量化阈值**：
     - 技术指标：$\text{RSI}(14) \le 40$ 或 现价距离 200 日均线偏离度 $\le -10\%$
     - 标的池限定：S&P 500 / QQQ 成分股高流动性龙头与核心 ETF

4. **策略四：远期异动放量异动 (Unusual Far-Dated Options Flow)**
   - **核心逻辑**：监测远月合约中大资金建仓异动。结合成交量与未平仓量异动比，利用微观买卖盘倾斜识别主动买盘。
   - **量化阈值**：
     - $\text{Volume} / \text{Open Interest} \ge 1.5$
     - 单日远月成交量 $\ge 500$ 张合约，成交价偏向买方主力

---

## 模块结构与实现清单 (Proposed Implementation Structure)

```
src/leaps_scanner/
├── __init__.py
├── api/
│   ├── __init__.py
│   ├── server.py              # FastAPI 路由与应用入口 (/api/v1/scan, /api/v1/chains, /api/v1/config)
│   ├── tasks.py               # 异步后台长任务与状态管理 (避免阻塞主循环)
│   └── static/
│       └── index.html         # 响应式 Web Dashboard 交互看板 (含滑点压力滑块)
├── core/
│   ├── __init__.py
│   ├── american_pricing.py    # Bjerksund-Stensland (2002) 美式期权定价与 Greeks 引擎
│   ├── greeks.py              # Black-Scholes 闭式与 Greeks 向量化封装
│   ├── iv_solver.py           # 混合 IV 求解器 (Newton-Raphson + Brent 回退 + 套利边界校验)
│   └── metrics.py             # 保守有效买入价、真实年化外在价值衰减、IV Rank/HV
├── data/
│   ├── __init__.py
│   ├── base.py                # 数据提供商抽象基类 (OptionDataProvider)
│   ├── webull.py              # Webull OpenAPI 客户端 (含 Single-Flight Token 锁、频控令牌桶与数据清洗)
│   ├── mock.py                # 离线沙盒数据提供商 (供离线 TDD 测试)
│   ├── funnel.py              # 三级漏斗初筛流水线 (Universe -> DTE >= 250 -> 行权价切片)
│   └── universe.py            # 标的资产池管理 (SP500, QQQ, 核心科技蓝筹, 自定义 Watchlist)
├── strategies/
│   ├── __init__.py
│   ├── base.py                # 策略筛选基类 (BaseStrategy)
│   ├── deep_itm.py            # 策略一：深度实值代正股
│   ├── vol_discount.py        # 策略二：波动率洼地超跌反转
│   ├── oversold.py            # 策略三：蓝筹龙头超跌共振
│   └── unusual_flow.py        # 策略四：远期异动放量
├── scoring/
│   ├── __init__.py
│   └── ranker.py              # 多维综合评分器、流动性安全护栏与机会排序
└── cli.py                     # 命令行终端扫描入口 (Rich 表格输出)
tests/python/
├── conftest.py                # 全局网络阻断夹具 (Socket Disable Guard，强制 100% 离线)
├── test_american_pricing.py   # Bjerksund-Stensland 美式期权与股息贴水验证
├── test_iv_solver.py          # 混合 IV 求解器极端边界与套利违规测试
├── test_slippage_metrics.py   # 保守执行价与滑点模型单测
├── test_webull_adapter.py     # Webull 客户端 Token 锁、频控与清洗测试
├── test_funnel.py             # 三级漏斗内存与流式测试
├── test_strategies.py         # 四大策略筛选逻辑测试
├── test_adversarial_chaos.py  # 脏数据注入破坏性测试 (0 Bid, 倒挂盘口, 负 DTE)
└── test_api.py                # FastAPI 接口集成测试
```

---

## 验证与测试计划 (Verification Plan)

### 自动化测试与红线要求 (Automated Tests)
- **网络硬拦截隔离**：在 `tests/python/conftest.py` 中注入 socket 猴子补丁，禁止任何真实的外部网络 socket 连接，确保 100% 离线单测运行。
- **Greeks 精度基准**：对齐标准美式期权（Bjerksund-Stensland 2002）标准算例，对比欧式 B-S 差异，验证除息日前提前行权溢价。
- **极端脏数据鲁棒性**：运行 `test_adversarial_chaos.py`，注入 $Bid=0$、$Ask=0$、$Bid > Ask$、极度虚值/实值导致 IV 无解等混沌数据，验证系统 0 崩溃、安全降级并输出日志提示。
- **执行命令**：
  ```bash
  pytest tests/python/ -v --cov=src/leaps_scanner
  ```

### 手动验证 (Manual Verification)
- 启动服务：`uvicorn src.leaps_scanner.api.server:app --port 8000`
- 浏览器访问 `http://localhost:8000`：
  - 验证看板加载速度、策略筛选切换；
  - 拖动 **买入滑点压力测试滑块 (Slippage Stress Slider)**，实时观察不同成交滑点下的真实外在价值与机会排名变化。

---

## Red-Team Adversarial Inquest & Defensive Clauses (红队质询回应与防御性条款)

> **蓝队公开裁决声明**：蓝队完全接受独立红队审计员出具的《方案红队质询函》(Design Adversarial Inquest) 中指出的 3 项 BLOCKER 与 4 项 CONCERN。以下为蓝队正式制定的 6 项工程级强制防御性条款（Defensive Clauses），这些条款已全部纳入架构与模块实现规范中：

### Defensive Clause 1: 美式期权定价模型与动态无风险利率/股息贴水集成 (针对 BLOCKER 01)
- **模型实现**：在 `src/leaps_scanner/core/american_pricing.py` 中实现金融工程界公认的 **Bjerksund-Stensland (2002)** 美式期权解析近似模型，同时保留 CRR/Leisen-Reimer 二叉树作为基准校验。
- **股息流处理**：针对 LEAPS 跨越 1~3 年的周期，引入标的年化连续股息率 $q$（Dividend Yield），在除息窗口计算提权行权边界值 $I_1, I_2$；
- **动态利率**：建立动态到期期限无风险利率映射表（1Y: 4.2%, 2Y: 4.0%, 3Y: 3.9% 等，可配置），彻底取缔固定 0 利率或单一利率假设。

### Defensive Clause 2: 保守买入执行价模型与滑点敏感度控制 (针对 BLOCKER 02)
- **摒弃纯 Mid-Price**：系统严禁仅使用 $\text{Mid} = \frac{Bid + Ask}{2}$ 进行策略评估与外在价值计算。
- **有效执行价模型**：
  $$P_{exec} = \text{Mid} + \alpha \cdot \frac{\text{Ask} - \text{Bid}}{2} \quad (\alpha \in [0.0, 1.0], \text{默认 } \alpha = 0.5 \text{ 即保守 Ask 计价})$$
- **外在价值与杠杆重算**：所有外在价值磨损率、杠杆倍数、打分排名均基于 $P_{exec}$ 计算，消除“纸面流动性幻觉”。
- **前端滑点压力滑块**：Web 看板提供 $\alpha$ 动态调节滑块（$0\% \sim 100\%$ 半价差），让用户直观评估滑点对盈亏平衡点的影响。

### Defensive Clause 3: 三级漏斗初筛与流式批处理流控 (针对 BLOCKER 03)
- **分层漏斗初筛 (Multi-Stage Funnel)**：
  - **Level 1 (标的池初筛)**：在拉取期权链前，先拉取标的现货日线，根据市值、流动性、技术面指标将标的缩小至目标集（如 20~50 只）；
  - **Level 2 (到期日剪枝)**：仅请求标的到期日列表，直接过滤丢弃 $DTE < 250$ 的非远期月份；
  - **Level 3 (行权价范围过滤)**：根据当前标的现价 $S$，仅向 API 请求或内存保留行权价在 $[0.65S, 1.35S]$ 之间的合约切片，过滤远离交易区的虚值垃圾合约。
- **内存与 QPS 控制**：采用 Python 异步生成器（Generator）进行流式处理，配合 `asyncio.Semaphore(5)` 限制最大并发，集成令牌桶限频器，彻底杜绝 OOM 与 429 封控。

### Defensive Clause 4: 混合 IV 求解器与套利边界校验 (针对 CONCERN 01)
- **套利边界预检**：在进入迭代前执行套利不等式校验：Call 价格必须满足 $\max(0, S e^{-qT} - K e^{-rT}) < C < S e^{-qT}$。违背套利边界的盘口直接标记为 `ARBITRAGE_VIOLATION`，不予求解；
- **混合算法收敛**：
  - 首选 Newton-Raphson 快速求解；
  - 当 Vega $< 10^{-6}$ 或迭代 20 次未收敛时，无缝回退至区间保证收敛的 Brent 方法（`scipy.optimize.brentq`）；
  - 极端异常无法收敛时，优雅降级标记为 `IV_UNAVAILABLE`，并保留盘口数据，严禁进程崩溃抛错。

### Defensive Clause 5: Single-Flight Token 刷新锁与盘后脏数据清洗 (针对 CONCERN 02)
- **Single-Flight 锁机制**：在 `webull.py` 中利用 `asyncio.Lock` 实现凭证刷新单飞模式。Token 即将过期或收到 401 时，仅允许首个协程执行刷新，其余并发请求排队等待新 Token，防止级联失效；
- **盘后异常挂单清洗器**：过滤规则包括：
  - 剔除 $Ask \le 0$ 或 $Bid < 0$；
  - 剔除盘口倒挂行情（$Bid > Ask$）；
  - 针对非交易时段 $Bid=0$ 且 $Ask$ 虚高的极端挂单，若价差比超过 50% 且 $OI < 100$，直接触发流动性安全门剔除。

### Defensive Clause 6: 计算/IO 线程隔离与离线网络硬拦截 (针对 CONCERN 03 & 04)
- **并发解耦**：所有 Greeks 与 IV 批量计算均通过 `fastapi.concurrency.run_in_threadpool` 或 `ProcessPoolExecutor` 调度至工作线程池，保障 FastAPI 主事件循环极速响应；
- **测试环境网络硬隔离**：在 `tests/python/conftest.py` 中注入 pytest 网络阻断夹具（禁用 `socket.socket.connect`），任何外部网络调用将被硬性拦截并报警，确保测试 100% 封闭离线；
- **混沌注入测试集**：创建 `test_adversarial_chaos.py`，专门测试畸形快照输入时的自愈与降级能力。
