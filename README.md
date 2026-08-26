# AssetHub

> 一个界面，看清你全部的家底——股票、基金、现金，一键汇总。
> One screen for all your assets — stocks, funds and cash, all in one place.

[中文](#中文) · [English](#english)

---

## 中文

### 这个应用解决什么问题？

同时炒美股和 A 股、在好几个券商开户、还买了场外基金？每天想看一眼"我今天到底赚了多少"，是不是要打开三四个 App，对着不同货币、不同盈亏来回换算？

**AssetHub 就是来解决这个麻烦的。** 它把分散在各处的资产聚到同一个本地界面——总资产、总盈亏、当日变动一眼看清，行情自动刷新，重大新闻和异动直接推送到系统通知，不用你天天盯盘。

- 🔒 **纯本地运行**：数据存在你自己的电脑里，不经过任何服务器
- 🇨🇳🇺🇸 **中英双语**：界面、股票名、基金名一键切换

### 核心亮点

| | 解决什么 |
|---|---|
| 📊 **一屏总览** | 美股 + A股 + 美元/人民币基金 + 现金，总资产 / 总浮盈 / 当日盈亏双货币视图 |
| 📈 **实时行情** | 逐股报价覆盖盘前 / 盘中 / 盘后全时段，30 秒自动刷新，hover 卡片看分时走势 |
| 🔔 **智能提醒** | 持仓涨跌幅到 3% / 5% / 10% / 15%… 自动弹通知，标题自动识别"急速上涨 / 高位回落"等形态 |
| 📰 **新闻中心** | 美股 / A股 / 持仓个股 / 行业领域标签分类，重要新闻自动推送 |
| 🗂️ **手动管理** | 添加 / 修改 / 删除股票与基金，数据落本地 JSON 并自动备份 |
| 🌏 **多市场指标** | 纳指 / 黄金 / 原油 / 比特币 / 美债 + 上证 / 创业板 / 恒生 / 日经 / 韩国指数 |

### 快速开始

```bash
# 1. 安装依赖
pip install pywebview pyobjc-framework-Cocoa pyobjc-framework-WebKit pyobjc-framework-Quartz pyobjc-framework-UserNotifications requests

# 2. 准备持仓数据（复制示例模板，填入你的持仓）
cp sample/portfolio.json data/portfolio.json

# 3. 启动
python3 app.py
```

> 系统通知需要 `terminal-notifier`（[GitHub 下载](https://github.com/julienXX/terminal-notifier/releases) v2.0.0，放到 `bin/terminal-notifier.app`）。

### 环境要求

- macOS（通知、Dock 图标依赖系统 WebKit / 通知中心）
- Python 3.9+

### 配置持仓数据

启动前准备持仓数据：复制示例模板并填入你的持仓

```bash
cp sample/portfolio.json data/portfolio.json
# 编辑 data/portfolio.json 填入你的持仓
```

#### 数据格式

```jsonc
{
  "usd_cny_rate": 7.0,          // 美元兑人民币
  "cash_usd": 10000,            // 现金（美元）
  "stocks": [                   // 股票：美股代码大写（NVDA）/ A股带前缀（sh600584）
    { "name": "英伟达", "code": "NVDA", "type": "stock", "shares": 100, "cost_per_share": 120.0 }
  ],
  "options": [],                // 期权（可选）
  "funds": [],                  // 美元基金（可选）
  "funds_cny": []               // 人民币基金（可选）
}
```

#### 可选配置 `data/config.json`

```jsonc
{ "jin10_app_id": "你的金十快讯 app id" }   // 新闻源之一，不配置则该源自动跳过
```

### 常见问题

- **收不到系统通知**：首次使用在「系统设置 → 通知」允许通知权限
- **端口占用**：默认 8765，可设环境变量 `ASSETHUB_PORT` 修改

### 数据源与免责声明

行情 / 新闻均来自第三方公开接口（腾讯、新浪、东方财富、Nasdaq、Binance、金十快讯、华尔街见闻等），仅供个人参考，不构成投资建议。接口可能变动或限流，请合理控制刷新频率。

### License

[MIT](LICENSE)

---

## English

### The Problem

Trading US stocks and A-shares at the same time? Holding accounts across multiple brokers plus off-exchange funds? Every day you want to know *"how much did I make today"* — but it means opening three or four apps and manually converting currencies and P&L across markets.

**AssetHub solves this.** It aggregates your scattered assets into one local interface — total assets, total P&L, and daily change at a glance. Quotes refresh automatically, and important news or price swings are pushed straight to your system notifications. No more staring at charts all day.

- 🔒 **100% local**: Your data stays on your own machine — no third-party servers involved
- 🇨🇳🇺🇸 **Bilingual UI**: Switch the whole interface, stock names and fund names between Chinese and English

### Key Features

| | What it does |
|---|---|
| 📊 **Unified overview** | US stocks + A-shares + USD/CNY funds + cash; total assets / total P&L / daily change in two currencies |
| 📈 **Live quotes** | Per-stock prices across pre-market / regular / after-hours; auto-refresh every 30s; hover a card for intraday trend |
| 🔔 **Smart alerts** | System notifications when a holding moves ±3% / 5% / 10% / 15%…, with context-aware titles like "Rapid Rally" or "Pullback from High" |
| 📰 **News hub** | Tabs for US / A-share / your holdings / industry sectors; important news auto-pushed |
| 🗂️ **Manual management** | Add / edit / delete stocks and funds; data stored in local JSON with automatic backups |
| 🌏 **Global indices** | NASDAQ / Gold / Oil / Bitcoin / 30Y Treasury + SSE / ChiNext / HSI / Nikkei / KOSPI |

### Quick Start

```bash
# 1. Install dependencies
pip install pywebview pyobjc-framework-Cocoa pyobjc-framework-WebKit pyobjc-framework-Quartz pyobjc-framework-UserNotifications requests

# 2. Prepare your portfolio (copy the sample template, then fill it in)
cp sample/portfolio.json data/portfolio.json

# 3. Run
python3 app.py
```

> System notifications require `terminal-notifier` ([download](https://github.com/julienXX/terminal-notifier/releases) v2.0.0, placed at `bin/terminal-notifier.app`).

### Requirements

- macOS (notifications & Dock icon rely on system WebKit / Notification Center)
- Python 3.9+

### Portfolio Data

Prepare your holdings before first launch by copying the sample template:

```bash
cp sample/portfolio.json data/portfolio.json
# edit data/portfolio.json with your holdings
```

#### Data Format

```jsonc
{
  "usd_cny_rate": 7.0,          // USD/CNY rate
  "cash_usd": 10000,            // cash in USD
  "stocks": [                   // US codes uppercase (NVDA) / A-share with prefix (sh600584)
    { "name": "英伟达", "code": "NVDA", "type": "stock", "shares": 100, "cost_per_share": 120.0 }
  ],
  "options": [],                // options (optional)
  "funds": [],                  // USD funds (optional)
  "funds_cny": []               // CNY funds (optional)
}
```

#### Optional `data/config.json`

```jsonc
{ "jin10_app_id": "your Jin10 app id" }   // one of the news sources; skipped if not set
```

### FAQ

- **No system notifications?** Allow notification permission in System Settings → Notifications on first use
- **Port already in use?** Default is 8765; override with the `ASSETHUB_PORT` environment variable

### Data Sources & Disclaimer

Quotes / news come from third-party public APIs (Tencent, Sina, Eastmoney, Nasdaq, Binance, Jin10, Wallstreetcn, etc.). For personal reference only — **not investment advice**. APIs may change or be rate-limited; keep your refresh frequency reasonable.

### License

[MIT](LICENSE)
