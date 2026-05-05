# WonderTrader - 强生战法量化系统

基于 [WonderTrader](https://github.com/wondertrader/wondertrader) C++ 量化框架，集成自研「强生战法」A股技术分析策略的全自动模拟盘系统。

## 项目结构

```
├── src/WtCtaStraFact/     # CTA策略（强生战法 V1/V3）
│   ├── WtStraQS.cpp/h     # 强生战法 V1 基础版
│   └── WtStraQS_v3.cpp/h  # 强生战法 V3（+破大哥线 +止损优化）
├── run_stk_sim/           # A股全自动模拟盘
│   ├── auto_sim.py        # 全自动买卖执行脚本（cron定时）
│   ├── daily_monitor.py   # 每日监控 & 持仓报告
│   ├── config.yaml        # WonderTrader CTA引擎配置
│   └── portfolio.json     # 模拟盘持仓状态
├── backtest_qs.py         # 强生战法回测脚本
├── backtest_qs_v2.py      # 回测V2（评分版）
├── backtest_qs_v3.py      # 回测V3（三方向对比版）
├── bt_dragons/            # 龙头股批量回测
├── run_cta_sim/           # WonderTrader CTA模拟盘环境
├── dist/                  # WonderTrader 编译产物（bin/libs）
└── common/                # 公共配置（GBK→UTF-8）
```

## 强生战法核心指标

| 指标 | 说明 |
|------|------|
| 强生线 (QS) | EMA10 的 EMA10，核心趋势判断 |
| 大哥线 | MA14/MA28/MA57/MA114 均值，长期趋势参考 |
| BBI | MA3/MA6/MA12/MA24 均值，多空分界 |
| KDJ | 经典KDJ(9,3,3)，超买超卖辅助 |
| ATR(14) | 波动率，用于止损距离计算 |

**买入条件：** 强生线上穿大哥线 + BBI向上 + KDJ金叉 + 价格站上强生线

**卖出条件：**
- 破强生线 → 减仓50%
- 破大哥线 → 清仓
- KDJ死叉 + 破BBI → 清仓
- 亏损超10% → 强制止损

## 模拟盘

当前监控标的：

| 代码 | 名称 | 类型 |
|------|------|------|
| 002236 | 大华股份 | 股票 |
| 000636 | 风华高科 | 股票 |
| 159870 | 有色金属ETF | ETF |

**运行方式：** cron 每交易日 14:50 自动执行 `auto_sim.py`，完成信号计算 → 自动下单 → 盈亏记录。

**核心文件：**
- `run_stk_sim/auto_sim.py` — 自动交易主逻辑
- `run_stk_sim/daily_monitor.py` — 每日监控推送
- `run_stk_sim/sim_portfolio.json` — 持仓 & 资金状态

## 快速开始

### 编译 WonderTrader

```bash
cd src
mkdir build && cd build
cmake ..
make -j$(nproc)
```

### 运行回测

```bash
python3 backtest_qs.py        # 单标的回测
python3 backtest_qs_v3.py     # 三方向对比（V1/V3/随机）
```

### 运行模拟盘

```bash
cd run_stk_sim
python3 auto_sim.py           # 手动执行一次
# 或配置 cron 自动运行
```

## 编译环境

- OS: Ubuntu 22.04
- GCC: 11+
- CMake: 3.16+
- Python: 3.8+（akshare, pandas, numpy）

## 致谢

- [WonderTrader](https://github.com/wondertrader/wondertrader) — 高性能C++量化交易框架
- [akshare](https://github.com/akfamily/akshare) — A股数据接口

## License

MIT
