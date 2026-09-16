# LEAPS 看涨期权量化扫描器

美股 **远月看涨（DTE ≥ 250）** 研究扫描器，三个策略分榜输出。 **不是** 自动下单系统。

[English](README.md) | [简体中文](README.zh-CN.md)

## 做什么

| 策略 | 思路 | 要点 |
|---|---|---|
| **1. 深度实值** | 代正股 / PMCC 底仓 | 行权价 0.65S–0.85S，Δ 0.70–0.85 为 PASS，Δ > 0.90 扫描前剔除。磨损 = 时间价值 / (P_exec × T)。单日 θ / P_exec 设门槛。 |
| **2. 波动率洼地** | 便宜波动上做多 Vega | 存满 ≥90 天 ATM IV 后走真 IV 分位。此前 Delayed 用 HV20 分位顶上，**最高只给 WATCH**。 |
| **3. 蓝筹超跌共振** | 核心池均值回归 | RSI / 200 日均 / 52 周 / 反弹打分。需属于核心池且 ≥200 根日线。 |

原策略四（远期异动大单）已删除：延迟行情的成交量不够当信号。

## 数据源

| 模式 | 行情 | 密钥 |
|---|---|---|
| **沙盒** | 本地样本 | 无 |
| **公开延迟** | Yahoo 日线 + Nasdaq OPRA 延迟期权链 | 无（约 15 分钟延迟，非 NBBO） |
| **Webull 实盘** | Webull OpenAPI | App Key + Secret，只留在内存，不写盘 |

不要把 Webull 密钥提交进 Git，也不要贴到聊天。`.env` 已被忽略。

Delayed / Webull 每次扫描会把各标的最接近 ATM 的 IV 追加到 `data/iv_history.json`，大约 90 个交易日后策略二切回真 IV 分位。

## 运行

需要 Python 3.9+。

```bash
cd Leaps-call-production
pip install -r requirements.txt
PYTHONPATH=. python -m src.leaps_scanner.cli --offline --serve --port 8000
```

打开看板后选择 沙盒 / 公开延迟 / Webull 实盘。

无界面命令行：

```bash
PYTHONPATH=. python -m src.leaps_scanner.cli --offline --symbols SPY,QQQ --alpha 0.5
PYTHONPATH=. python -m src.leaps_scanner.cli --strategy deep_itm --universe etfs
```

看板上的 α 滑条在内存里重排（不重新拉网）。状态筛选默认 PASS + WATCH。右上角 **EN / 中文** 开关，选择存在 `localStorage`。

扫描分档：**ETF**（12）· **道指**（30）· **核心并集**（标普100 ∪ 纳指100 ∪ 道指 ∪ ETF）。公开延迟在后台扫。

## 测试

```bash
PYTHONPATH=. python -m unittest discover -s tests/python -q
```

测试环境阻断真实网络。CI 不要依赖 Yahoo / Nasdaq / Webull 在线。

## 部署

这是一台 **常驻 Python 进程**（内存 Ranker + 后台 Delayed 扫描）。 **不要** 发到 Vercel、Netlify、Cloudflare Pages 或 Grok Publish。

可用的做法：

- 家里 Mac mini 常开 + [Tailscale](https://tailscale.com/pricing)（Personal 免费）从公司打开看板
- 需要公网 https 再上一台小 VPS（务必加门禁，不要裸挂）

个人自用不必数据库。策略二的 IV 仓库是本地 JSON。

## 定价 / Greeks

美式 call 用 Bjerksund–Stensland 2002。执行价：

`P_exec = 中间价 + α × (卖一 − 中间价)`（默认 α = 0.5）

Delta / Theta 是模型值，不是交易所 Greeks。PASS 只当观察名单，下单前到券商盘口核对。

## 许可

MIT，见 [LICENSE](LICENSE)。
