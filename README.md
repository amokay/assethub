# AssetHub

个人资产监控桌面应用（macOS）——聚合股票 / 基金 / 现金资产，实时行情、K 线走势、新闻推送、涨跌异动提醒，中英文界面。

基于 `pywebview` + 零第三方依赖的 Python 后端 + 原生前端，纯本地运行（无需自建服务器）。

## 功能

- **资产总览**：总资产 / 总浮盈 / 当日盈亏，美元 / 人民币双视图
- **实时行情**：美股 + A股持仓逐股报价（盘前 / 盘中 / 盘后全时段），30s 自动刷新
- **走势图**：股票卡片 hover 分时走势、基金净值走势、市场指标（纳指 / 黄金 / 原油 / 比特币等）
- **新闻中心**：美股 / A股 / 持仓代码 / 领域分类标签，点击重拉；5 分钟自动刷新
- **系统通知**：重大新闻推送、个股涨跌幅阶梯提醒（3% / 5% / 10% / 15%…，带情境标题：急速上涨 / 高位回落等）
- **中英双语**：顶部语言切换，全站文案 + 股票 / 基金名 + 投资偏好翻译
- **手动管理**：添加 / 修改 / 删除股票与基金（数据落本地 JSON，自动备份）

## 环境要求

- macOS（通知、图标依赖系统 WebKit / 通知中心）
- Python 3.9+，依赖：`pywebview`、`pyobjc`、`requests`
- 系统通知需要 `terminal-notifier`（[GitHub 下载](https://github.com/julienXX/terminal-notifier/releases) v2.0.0，放到 `bin/terminal-notifier.app`）

## 安装与运行

```bash
pip install pywebview pyobjc-framework-Cocoa pyobjc-framework-WebKit pyobjc-framework-Quartz pyobjc-framework-UserNotifications requests
python3 app.py
```

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
