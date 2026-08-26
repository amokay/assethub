# AssetHub

> 一个界面，看清你全部的家底。**股票、基金、现金，一键汇总。**

同时炒美股和 A 股、在好几个券商开户、还买了场外基金？每天想看一眼"我今天到底赚了多少"，是不是要打开三四个 App，对着不同货币、不同盈亏来回换算？

**AssetHub 就是来解决这个麻烦的。** 它把分散在各处的资产聚到同一个本地界面——总资产、总盈亏、当日变动一眼看清，行情自动刷新，重大新闻和异动直接推送到系统通知，不用你天天盯盘。

- 🔒 **纯本地运行**：数据存在你自己的电脑里，不经过任何服务器
- 🇨🇳🇺🇸 **中英双语**：界面、股票名、基金名一键切换

## 核心亮点

| | 解决什么 |
|---|---|
| 📊 **一屏总览** | 美股 + A股 + 美元/人民币基金 + 现金，总资产 / 总浮盈 / 当日盈亏双货币视图 |
| 📈 **实时行情** | 逐股报价覆盖盘前 / 盘中 / 盘后全时段，30 秒自动刷新，hover 卡片看分时走势 |
| 🔔 **智能提醒** | 持仓涨跌幅到 3% / 5% / 10% / 15%… 自动弹通知，标题自动识别"急速上涨 / 高位回落"等形态 |
| 📰 **新闻中心** | 美股 / A股 / 持仓个股 / 行业领域标签分类，重要新闻自动推送 |
| 🗂️ **手动管理** | 添加 / 修改 / 删除股票与基金，数据落本地 JSON 并自动备份 |
| 🌏 **多市场指标** | 纳指 / 黄金 / 原油 / 比特币 / 美债 + 上证 / 创业板 / 恒生 / 日经 / 韩国指数 |

## 快速开始

```bash
# 1. 安装依赖
pip install pywebview pyobjc-framework-Cocoa pyobjc-framework-WebKit pyobjc-framework-Quartz pyobjc-framework-UserNotifications requests

# 2. 准备持仓数据（复制示例模板，填入你的持仓）
cp sample/portfolio.json data/portfolio.json

# 3. 启动
python3 app.py
```

> 系统通知需要 `terminal-notifier`（[GitHub 下载](https://github.com/julienXX/terminal-notifier/releases) v2.0.0，放到 `bin/terminal-notifier.app`）。

## 环境要求

- macOS（通知、Dock 图标依赖系统 WebKit / 通知中心）
- Python 3.9+

## 配置持仓数据

启动前准备持仓数据（二选一）：

1. **通用方式**：复制示例模板
   ```bash
   cp sample/portfolio.json data/portfolio.json
   # 编辑 data/portfolio.json 填入你的持仓
   ```
2. **QClaw 用户**：程序自动回退读取 `~/.qclaw/workspace/portfolio_template.json`（无需额外配置）

### 数据格式

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

### 可选配置 `data/config.json`

```jsonc
{ "jin10_app_id": "你的金十快讯 app id" }   // 新闻源之一，不配置则该源自动跳过
```

## 常见问题

- **收不到系统通知**：首次使用在「系统设置 → 通知」允许通知权限
- **端口占用**：默认 8765，可设环境变量 `ASSETHUB_PORT` 修改

## 数据源与免责声明

行情 / 新闻均来自第三方公开接口（腾讯、新浪、东方财富、Nasdaq、Binance、金十快讯、华尔街见闻等），仅供个人参考，不构成投资建议。接口可能变动或限流，请合理控制刷新频率。

## License

[MIT](LICENSE)
