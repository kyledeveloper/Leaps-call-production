# 实施方案归档目录 (Implementation Plans Archive)

本项目的所有实施方案与红蓝对抗记录均持久化在此目录下，采用 `YYYY-MM-DD_<topic>.md` 的版本命名规范进行保存。

| 方案版本 | 归档文件名 | 主题描述 | 状态 |
| :--- | :--- | :--- | :--- |
| **CSP v1.0** | [2026-09-16_cash_secured_put_scanner.md](2026-09-16_cash_secured_put_scanner.md) | **Cash Secured Put (CSP) 卖方期权量化扫描系统**：三大策略看板（收割/建仓/高IVR）、7~45 DTE、6项开关过滤器、美式Put对称定价、10大红队防御条款 (DC-CSP-1 ~ DC-CSP-10) | 🎯 **当前最新实施中** |
| **LEAPS v2.0** | [2026-09-15_leaps_call_quant_scanner_v2.md](2026-09-15_leaps_call_quant_scanner_v2.md) | **实盘深度优化版**：策略分档 (Pass/Watch/Reject)、Level 3 行权价窗策略感知分叉、美式边界修正 ($C \le S$)、历史 IV 仓库先决与降级、8 大防御性条款、6 大全局不变量 | 🟢 **已上线运行** |
| **LEAPS v1.0** | [2026-09-15_leaps_call_quant_scanner.md](2026-09-15_leaps_call_quant_scanner.md) | 初始通过红蓝对抗质询版本（BS2002 美式定价、Webull 适配、6 大防御性条款） | 📦 历史归档 |

---

### 快速导航 (Quick Navigation)
- **根目录最新版本**：[../IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md)
- **架构全景图**：
  - [../docs/arch-zh.html](../docs/arch-zh.html) (中文架构 Showcase 9/9)
  - [../docs/arch-en.html](../docs/arch-en.html) (英文架构 Showcase 9/9)
