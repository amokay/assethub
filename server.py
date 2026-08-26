#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AssetHub 桌面应用后端
零第三方依赖（纯标准库），提供：
  GET /                    前端仪表盘
  GET /api/portfolio       实时持仓行情（本地模板 + 腾讯行情，缓存 45s）
  GET /api/news            新浪美股滚动新闻（缓存 5 分钟）
  GET /api/stocknews?code= 东方财富个股新闻检索（缓存 10 分钟）
  GET /api/reports         历史日报列表
  GET /api/report?date=    读取指定日期日报 HTML
"""
import json, os, glob, time, threading, datetime, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import urlopen, Request

ROOT = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(ROOT)

def _load_config():
    """读取 data/config.json（本地配置，不入库）：金十 app id 等敏感项"""
    cfg = {}
    try:
        with open(os.path.join(ROOT, "data", "config.json")) as fp:
            cfg = json.load(fp)
    except Exception:
        pass
    return cfg

def _resolve_template():
    """持仓模板：读取应用同目录 data/portfolio.json；
    缺失时自动从 sample/portfolio.json 复制示例（首次启动自动初始化）"""
    local = os.path.join(ROOT, "data", "portfolio.json")
    if not os.path.exists(local):
        try:
            sample = os.path.join(ROOT, "sample", "portfolio.json")
            if os.path.exists(sample):
                os.makedirs(os.path.dirname(local), exist_ok=True)
                import shutil
                shutil.copy(sample, local)
        except Exception:
            pass
    return local

TEMPLATE = _resolve_template()
REPORTS_DIR = os.path.join(WORKSPACE, "output")
PORT = int(os.environ.get("ASSETHUB_PORT", "8765"))

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# 个股新闻检索关键词（东方财富按中文关键词搜索效果最好）
KEYWORDS = {
    "BABA": "阿里巴巴", "PDD": "拼多多", "LMT": "洛克希德马丁", "CSCO": "思科",
    "EOSE": "Eos Energy 储能", "NTAP": "NetApp", "NVDA": "英伟达", "NASA": "Eos Energy",
    "TCOM": "携程", "TSLA": "特斯拉", "AEP": "美国电力", "TSLL": "特斯拉",
    "DRAM": "DRAM 存储芯片", "MU": "美光", "SKHY": "SK海力士", "AMZN": "亚马逊",
}

# 持仓关键词 → 股票代码（用于在要闻流里打代码标签）
KEYWORD_CODES = {
    "阿里巴巴": ["BABA"], "阿里": ["BABA"], "拼多多": ["PDD"],
    "洛克希德": ["LMT"], "洛马": ["LMT"], "思科": ["CSCO"],
    "Eos": ["EOSE", "NASA"], "NetApp": ["NTAP"], "英伟达": ["NVDA"],
    "携程": ["TCOM"], "特斯拉": ["TSLA", "TSLL"], "美国电力": ["AEP"],
    "美光": ["MU"], "海力士": ["SKHY"], "亚马逊": ["AMZN"],
    "存储": ["MU", "DRAM", "SKHY"], "DRAM": ["DRAM"],
}
HOLDING_KEYWORDS = list(KEYWORD_CODES.keys())

# ---------- 投资偏好：股票/基金 → 行业 ----------
# 美股代码 → 行业
STOCK_SECTOR = {
    "BABA": "科技互联网", "PDD": "科技互联网", "TCOM": "科技互联网",
    "LMT": "军工航天", "NASA": "军工航天",
    "CSCO": "科技硬件", "NTAP": "科技硬件",
    "NVDA": "半导体", "MU": "半导体", "SKHY": "半导体", "DRAM": "半导体",
    "TSLA": "汽车新能源", "TSLL": "汽车新能源", "EOSE": "汽车新能源",
    "AEP": "公用事业",
    "sh600584": "半导体",          # 长电科技
    "sz000977": "科技硬件",        # 浪潮信息
    "sz159516": "半导体",          # 半导体设备ETF
    "sz159583": "通信",            # 通信ETF富国
    "sh600166": "汽车新能源",      # 福田汽车
    "sh601616": "公用事业",        # 广电电气
}
# 基金名称关键词 → 行业（美元基金 + 人民币基金）
FUND_SECTOR = [
    ("世界科技", "科技互联网"), ("富兰克林科技", "科技互联网"),
    ("纳斯达克", "科技互联网"), ("人工智能", "科技互联网"),
    ("科动力", "科技互联网"),
    ("半导体", "半导体"), ("日本价值", "金融价值"),
]
# 雷达图维度（固定轴，含"其他"兜底）
RADAR_AXES = ["半导体", "科技互联网", "科技硬件", "汽车新能源", "军工航天", "公用事业", "金融价值"]

_cache = {}
_lock = threading.Lock()

def norm_code(code):
    """股票代码规范化：A股(sh/sz/bj 前缀)与港股(hk 前缀)保留小写前缀+数字，美股大写"""
    code = (code or "").strip()
    if code[:2].lower() in ("sh", "sz", "bj", "hk"):
        return code[:2].lower() + code[2:]
    return code.upper()

def cache_get(key, ttl, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val

def http_get(url, timeout=15, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=timeout) as r:
        return r.read()

# ---------- 行情 / 时段 ----------

def fetch_usd_cny():
    """实时美元兑人民币汇率（新浪外汇牌价为主，er-api 兜底），缓存 300s"""
    def build():
        try:
            raw = http_get("https://hq.sinajs.cn/list=fx_susdcny",
                           headers={"Referer": "https://finance.sina.com.cn"}).decode("gbk", "ignore")
            val = raw.split('"')[1] if '"' in raw else ""
            f = val.split(",")
            if len(f) > 3 and f[3]:
                rate = float(f[3])
                if 5 < rate < 10:
                    return {"rate": rate, "source": "sina", "ts": f[0]}
        except Exception:
            pass
        try:
            raw = http_get("https://open.er-api.com/v6/latest/USD")
            rate = float(json.loads(raw)["rates"]["CNY"])
            if 5 < rate < 10:
                return {"rate": rate, "source": "er-api", "ts": ""}
        except Exception:
            pass
        return None
    return cache_get("usd_cny", 300, build)

def session_of(ts_str, fallback_now=None):
    """根据行情时间戳判断属于盘前/盘中/盘后/夜盘。时间戳按美东时间处理。"""
    if not ts_str:
        return {"key": "after", "text": "夜盘", "icon": ""}
    try:
        dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").time()
    except ValueError:
        return {"key": "after", "text": "夜盘", "icon": ""}
    if datetime.time(4, 0) <= dt < datetime.time(9, 30):
        return {"key": "pre", "text": "盘前", "icon": ""}
    if datetime.time(9, 30) <= dt < datetime.time(16, 0):
        return {"key": "regular", "text": "盘中", "icon": ""}
    if datetime.time(16, 0) <= dt <= datetime.time(20, 0):
        return {"key": "after", "text": "盘后", "icon": ""}
    # 深夜到次日凌晨（20:00-4:00）→ 夜盘
    return {"key": "night", "text": "夜盘", "icon": ""}

# Nasdaq 扩展时段后台缓存：{ticker: data, "ts": 更新时间}
_nasdaq_cache = {}

def _fetch_sina_after_hours(us):
    """新浪 gb_ 接口（1 次批量请求，快）：盘前 f1 / 盘后 f21"""
    out = {}
    try:
        url = "https://hq.sinajs.cn/list=" + ",".join("gb_" + t.lower() for t in us)
        raw = http_get(url, headers={"Referer": "https://finance.sina.com.cn"}).decode("gbk", "ignore")
        for line in raw.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            key = line.split("=", 1)[0].replace("var hq_str_gb_", "").strip().upper()
            val = line.split("=", 1)[1].strip('"')
            f = val.split(",")
            if len(f) < 27 or not f[1] or float(f[1]) <= 0:
                continue
            try:
                # 新浪 f21：盘前时段(ET 4:00-9:30)是盘前价，盘后/深夜是盘后价 → 按时段标 session
                import datetime as _dt
                et = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=4)
                f21_sess = "pre" if et.time() < _dt.time(9, 30) else "post"
                after_price = float(f[21]) if f[21] else 0.0
                if after_price > 0:
                    # prev = 新浪现价 f1（最近交易日收盘）：新浪盘前/盘后涨跌正是相对它算的。
                    # 不能用 f26 昨收（盘前时段那是上上个交易日收盘，会算错涨跌）
                    out[key] = {"price": after_price,
                                "chg": float(f[22]) if f[22] else 0.0,
                                "pct": float(f[23]) if f[23] else 0.0,
                                "ts": f[24], "session": f21_sess,
                                "prev": float(f[1]) if f[1] else 0.0}
                else:
                    # 无盘前/盘后价：常规现价 f1，昨收 f26（兜底 f1 - f4 涨跌额）
                    prev = float(f[26]) if len(f) > 26 and f[26] else \
                        (float(f[1]) - float(f[4])) if f[4] else 0.0
                    out[key] = {"price": float(f[1]),
                                "chg": float(f[4]) if f[4] else 0.0,
                                "pct": float(f[2]) if f[2] else 0.0,
                                "ts": f[3], "session": "pre",
                                "prev": prev}
            except (ValueError, IndexError):
                continue
    except Exception:
        pass
    return out

def _fetch_nasdaq_after_hours(us):
    """Nasdaq 官方 extended-trading 接口（并行，慢）：盘前 pre / 盘后 post"""
    out = {}
    if not us:
        return out
    nasdaq_hdr = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }

    def nasdaq_one(t):
        # ETF（DRAM/NASA/TSLL 等）需 assetclass=etf，正股用 stocks：先 stocks 后 etf
        for ac in ("stocks", "etf"):
            for mt, sess in (("pre", "pre"), ("post", "post")):
                try:
                    url = ("https://api.nasdaq.com/api/quote/%s/extended-trading"
                           "?assetclass=%s&markettype=%s" % (t, ac, mt))
                    d = json.loads(http_get(url, headers=nasdaq_hdr, timeout=10))
                    data = d.get("data") or {}
                    it = (data.get("infoTable") or {}).get("rows")
                    if not it:
                        continue
                    row = it[0]
                    m = re.search(r"\$([\d.]+)\s+([+-]?[\d.]+)\s+\(([+-]?[\d.]+)%\)", row.get("consolidated", ""))
                    if not m:
                        continue
                    price = float(m.group(1))
                    chg = float(m.group(2))
                    ts = ""
                    for lu in (data.get("lastUpdateInfo") or []):
                        mm = re.search(r"updated\s+(.+?)\.\s*$", lu)
                        if mm:
                            ts = mm.group(1).strip()
                            break
                    # prev = 官方昨收（Nasdaq 涨跌相对官方昨收；腾讯/新浪昨收字段不可靠）
                    return (t, {"price": price, "chg": chg, "pct": float(m.group(3)),
                                "ts": ts, "session": sess, "prev": round(price - chg, 4)})
                except Exception:
                    continue
        return (t, None)

    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(us))) as ex:
            for t, data in ex.map(nasdaq_one, us):
                if data:
                    out[t] = data
    except Exception:
        for t in us:
            _, data = nasdaq_one(t)
            if data:
                out[t] = data
    return out

def fetch_after_hours(tickers, force=False):
    """美股扩展时段数据：先新浪快速返回，Nasdaq 异步更新缓存。
    force=True（手动刷新）时同步等待 Nasdaq 最新值，覆盖新浪。
    返回 {code: {price, chg, pct, ts, session}}"""
    us = [t for t in tickers if not t[:2] in ("sh", "sz", "bj")]
    if not us:
        return {}

    # 1. 快路径：新浪批量请求（1 次 HTTP），毫秒级返回
    out = _fetch_sina_after_hours(us)

    # 2. Nasdaq：缓存新鲜（<45s）直接用；过期仍优先旧缓存（比新浪可靠），后台刷新；
    #    手动刷新（force）同步等待最新
    import time as _time
    cached = _nasdaq_cache.get("data", {})
    stale = _time.time() - _nasdaq_cache.get("ts", 0) > 45
    if cached and not stale and not force:
        for t, v in cached.items():
            out[t] = v
    elif force:
        # 手动刷新：同步等待 Nasdaq 最新（覆盖滞后新浪数据）
        try:
            nq = _fetch_nasdaq_after_hours(us)
            if nq:
                _nasdaq_cache["data"] = nq
                _nasdaq_cache["ts"] = _time.time()
                for t, v in nq.items():
                    out[t] = v
        except Exception:
            pass
    else:
        # 缓存过期：先用旧 Nasdaq 缓存（比新浪差异小），后台异步刷新
        for t, v in cached.items():
            out[t] = v
        def _update():
            try:
                nq = _fetch_nasdaq_after_hours(us)
                if nq:
                    _nasdaq_cache["data"] = nq
                    _nasdaq_cache["ts"] = _time.time()
            except Exception:
                pass
        threading.Thread(target=_update, daemon=True).start()

    return out

def fetch_quotes(tickers):
    """美股自动加 us 前缀，A股（sh/sz/bj 前缀）原样请求；
    过滤非法代码（含非字母数字，如误填的中文名称），避免请求 URL 编码崩溃"""
    valid = [t for t in tickers
             if re.match(r"^[A-Za-z0-9]{1,12}$", t or "")]
    req = [t if t[:2] in ("sh", "sz", "bj", "hk") else "us" + t for t in valid]
    url = "https://qt.gtimg.cn/q=" + ",".join(req)
    raw = http_get(url).decode("gbk", "ignore")
    quotes = {}
    for line in raw.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key = line.split("=", 1)[0][2:]   # v_sh600584 -> sh600584, v_usBABA -> usBABA
        if key.startswith("us"):
            key = key[2:]
        val = line.split("=", 1)[1].strip('"')
        if not val:
            continue
        f = val.split("~")
        if len(f) < 35 or not f[3]:
            continue
        try:
            quotes[key] = {
                "name": f[1], "price": float(f[3]), "prev_close": float(f[4]),
                "ts": f[30],
            }
        except ValueError:
            continue
    return quotes

def get_portfolio_meta():
    """持仓静态信息（读模板，毫秒级）：先渲染卡片框架（名称等不变信息），价格到齐后再填充"""
    def build():
        with open(TEMPLATE) as fp:
            tpl = json.load(fp)
        agg_us, agg_cn, order_us, order_cn = {}, {}, [], []

        def is_cn(c):
            return c[:2] in ("sh", "sz", "bj", "hk")

        for s in tpl.get("stocks", []):
            c = s["code"]
            agg = agg_cn if is_cn(c) else agg_us
            order = order_cn if is_cn(c) else order_us
            if c not in agg:
                agg[c] = {"name": s["name"], "shares": 0, "cost": 0.0}
                order.append(c)
            agg[c]["shares"] += s["shares"]
            agg[c]["cost"] += s["shares"] * s["cost_per_share"]

        stocks = [{"code": c, "name": a["name"], "shares": a["shares"],
                   "costp": round(a["cost"] / a["shares"], 4)} for c, a in
                  [(c, agg_us[c]) for c in order_us] + [(c, agg_cn[c]) for c in order_cn]]
        funds = [{"name": f.get("name", ""), "code": f.get("code", "")}
                 for f in tpl.get("funds", [])]
        funds_cny = [{"name": f.get("name", ""), "code": f.get("code", "")}
                     for f in tpl.get("funds_cny", [])]
        # 领域 tags：持仓股票（按代码）+ 基金（按名称关键词）的行业去重，供 News tab 生成领域 tag
        tags, seen = [], set()
        for c, a in [(c, agg_us[c]) for c in order_us] + [(c, agg_cn[c]) for c in order_cn]:
            sec = _sector_of(c, a["name"])
            if sec != "其他" and sec not in seen:
                seen.add(sec)
                tags.append(sec)
        for f in list(tpl.get("funds", [])) + list(tpl.get("funds_cny", [])):
            sec = _sector_of(f.get("code", ""), f.get("name", ""))
            if sec != "其他" and sec not in seen:
                seen.add(sec)
                tags.append(sec)
        return {"stocks": stocks, "funds": funds, "funds_cny": funds_cny,
                "tags": tags, "rate": tpl.get("usd_cny_rate", 7.0)}
    return cache_get("portfolio_meta", 300, build)

def get_portfolio(force=False):
    """组合快照。force=True 时扩展时段同步取 Nasdaq 最新（手动刷新）"""
    def build():
        with open(TEMPLATE) as fp:
            tpl = json.load(fp)
        agg_us, agg_cn, order_us, order_cn = {}, {}, [], []

        def is_cn(c):
            return c[:2] in ("sh", "sz", "bj", "hk")

        for s in tpl.get("stocks", []):
            c = s["code"]
            agg = agg_cn if is_cn(c) else agg_us
            order = order_cn if is_cn(c) else order_us
            if c not in agg:
                agg[c] = {"name": s["name"], "shares": 0, "cost": 0.0}
                order.append(c)
            agg[c]["shares"] += s["shares"]
            agg[c]["cost"] += s["shares"] * s["cost_per_share"]

        all_order = order_us + order_cn
        quotes = fetch_quotes(all_order) if all_order else {}
        # 扩展时段数据（新浪）：盘前/盘后/休市时美股用新浪最新价
        after_hours = fetch_after_hours(order_us, force=force) if order_us else {}
        now_et = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
        weekday_et = now_et.weekday()
        hour_et = now_et.time()
        in_regular = weekday_et < 5 and datetime.time(9, 30) <= hour_et < datetime.time(16, 0)
        in_pre = weekday_et < 5 and datetime.time(4, 0) <= hour_et < datetime.time(9, 30)
        in_after = weekday_et < 5 and datetime.time(16, 0) <= hour_et <= datetime.time(20, 0)

        def is_cn_code(c):
            return c[:2] in ("sh", "sz", "bj", "hk")

        def cn_session():
            """A股时段：9:30-11:30 / 13:00-15:00 盘中，其余已收盘（按北京时间）"""
            now_bj = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
            if now_bj.weekday() >= 5:
                return {"key": "closed", "text": "已收盘", "icon": ""}
            t = now_bj.time()
            am = datetime.time(9, 30) <= t < datetime.time(11, 30)
            pm = datetime.time(13, 0) <= t < datetime.time(15, 0)
            return {"key": "regular", "text": "盘中", "icon": ""} if (am or pm) \
                else {"key": "closed", "text": "已收盘", "icon": ""}

        def build_stocks(agg, order):
            stocks, mv, cost, dpnl, tss = [], 0.0, 0.0, 0.0, ""
            for c in order:
                a, q = agg[c], quotes.get(c)
                if not q or q["price"] <= 0:
                    stocks.append({"code": c, "name": a["name"], "shares": a["shares"],
                                   "miss": True, "costp": round(a["cost"] / a["shares"], 3),
                                   "session": {"key": "na", "text": "--", "icon": ""}})
                    continue
                price, prev_close = q["price"], q["prev_close"]
                if is_cn_code(c):
                    # A股：只有 盘中 / 已收盘 两种状态
                    sess = cn_session()
                else:
                    # 美股昨收：盘中信任腾讯 f4（可靠）；盘前/盘后/夜盘用扩展时段基准
                    # （Nasdaq 官方 prev 优先，新浪 f1=最近收盘兜底）——腾讯 f4 在扩展时段
                    # 仍是上个交易日收盘，会让盘前涨跌算错
                    if not in_regular:
                        ah0 = after_hours.get(c)
                        if ah0 and ah0.get("prev"):
                            prev_close = ah0["prev"]
                    sess = session_of(q["ts"])
                    # 美股扩展时段：盘中用腾讯实时，盘前/盘后用 Nasdaq 官方（或新浪）最新价
                    if not in_regular and c in after_hours:
                        ah = after_hours[c]
                        if ah["price"] > 0 and abs(ah["price"] - price) / price < 0.3:
                            price = ah["price"]
                            tss = tss or ah.get("ts") or q["ts"]   # as_of 用扩展时段实际数据时间
                            sess_key = ah.get("session", "post")
                            if sess_key == "pre":
                                sess = {"key": "pre", "text": "盘前", "icon": ""}
                            elif sess_key == "post":
                                sess = {"key": "after", "text": "盘后", "icon": ""}
                            else:
                                sess = {"key": "night", "text": "夜盘", "icon": ""}
                mv_i = a["shares"] * price
                d_i = a["shares"] * (price - prev_close)
                dp = (price - prev_close) / prev_close * 100
                chg = price - prev_close
                tss = tss or q["ts"]
                mv += mv_i; cost += a["cost"]; dpnl += d_i
                stocks.append({"code": c, "name": a["name"], "shares": a["shares"],
                               "price": price, "prev": prev_close, "mv": mv_i,
                               "costp": round(a["cost"] / a["shares"], 3),
                               "pnl": mv_i - a["cost"], "day_pnl": d_i, "day_pct": dp,
                               "chg": chg, "chg_pct": dp,
                               "warn": price < a["cost"] / a["shares"], "miss": False,
                               "session": sess})
            return stocks, mv, cost, dpnl, tss

        stocks, stock_mv, stock_cost, day_pnl, ts = build_stocks(agg_us, order_us)
        stocks_cn, stock_cn_mv, stock_cn_cost, cn_day_pnl, _ = build_stocks(agg_cn, order_cn)

        opt = tpl.get("options", [])
        opt_mv = sum(o.get("mkt_value", 0) for o in opt)
        opt_cost = sum(o.get("premium_paid_total", 0) for o in opt)
        funds = tpl.get("funds", [])
        fund_mv = sum(f.get("market_value", 0) for f in funds)
        fund_cost = sum(f.get("cost", 0) for f in funds)
        funds_cny = tpl.get("funds_cny", [])
        fund_cny_mv = sum(f.get("market_value", 0) for f in funds_cny)
        fund_cny_cost = sum(f.get("cost", 0) for f in funds_cny)
        cash = tpl.get("cash_usd", 0)
        rate = tpl.get("usd_cny_rate", 7.0)
        rate_src = "template"
        fx = fetch_usd_cny()
        if fx:
            rate, rate_src = fx["rate"], fx["source"]
        grand = stock_mv + opt_mv + fund_mv + cash
        gcost = stock_cost + opt_cost + fund_cost + cash

        # 美股盘况（approximate ET，夏令时 UTC-4）
        now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=4)
        mkt_open = now.weekday() < 5 and datetime.time(9, 30) <= now.time() < datetime.time(16, 0)

        funds_out = []
        for f in funds:
            fd = {"name": f.get("name", ""), "code": f.get("code", ""),
                  "market_value": f.get("market_value", 0),
                  "cost": f.get("cost", 0),
                  "pnl": f.get("market_value", 0) - f.get("cost", 0)}
            for b in FUND_BENCH:
                if b[0] in f.get("name", ""):
                    bsp = fetch_bench_spark(b[2], b[3])
                    fd["benchmark"] = b[1]
                    fd["spark"] = bsp["values"]
                    fd["nav_dates"] = bsp["dates"]
                    break
            funds_out.append(fd)

        # 人民币基金（场外基金，按最近记录市值/收益率计价 + 天天基金真实净值走势）
        funds_cny_out = []
        for f in funds_cny:
            fc = {"name": f.get("name", ""), "code": f.get("code", ""),
                  "market_value": f.get("market_value", 0),
                  "cost": f.get("cost", 0),
                  "cost_per_nav": f.get("cost_per_nav", 0),
                  "yield_pct": f.get("yield_pct", 0),
                  "pnl": f.get("market_value", 0) - f.get("cost", 0)}
            nav = fetch_cn_fund_nav(f.get("code", ""))
            fc["spark"] = nav["values"]
            fc["nav_dates"] = nav["dates"]
            funds_cny_out.append(fc)

        # 个股涨跌幅阶梯推送（3%/5%/10%/15%...，随 30s 行情刷新检查）
        try:
            _check_price_alerts([s for s in stocks if not s.get("miss")])
            _check_price_alerts([s for s in stocks_cn if not s.get("miss")])
        except Exception:
            pass

        return {
            "as_of": ts, "rate": rate, "rate_source": rate_src, "market_open": mkt_open,
            "stocks": sorted([s for s in stocks if not s.get("miss")], key=lambda x: -x["mv"])
                      + [s for s in stocks if s.get("miss")],
            "stock_total": {"mv": stock_mv, "cost": stock_cost,
                            "pnl": stock_mv - stock_cost, "day_pnl": day_pnl},
            "stocks_cn": sorted([s for s in stocks_cn if not s.get("miss")], key=lambda x: -x["mv"])
                         + [s for s in stocks_cn if s.get("miss")],
            "stock_total_cn": {"mv": stock_cn_mv, "cost": stock_cn_cost,
                               "pnl": stock_cn_mv - stock_cn_cost, "day_pnl": cn_day_pnl},
            "funds_cny": funds_cny_out,
            "cn_totals": {"mv": stock_cn_mv + fund_cny_mv,
                          "cost": stock_cn_cost + fund_cny_cost,
                          "pnl": (stock_cn_mv - stock_cn_cost) + (fund_cny_mv - fund_cny_cost),
                          "pnl_pct": ((stock_cn_mv + fund_cny_mv) - (stock_cn_cost + fund_cny_cost))
                                     / (stock_cn_cost + fund_cny_cost) * 100
                                     if (stock_cn_cost + fund_cny_cost) else 0,
                          "day_pnl": cn_day_pnl},
            "options": [{"name": o.get("name", ""), "contracts": o.get("contracts", 0),
                         "mkt_value": o.get("mkt_value", 0), "cost": o.get("premium_paid_total", 0),
                         "expiry": o.get("expiry", ""),
                         "pnl": o.get("mkt_value", 0) - o.get("premium_paid_total", 0)} for o in opt],
            "funds": funds_out,
            "cash": cash,
            "totals": {"mv": grand, "mv_cny": grand * rate, "cost": gcost,
                       "pnl": grand - gcost, "pnl_pct": (grand - gcost) / gcost * 100,
                       "day_pnl": day_pnl},
        }
    return cache_get("portfolio", 30, build)

# ---------- 核心市场指标 ----------

# 基金 → 参考指数（境外基金无公开净值历史，用对应市场指数走势近似）
FUND_BENCH = [
    # (基金名包含, 参考指数名, 数据源, 参数)
    ("贝莱德", "参考: 纳指", "qq", "usIXIC"),
    ("富兰克林", "参考: 纳指100", "qq", "usNDX"),
    ("富达", "参考: 日经225", "em", "100.N225"),
]

_FRED_CACHE = {}   # {fred_id: {"ts": 时间戳, "data": [(date, value)...]}}
_KS_CACHE = {"data": []}   # 韩国指数最近成功数据（东财限流时兜底）

def _fetch_fred(fred_id, days=45):
    """FRED CSV 抓取（curl 子进程，绕过 python 反爬；cosd 只取近 days 天加速）。
    返回 [(date, value)] 日期升序；缓存 6 小时（FRED 每日更新）"""
    now = time.time()
    c = _FRED_CACHE.get(fred_id)
    if c and c.get("data") and now - c["ts"] < 21600:
        return c["data"]
    try:
        import subprocess
        cosd = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        # 沙箱代理（HTTPS_PROXY=127.0.0.1:xxxx）会让 curl 走代理访问 FRED 失败（HTTP/2 error），
        # 清掉代理环境变量强制直连才可用
        env = {k: v for k, v in os.environ.items()
               if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
               and not k.upper().endswith("_PROXY")}
        out = subprocess.check_output(
            ["curl", "-s", "--http1.1", "--max-time", "20",
             "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s&cosd=%s" % (fred_id, cosd)],
            timeout=25, env=env).decode("utf-8", "ignore")
        rows = []
        for line in out.strip().splitlines():
            if not line or line.startswith("observation_date") or line.startswith("//"):
                continue
            parts = line.split(",")
            if len(parts) >= 2 and parts[1]:
                try:
                    rows.append((parts[0], float(parts[1])))
                except ValueError:
                    continue
        if len(rows) >= 2:
            _FRED_CACHE[fred_id] = {"data": rows, "ts": now}
            return rows
    except Exception:
        pass
    c = _FRED_CACHE.get(fred_id)
    return c["data"] if c and c.get("data") else []

def fetch_bench_spark(kind, code, tries=3):
    """参考指数近 30 日收盘价序列（qq=腾讯美股指数, em=东财全球指数）。
    东财接口偶发拒绝，自动重试；多次失败返回空列表。
    返回 {"values": [...], "dates": [...]}（日期升序，最近在末尾）"""
    for attempt in range(tries):
        try:
            if kind == "qq":
                raw = http_get("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?"
                               "param=%s,day,,,30,qfq" % code)
                days = json.loads(raw).get("data", {}).get(code, {}).get("day") or []
                closes = [(x[0], float(x[2])) for x in days if len(x) > 2 and x[2]]
                if closes:
                    return {"values": [c for _, c in closes],
                            "dates": [d for d, _ in closes]}
            if kind == "em":
                # 东财 kline：https 在 GUI 进程被 TLS 反爬拒绝，改用 http 直连；
                # requests 需 trust_env=False 忽略 macOS 系统代理
                url = ("http://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
                       "&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55&klt=101&fqt=1"
                       "&end=20500101&lmt=30" % code)
                closes = []
                try:
                    import requests as _req
                    s = _req.Session()
                    s.trust_env = False
                    r = s.get(url, headers={"User-Agent": UA}, timeout=10)
                    klines = (r.json().get("data") or {}).get("klines") or []
                    closes = [(k.split(",")[0], float(k.split(",")[2]))
                              for k in klines if len(k.split(",")) > 2]
                except Exception:
                    closes = []
                if closes:
                    return {"values": [c for _, c in closes],
                            "dates": [d for d, _ in closes]}
                # 东财失败 → FRED（圣路易斯联储，免费 CSV）兜底：日经225
                fred_id = {"100.N225": "NIKKEI225"}.get(code)
                if fred_id:
                    rows = _fetch_fred(fred_id)
                    if len(rows) >= 2:
                        return {"values": [v for _, v in rows[-30:]],
                                "dates": [d for d, _ in rows[-30:]]}
        except Exception:
            pass
        if attempt < tries - 1:
            time.sleep(0.6)
    return {"values": [], "dates": []}

def fetch_cn_fund_nav(code, tries=3):
    """人民币基金真实净值近 30 日序列（天天基金 pingzhongdata 接口）。
    返回 {"values": [净值...], "dates": ["YYYY-MM-DD"...]}（按日期升序，最近在末尾）。
    基金净值每天更新一次即可 → 缓存 1 天（86400s）"""
    if not code:
        return {"values": [], "dates": []}
    def build():
        for attempt in range(tries):
            try:
                url = "https://fund.eastmoney.com/pingzhongdata/%s.js" % code
                req = Request(url, headers={"User-Agent": UA,
                                            "Referer": "https://fund.eastmoney.com/%s.html" % code})
                raw = urlopen(req, timeout=12).read().decode("utf-8", "ignore")
                m = re.search(r"var Data_netWorthTrend\s*=\s*(\[.*?\]);", raw, re.S)
                if not m:
                    return {"values": [], "dates": []}
                data = json.loads(m.group(1))
                pairs = [(d.get("x"), d.get("y")) for d in data if d.get("y")]
                if not pairs:
                    return {"values": [], "dates": []}
                pairs = pairs[-30:]
                values = [float(y) for _, y in pairs]
                dates = [datetime.datetime.utcfromtimestamp(x / 1000).strftime("%Y-%m-%d")
                         for x, _ in pairs]
                return {"values": values, "dates": dates}
            except Exception:
                pass
            if attempt < tries - 1:
                time.sleep(0.6)
        return {"values": [], "dates": []}
    return cache_get("fund_nav_" + code, 86400, build)

def _intraday_qq(code, total_minutes):
    """腾讯当日分时（0930 起逐分钟增量）：返回 (pts, progress)；失败返回 (None, None)"""
    try:
        raw = http_get("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/minute/query?code=%s" % code)
        dd = (json.loads(raw).get("data", {}).get(code, {}).get("data") or {})
        rows = dd.get("data") or []
        pts = []
        for r in rows:
            f = r.split(" ")
            if len(f) >= 2:
                try:
                    pts.append(float(f[1]))
                except ValueError:
                    continue
        if len(pts) >= 2:
            return pts, round(min(1.0, len(pts) / total_minutes), 3)
    except Exception:
        pass
    return None, None

def _sina_fut_minute(symbol, total_minutes=1380):
    """新浪外盘期货当日分时（minLine_1d，CME 近 24h 交易 ≈1380 分钟）：
    返回 (pts, progress)；失败返回 (None, None)"""
    try:
        raw = http_get("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
                       "var%20t=/GlobalFuturesService.getGlobalFuturesMinLine?symbol=" + symbol)
        m = re.search(r"\((.*)\)\s*;?\s*$", raw.decode("utf-8", "ignore"), re.S)
        if m:
            d = json.loads(m.group(1))
            rows = d.get("minLine_1d") or []
            pts = []
            for r in rows:
                if len(r) >= 2 and r[1]:
                    try:
                        pts.append(float(r[1]))
                    except ValueError:
                        continue
            if len(pts) >= 2:
                return pts, round(min(1.0, len(pts) / total_minutes), 3)
    except Exception:
        pass
    return None, None

def get_markets():
    """纳指 / 恐慌指数 / 黄金 / 原油 / 比特币 一行核心指标 + 迷你趋势线"""
    def build():
        items = []
        sparks = {}

        def add(code, label, price, prev, extra=""):
            if not price or price <= 0 or not prev:
                return
            chg = price - prev
            pct = chg / prev * 100
            items.append({"code": code, "label": label,
                          "price": round(price, 2), "chg": round(chg, 2),
                          "chg_pct": round(pct, 2), "extra": extra})

        # 纳指：腾讯美股指数（符号 .IXIC，单独解析）
        try:
            raw = http_get("https://qt.gtimg.cn/q=usIXIC").decode("gbk", "ignore")
            for line in raw.strip().split(";"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key = line.split("=", 1)[0].split("_")[-1]
                if key.startswith("us"):
                    key = key[2:]
                val = line.split("=", 1)[1].strip('"')
                f = val.split("~")
                if len(f) < 5 or not f[3] or not f[4]:
                    continue
                price, prev = float(f[3]), float(f[4])
                if key == "IXIC":
                    add("IXIC", "纳指", price, prev, "点")
        except Exception:
            pass
        # 纳指趋势线：腾讯新 K 线接口（30 日收盘）
        try:
            raw = http_get("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=usIXIC,day,,,30,qfq")
            days = json.loads(raw).get("data", {}).get("usIXIC", {}).get("day") or []
            sparks["IXIC"] = [float(x[2]) for x in days if len(x) > 2 and x[2]]
        except Exception:
            pass

        # 黄金 / 原油：腾讯期货 hf_ 逗号格式（f0=现价 f1=涨跌额）
        try:
            raw = http_get("https://qt.gtimg.cn/q=hf_GC,hf_CL").decode("gbk", "ignore")
            for line in raw.strip().split(";"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key = line.split("=", 1)[0].split("_")[-1]
                val = line.split("=", 1)[1].strip('"')
                f = val.split(",")
                if len(f) < 6 or not f[0] or not f[1]:
                    continue
                price, chg = float(f[0]), float(f[1])
                if key == "GC":
                    add("GC", "黄金", price, price - chg, "美元/盎司")
                elif key == "CL":
                    add("CL", "原油", price, price - chg, "美元/桶")
        except Exception:
            pass
        # 黄金 / 原油走势：当日分时（实时刷新）但全宽显示（不按进度裁切）
        for sym, code in (("GC", "GC"), ("CL", "CL")):
            pts, _ = _sina_fut_minute(sym)
            if pts:
                sparks[code] = pts
            else:
                try:
                    raw = http_get("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
                                   "var%20t=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=" + sym)
                    m = re.search(r"\((.*)\)\s*;?$", raw.decode("utf-8", "ignore"), re.S)
                    if m:
                        d = json.loads(m.group(1))
                        if d:
                            sparks[code] = [float(x["close"]) for x in d[-30:] if x.get("close")]
                except Exception:
                    pass

        # 比特币：Binance 公共行情数据 + 近 24h 走势（1 小时K线，24h 交易无进度留白）
        try:
            raw = http_get("https://data-api.binance.vision/api/v3/ticker/24hr?symbol=BTCUSDT")
            d = json.loads(raw)
            add("BTC", "比特币", float(d["lastPrice"]), float(d["prevClosePrice"]), "美元")
            raw = http_get("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=24")
            sparks["BTC"] = [float(x[4]) for x in json.loads(raw)]
        except Exception:
            pass

        # 30年期美债收益率（美国30年国债，FRED DGS30 每日更新，替代原恐慌指数）
        try:
            rows = _fetch_fred("DGS30")
            vals = [v for _, v in rows]
            if len(vals) >= 2:
                add("US30Y", "30年期美债", vals[-1], vals[-2], "%")
                sparks["US30Y"] = vals[-30:]
        except Exception:
            pass

        # ===== A股/港股市场指标 =====
        cn = []
        cn_sparks = {}

        def add_cn(code, label, price, prev, extra=""):
            if not price or price <= 0 or not prev:
                return
            chg = price - prev
            pct = chg / prev * 100
            cn.append({"code": code, "label": label, "price": round(price, 2),
                       "chg": round(chg, 2), "chg_pct": round(pct, 2), "extra": extra})

        # 上证 / 创业板 / 恒生：腾讯实时 + 腾讯日K
        try:
            raw = http_get("https://qt.gtimg.cn/q=sh000001,sz399006,hkHSI").decode("gbk", "ignore")
            for line in raw.strip().split(";"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key = line.split("=", 1)[0][2:]
                val = line.split("=", 1)[1].strip('"')
                f = val.split("~")
                if len(f) < 5 or not f[3] or not f[4]:
                    continue
                price, prev = float(f[3]), float(f[4])
                if key == "sh000001":
                    add_cn("sh000001", "上证指数", price, prev, "点")
                    pts, prog = _intraday_qq("sh000001", 240)
                    cn_sparks["sh000001"] = {"pts": pts, "progress": _session_progress("cn")} if pts else \
                        fetch_bench_spark("qq", "sh000001")["values"]
                elif key == "sz399006":
                    add_cn("sz399006", "创业板指", price, prev, "点")
                    pts, prog = _intraday_qq("sz399006", 240)
                    cn_sparks["sz399006"] = {"pts": pts, "progress": _session_progress("cn")} if pts else \
                        fetch_bench_spark("qq", "sz399006")["values"]
                elif key == "hkHSI":
                    add_cn("hkHSI", "恒生指数", price, prev, "点")
                    pts, prog = _intraday_qq("hkHSI", 330)
                    cn_sparks["hkHSI"] = {"pts": pts, "progress": _session_progress("hk")} if pts else \
                        fetch_bench_spark("qq", "hkHSI")["values"]
        except Exception:
            pass

        # 日经指数（日经225）：新浪实时 + 东财日K走势
        try:
            raw = http_get("https://hq.sinajs.cn/list=int_nikkei",
                           headers={"Referer": "https://finance.sina.com.cn"}).decode("gbk", "ignore")
            val = raw.split('"')[1] if '"' in raw else ""
            f = val.split(",")
            if len(f) > 3 and f[1] and f[3]:
                price = float(f[1])           # 现价
                chg = float(f[2]) if f[2] else 0.0   # 涨跌额
                prev = price - chg            # 昨收
                add_cn("N225", "30日日经指数", price, prev, "点")
                nk = fetch_bench_spark("em", "100.N225")["values"]
                if len(nk) > 1:
                    cn_sparks["N225"] = nk
        except Exception:
            pass

        # 韩国指数（KOSPI）：NAVER 财经（韩国门户官方源，当日收盘 + 近 35 交易日），
        # 东财 KS11 作备用（恢复后优先），限流时用最近缓存兜底
        try:
            raw = http_get("https://m.stock.naver.com/api/index/KOSPI/price?pageSize=35&page=1",
                           headers={"User-Agent": UA, "Referer": "https://m.stock.naver.com/"})
            arr = json.loads(raw)
            if arr:
                rows = []
                for it in arr:
                    try:
                        rows.append((it["localTradedAt"], float(it["closePrice"].replace(",", ""))))
                    except (KeyError, ValueError):
                        continue
                rows.sort()
                vals = [v for _, v in rows]
                if len(vals) >= 2:
                    _KS_CACHE["data"] = vals
                    add_cn("KS11", "30日韩国指数", vals[-1], vals[-2], "KOSPI")
                    cn_sparks["KS11"] = vals[-30:]
        except Exception:
            pass
        if "KS11" not in cn_sparks:
            # 备用：东财 KS11（无实时源，用最近交易日收盘）或最近缓存
            ks = []
            try:
                ks = fetch_bench_spark("em", "100.KS11")["values"]
            except Exception:
                ks = []
            if len(ks) > 1:
                _KS_CACHE["data"] = ks
            elif not ks and _KS_CACHE.get("data"):
                ks = _KS_CACHE["data"]
            if len(ks) > 1:
                add_cn("KS11", "30日韩国指数", ks[-1], ks[-2], "KOSPI")
                cn_sparks["KS11"] = ks

        for it in cn:
            it["spark"] = cn_sparks.get(it["code"], [])

        for it in items:
            it["spark"] = sparks.get(it["code"], [])
        return {"us": items, "cn": cn}
    return cache_get("markets", 30, build)

# ---------- 投资偏好 ----------

def _sector_of(code, name):
    """返回持仓标的的行业（股票按代码、基金按名称关键词）"""
    s = STOCK_SECTOR.get(code)
    if s:
        return s
    for kw, sec in FUND_SECTOR:
        if kw in (name or ""):
            return sec
    return "其他"

def get_preference():
    """投资偏好：按 美元/人民币 分组，各自输出行业占比 + 雷达图"""
    def build():
        pf = get_portfolio()
        rate = pf.get("rate") or 7.0
        # (键, 行业, 市值) —— 人民币资产按本币口径（不换算，与页面人民币视图一致）
        groups = {
            "us": [],   # 美元：美股 + 美元基金
            "cn": [],   # 人民币：A股 + 人民币基金
        }
        for s in pf.get("stocks", []):
            groups["us"].append((_sector_of(s.get("code"), s.get("name")), s.get("mv", 0)))
        for f in pf.get("funds", []):
            groups["us"].append((_sector_of(f.get("code"), f.get("name")), f.get("market_value", 0)))
        for s in pf.get("stocks_cn", []):
            groups["cn"].append((_sector_of(s.get("code"), s.get("name")), s.get("mv", 0)))
        for f in pf.get("funds_cny", []):
            groups["cn"].append((_sector_of(f.get("code"), f.get("name")), f.get("market_value", 0)))

        def aggregate(rows):
            agg = {}
            total = 0.0
            for sec, mv in rows:
                agg[sec] = agg.get(sec, 0) + mv
                total += mv
            if not total:
                return {"industries": [], "radar": [], "total": 0}
            industries = [{"name": k, "mv": round(v, 0), "pct": round(v / total * 100, 1)}
                          for k, v in sorted(agg.items(), key=lambda x: -x[1])]
            radar = [{"axis": a, "pct": round(agg.get(a, 0) / total * 100, 1)} for a in RADAR_AXES]
            other = agg.get("其他", 0) / total * 100
            if other > 0:
                radar.append({"axis": "其他", "pct": round(other, 1)})
            return {"industries": industries, "radar": radar, "total": round(total, 0)}

        return {"us": aggregate(groups["us"]), "cn": aggregate(groups["cn"]),
                "rate": round(rate, 4)}
    return cache_get("preference", 45, build)

# ---------- 股票K线（hover 渐现） ----------

def _session_progress(market):
    """按当前时间计算交易进度 0-1（统一时间基准，不依赖各股成交条数）。
    us=美股（ET 4:00-9:30 盘前/9:30-16:00 盘中/16:00-20:00 盘后），
    cn=A股（北京 9:30-11:30+13:00-15:00，全天240分钟），hk=港股（9:30-12:00+13:00-16:00，全天330分钟）"""
    import datetime as _dt
    def _p(mins, start, total):
        return round(min(1.0, max(0.0, (mins - start) / total)), 3)
    if market == "us":
        et = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=4)
        mins = et.hour * 60 + et.minute
        if mins < 240: return 0.0
        if mins < 570: return _p(mins, 240, 330)      # 盘前 4:00-9:30
        if mins < 960: return _p(mins, 570, 390)      # 盘中 9:30-16:00
        return 1.0
    now = _dt.datetime.now()
    mins = now.hour * 60 + now.minute
    if market == "cn":
        if mins < 570: return 0.0
        if mins <= 690: return _p(mins, 570, 240)     # 上午 9:30-11:30
        if mins < 780: return 0.5                     # 午休
        if mins <= 900: return _p(mins, 780, 240) + 0.5  # 下午 13:00-15:00
        return 1.0
    if market == "hk":
        if mins < 570: return 0.0
        if mins <= 720: return _p(mins, 570, 330)     # 上午 9:30-12:00
        if mins < 780: return _p(720, 570, 330)       # 午休 12:00-13:00
        if mins <= 960: return _p(mins, 780, 330) + _p(720, 570, 330)  # 下午 13:00-16:00
        return 1.0
    return 1.0

def _fill_minutes_idx(items, start_mins, end_mins):
    """按分钟网格补全分时序列：items=[(分钟索引, price)] 升序，缺失分钟用前值填充。
    返回价格序列（长度 = end-start+1，所有标的同一时间轴）"""
    out = []
    cur = start_mins
    i = 0
    n = len(items)
    last = None
    while cur <= end_mins:
        while i < n and items[i][0] < cur:
            last = items[i][1]
            i += 1
        if i < n and items[i][0] == cur:
            out.append(items[i][1])
            last = items[i][1]
            i += 1
        elif last is not None:
            out.append(last)
        cur += 1
    return [v for v in out if v is not None]

def _fill_minutes_ts(rows, start_ts, now_ts):
    """按 epoch 毫秒分钟网格补全：rows=[(ts_ms, price)] 升序，缺失分钟用前值。
    返回价格序列（长度 = (now-start)/60000+1，所有标的同一时间轴）"""
    out = []
    cur = start_ts
    i = 0
    n = len(rows)
    last = None
    while cur <= now_ts:
        while i < n and rows[i][0] < cur - 30000:
            last = rows[i][1]
            i += 1
        if i < n and abs(rows[i][0] - cur) <= 30000:
            out.append(rows[i][1])
            last = rows[i][1]
            i += 1
        elif last is not None:
            out.append(last)
        cur += 60000
    return [v for v in out if v is not None]

def get_stock_klines():
    """全部持仓股票当日分时序列 + 交易进度，供卡片 hover 渐现盘中实时走势。
    返回 {code: {"pts": [现价...], "progress": 0-1}}（progress = 已交易分钟/全天分钟）。
    A股：腾讯 minute（0930 起增量）；美股：东财 trends2（105.代码，21:30 起增量）。
    并行请求，缓存 2 分钟（盘中 30s 刷新时能跟上进度）"""
    def build():
        with open(TEMPLATE) as fp:
            tpl = json.load(fp)
        codes = []
        for s in tpl.get("stocks", []):
            c = s["code"]
            if c not in codes:
                codes.append(c)
        out = {}

        def is_cn(c):
            return c[:2] in ("sh", "sz", "bj", "hk")

        def one(c):
            try:
                if is_cn(c):
                    # A股：腾讯分时（0930 起逐分钟增量）；按分钟网格补全 9:30→当前（含午休），
                    # 所有 A股标的同一时间轴（起始 9:30、结束当前/收盘 15:00、进度一致）
                    url = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/minute/query?code=%s" % c)
                    d = json.loads(http_get(url, timeout=10))
                    dd = (d.get("data", {}).get(c, {}).get("data") or {})
                    rows = dd.get("data") or []
                    items = []
                    for r in rows:
                        f = r.split(" ")
                        if len(f) >= 2 and len(f[0]) >= 4:
                            try:
                                mins = int(f[0][:2]) * 60 + int(f[0][2:4])
                                items.append((mins, float(f[1])))
                            except ValueError:
                                continue
                    if items:
                        now = datetime.datetime.now()
                        now_mins = now.hour * 60 + now.minute
                        end_mins = min(now_mins, 900) if now_mins >= 570 else 570
                        pts = _fill_minutes_idx(items, 570, max(570, end_mins))
                        if pts:
                            return (c, {"pts": pts, "progress": _session_progress("cn")})
                else:
                    # 美股：Nasdaq 官方 intraday chart（1 分钟粒度，覆盖盘前 4:00 起；
                    # 盘前取 4:00 起的盘前分时，开盘后取 9:30 起的盘中分时）。东财 trends2 备用。
                    import datetime as _dt
                    pts = []
                    # 当前美东时段（Nasdaq 失败时备用分支也用它算进度）
                    et = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=4)
                    cur = et.time()
                    nasdaq_hdr = {
                        "User-Agent": UA,
                        "Accept": "application/json, text/plain, */*",
                        "Origin": "https://www.nasdaq.com",
                        "Referer": "https://www.nasdaq.com/",
                    }
                    try:
                        chart = []
                        # ETF（NASA/TSLL/DRAM 等）需用 assetclass=etf；正股用 stocks
                        for ac in ("stocks", "etf"):
                            url = ("https://api.nasdaq.com/api/quote/%s/chart?assetclass=%s&type=intraday" % (c, ac))
                            d = json.loads(http_get(url, headers=nasdaq_hdr, timeout=10))
                            chart = (d.get("data") or {}).get("chart") or []
                            if chart:
                                break
                        # 按当前美东时段过滤：盘中/盘后取 9:30 开盘后的分时；盘前/凌晨取 4:00 起的盘前分时
                        if cur >= _dt.time(9, 30):
                            start_ts = int(et.replace(hour=9, minute=30, second=0, microsecond=0).timestamp() * 1000)
                            denom = 390.0
                        else:
                            start_ts = int(et.replace(hour=4, minute=0, second=0, microsecond=0).timestamp() * 1000)
                            denom = 330.0
                        # 按分钟网格补全 start_ts→当前：所有美股同一时间轴（起始/结束/进度一致）
                        rows_ts = [(row.get("x", 0), float(row["y"])) for row in chart
                                   if row.get("x", 0) >= start_ts and row.get("y")]
                        now_ts = int(et.timestamp() * 1000)
                        pts = _fill_minutes_ts(rows_ts, start_ts, now_ts)
                    except Exception:
                        pts = []
                    if not pts:
                        # 备用：东财 trends2 分时（北京 21:30 起增量）
                        import requests as _req
                        s = _req.Session(); s.trust_env = False
                        try:
                            url = ("http://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=105.%s"
                                   "&fields1=f1,f2,f3,f7,f8,f9,f10,f11,f12,f13"
                                   "&fields2=f51,f52,f53,f54,f55,f56,f57,f58&ndays=1&iscr=0" % c)
                            r = s.get(url, headers={"User-Agent": UA}, timeout=8)
                            for row in (r.json().get("data") or {}).get("trends") or []:
                                f = row.split(",")
                                if len(f) >= 2:
                                    try:
                                        pts.append(float(f[1]))  # f52=现价
                                    except ValueError:
                                        continue
                        except Exception:
                            pts = []
                    if pts:
                        # 备用东财兜底的数据无时段概念（东财 trends2 从 21:30 起），统一按分母估算进度
                        dnm = 390.0 if cur >= _dt.time(9, 30) else 330.0
                        return (c, {"pts": pts, "progress": _session_progress("us")})
            except Exception:
                pass
            return (c, None)

        try:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(8, len(codes))) as ex:
                for c, data in ex.map(one, codes):
                    if data:
                        out[c] = data
        except Exception:
            for c in codes:
                _, data = one(c)
                if data:
                    out[c] = data
        return out
    return cache_get("stock_klines", 120, build)

# ---------- 重要新闻推送（macOS 系统通知） ----------
# 重大行情关键词：命中即视为重要新闻（含"持仓相关"的新闻自动重要）
IMPORTANT_KW = ["暴涨", "暴跌", "大涨", "大跌", "涨停", "跌停", "财报", "业绩", "评级",
                "收购", "并购", "回购", "增持", "减持", "破产", "违约", "退市",
                "上调", "下调", "警告", "调查", "罚款", "断供", "禁令", "创新高", "创新低",
                "突破", "预警", "重组", "仲裁", "起诉"]
_notified_titles = set()      # 已通知新闻标题去重
_last_notify_ts = [0.0]       # 上次通知时间（频率限制）

TN_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "bin", "terminal-notifier.app", "Contents", "MacOS", "terminal-notifier")

def _osx_notify(msg, title="AssetHub 重要新闻"):
    """macOS 系统通知：优先 terminal-notifier（-sender 指定 AssetHub bundle，
    通知图标=Dock 同款 logo，尺寸系统自适应）；失败兜底 osascript（图标为脚本编辑器）"""
    import subprocess as _sp
    if os.path.exists(TN_BIN):
        try:
            _sp.call([TN_BIN, "-message", msg[:150], "-title", title[:50],
                      "-sender", "com.assethub.app", "-sound", "default"],
                     stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"), timeout=8)
            return
        except Exception:
            pass
    try:
        msg = msg.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")
        title = title.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")
        _sp.call(["osascript", "-e",
                  'display notification "%s" with title "%s"' % (msg, title)],
                 stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"), timeout=8)
    except Exception:
        pass

def _push_important_news(news):
    """对新增的重要新闻发系统通知：持仓相关(related非空) 或 命中重大关键词。
    首次预热只记录不通知（避免启动轰炸）；60s 内最多 1 条"""
    import time as _tm
    now = _tm.time()
    # 首次调用：预热当前已有重要新闻，标记已通知，不弹
    if not _notified_titles:
        for n in news:
            if n.get("related") or any(k in n["title"] for k in IMPORTANT_KW):
                _notified_titles.add(n["title"][:50])
        return
    for n in news:
        codes = n.get("related") or []
        title = n.get("title", "")
        if not (codes or any(k in title for k in IMPORTANT_KW)):
            continue
        h = title[:50]
        if h in _notified_titles:
            continue
        if now - _last_notify_ts[0] < 60:
            continue
        _notified_titles.add(h)
        if len(_notified_titles) > 300:
            _notified_titles.clear()
        _last_notify_ts[0] = now
        tag = ("持仓 " + "、".join(codes[:3])) if codes else "重要"
        _osx_notify("%s｜%s" % (tag, title[:90]))
    return

# ---------- 个股涨跌幅阶梯推送 ----------
# 当日涨跌幅达到 3% / 5% / 10% / 15% / 20% ...（5 的倍数）时推送系统通知。
# 每档每形态只推一次（内存去重，重启清空）；30s 限频防风暴。
# 标题按情境：急速上涨/急速下跌(单次 ≥10%)、高位回落(曾高位明显回落)、
#             低位反弹(曾深跌明显反弹)、默认「股票异动」。
PRICE_ALERT_STEPS = [3, 5] + list(range(10, 51, 5))   # 3,5,10,15,...,50
_price_alerted = {}       # code -> set(已推送 key："+3" / "retreat@+5" 等)
_price_state = {"date": "", "stocks": {}}   # code -> {high: 当日曾达最高 signed 档, low: 当日曾达最低 signed 档}
_last_price_alert_ts = [0.0]

def _check_price_alerts(stocks):
    """持仓个股涨跌幅阶梯推送：|chg_pct| 达档位首次触发推一条，标题按涨跌情境变化"""
    import time as _tm
    import datetime as _dt
    now = _tm.time()
    # 跨日重置当日形态状态（昨日的涨跌不应影响今天的高位/低位判断）
    today = _dt.date.today().isoformat()
    if _price_state["date"] != today:
        _price_state["date"] = today
        _price_state["stocks"] = {}
    for s in stocks:
        if s.get("miss") or s.get("chg_pct") is None:
            continue
        c = s["code"]
        pct = s["chg_pct"]
        absp = abs(pct)
        step = None
        for st in PRICE_ALERT_STEPS:
            if absp >= st:
                step = st
            else:
                break
        if step is None:
            continue
        signed = step if pct > 0 else -step
        st = _price_state["stocks"].setdefault(c, {"high": 0, "low": 0})
        prev_high, prev_low = st["high"], st["low"]
        st["high"] = max(prev_high, signed)
        st["low"] = min(prev_low, signed)

        # 情境 → 标题（优先级：回落/反弹 > 急速 > 默认）
        title, morph = "AssetHub 股票异动", None
        if prev_high >= 10 and signed <= prev_high - 5:
            title, morph = "AssetHub 高位回落", ("retreat", signed)
        elif prev_low <= -10 and signed >= prev_low + 5:
            title, morph = "AssetHub 低位反弹", ("bounce", signed)
        elif pct > 0 and step >= 10:
            title = "AssetHub 急速上涨"
        elif pct < 0 and step >= 10:
            title = "AssetHub 急速下跌"

        done = _price_alerted.setdefault(c, set())
        key = ("%s@%+d" % (morph[0], morph[1])) if morph else ("%+d" % signed)
        if key in done:
            continue
        if now - _last_price_alert_ts[0] < 30:
            continue
        done.add(key)
        _last_price_alert_ts[0] = now
        if len(done) > 40:
            _price_alerted[c] = set(list(done)[-20:])
        direction = "涨" if pct > 0 else "跌"
        _osx_notify("持仓 %s %s%.1f%%｜触发 %s%% 档" % (c, direction, absp, step), title)
    return

# ---------- 新闻 ----------

# 民生/社会/国内A股/广告类关键词黑名单（过滤掉与美股持仓、全球财经无关的内容）
FILTER_OUT = [
    # 民生/灾害/文娱
    "洪水", "台风", "暴雨", "应急响应", "地质灾害", "泥石流", "山洪",
    "天气", "气温", "供暖", "停水", "停电", "燃气管道",
    "明星", "演员", "综艺", "演唱会", "电视剧", "票房", "娱乐圈",
    "甲醛", "食品安全", "保健品", "体检", "养生",
    "放假", "假期", "景区", "旅游旺季",
    "世界杯", "奥运会", "NBA", "CBA", "中超", "足球联赛", "男篮",
    # 国内社会/政治/地方
    "省委", "书记", "移民", "环球时报", "社评", "横琴", "离境退税",
    "加油机", "社评", "湖北发布", "广东", "广西",
    # A股 / 港股 / 国内公司（默认过滤；命中 TECH_WHITELIST 的科技新闻放行）
    "A股", "港股", "中概", "恒生", "宁德时代", "华虹", "中芯", "长存",
    "东财", "红利资产", "沪指", "沪深", "北向资金", "创业板", "科创板",
    "涨停", "晶圆代工", "上证", "深成指",
    # 金十广告 / 促销
    "解锁", "点击查看", "扑克", "FM-Radio", "早餐", "9折", "VIP", "福利",
    ">>", "9.9", "活动",
]

# 科技类关键词白名单：命中的新闻即使含 A股/国内信号词也放行（用户需要 A股科技新闻）
TECH_WHITELIST = [
    "AI", "人工智能", "大模型", "DeepSeek", "算力", "数据中心", "GPU", "芯片",
    "半导体", "晶圆", "光模块", "存储", "英伟达", "苹果", "华为", "中芯", "华虹",
    "长存", "机器人", "人形", "具身智能", "自动驾驶", "智能驾驶", "飞行汽车",
    "固态电池", "稀土", "鸿蒙", "昇腾", "鲲鹏", "国产替代", "先进制程",
    "折叠屏", "云计算", "元宇宙", "6G", "5.5G", "PCB", "服务器", "光纤",
]

# 硬过滤：命中即过滤，不受科技白名单放行（政治/民生/广告类，避免长正文误命中白名单词）
STRICT_FILTER = [
    "省委", "书记", "省长", "市长", "县委", "局长", "主任", "统战", "人大",
    "政协", "移民", "环球时报", "社评", "湖北发布", "横琴", "离境退税",
    "明星", "演员", "综艺", "演唱会", "电视剧", "票房", "娱乐圈",
    "洪水", "台风", "暴雨", "泥石流", "山洪", "天气", "气温", "供暖", "停水",
    "食品安全", "甲醛", "保健品", "体检", "养生", "放假", "假期", "景区",
    "解锁", "点击查看", "扑克", "FM-Radio", "VIP", "福利", "9折", "9.9", "活动",
]

# A股科技公司/概念 → 命中时新闻标「A股」标签
CN_TECH_KW = [
    "华为", "中芯", "华虹", "长存", "寒武纪", "海光", "北方华创", "中微",
    "新易盛", "中际旭创", "光迅", "天孚", "沪电", "精智达", "蓝思科技",
    "长飞光纤", "宇树", "振邦智能", "国产替代", "昇腾", "鲲鹏", "鸿蒙",
    "光谷", "中芯国际", "长鑫", "兆易创新", "韦尔", "圣邦", "澜起",
    "中科曙光", "浪潮信息",
]

JIN10_HEADERS = {"x-app-id": _load_config().get("jin10_app_id") or "YOUR_JIN10_APP_ID",
                 "x-version": "1.0.0"}

# 国内（A股/港股/中概/人民币等）新闻信号词 → 标签显示" A股"
CN_KEYWORDS = [
    "A股", "港股", "中概", "人民币", "离岸人民币", "中国央行", "央行行长",
    "上证", "深成", "沪深", "北向资金", "恒生", "中证", "富时中国", "MSCI中国",
    "证监会", "上交所", "深交所", "港交所", "创业板", "科创板", "沪深港通",
    "内盘", "南向", "北向", "中特估",
]

def _tag_news(title):
    """按标题匹配持仓关键词，返回 (代码列表, 关键词列表)"""
    codes, kws = [], []
    for kw, cs in KEYWORD_CODES.items():
        if kw in title:
            kws.append(kw)
            for c in cs:
                if c not in codes:
                    codes.append(c)
    return codes, kws

def get_market_news(kw=None, cat=None):
    """金十数据快讯（国际财经/地缘）+ 华尔街见闻深度财经，合并过滤排序。
    kw=按标题/标签/代码关键词过滤；cat=cn（A股）/ us（非 A股）分类"""
    def build():
        items = []

        # 金十数据快讯：时效性最强，含地缘冲突消息
        try:
            raw = http_get("https://flash-api.jin10.com/get_flash_list?max_time=0&channel=-8200",
                           headers=JIN10_HEADERS)
            for it in json.loads(raw).get("data", []):
                content = (it.get("data") or {}).get("content") or ""
                content = re.sub(r"金十数据\d+月\d+日[讯，,]", "", content).strip()
                if not content:
                    continue
                tstr = it.get("time") or ""
                ts = 0
                try:
                    ts = int(datetime.datetime.strptime(tstr, "%Y-%m-%d %H:%M:%S").timestamp())
                except ValueError:
                    pass
                items.append({"title": content, "url": "https://www.jin10.com/",
                              "time": tstr[5:16], "ts": ts, "source": "金十"})
        except Exception:
            pass

        # 华尔街见闻：深度财经文章（global 频道）
        try:
            raw = http_get("https://api-one.wallstcn.com/apiv1/content/information-flow"
                           "?channel=global-channel&limit=30")
            for it in json.loads(raw).get("data", {}).get("items", []):
                r = it.get("resource") or {}
                title = (r.get("title") or "").strip()
                if not title:
                    continue
                dt = r.get("display_time") or 0
                tstr = datetime.datetime.fromtimestamp(int(dt)).strftime("%m-%d %H:%M") if dt else ""
                items.append({"title": title, "url": r.get("uri") or "https://wallstreetcn.com/",
                              "time": tstr, "ts": int(dt or 0), "source": "见闻"})
        except Exception:
            pass

        # 华尔街见闻：AI/科技频道（补充 A股科技类新闻，如中芯/华虹/长存/华为）
        try:
            raw = http_get("https://api-one.wallstcn.com/apiv1/content/information-flow"
                           "?channel=ai&limit=20")
            for it in json.loads(raw).get("data", {}).get("items", []):
                r = it.get("resource") or {}
                title = (r.get("title") or "").strip()
                if not title:
                    continue
                dt = r.get("display_time") or 0
                tstr = datetime.datetime.fromtimestamp(int(dt)).strftime("%m-%d %H:%M") if dt else ""
                items.append({"title": title, "url": r.get("uri") or "https://wallstreetcn.com/",
                              "time": tstr, "ts": int(dt or 0), "source": "见闻·AI"})
        except Exception:
            pass

        # 清理 HTML 标签 → 过滤（科技白名单优先放行）→ 打持仓代码标签
        out, seen = [], set()
        for n in items:
            n["title"] = re.sub(r"<[^>]+>", "", n["title"])
            n["title"] = n["title"].replace("&gt;", ">").replace("&lt;", "<").strip()
            if not n["title"]:
                continue
            # 标题去重（跨来源，如见闻 global 与 ai 频道重复）
            key = n["title"][:40]
            if key in seen:
                continue
            # 硬过滤（政治/民生/广告）优先，不受科技白名单放行
            if any(k in n["title"] for k in STRICT_FILTER):
                continue
            # 命中科技白名单 → 放行（含 A股科技新闻）；否则按黑名单过滤
            is_tech = any(k in n["title"] for k in TECH_WHITELIST)
            if not is_tech and any(k in n["title"] for k in FILTER_OUT):
                continue
            seen.add(key)
            if len(n["title"]) > 90:
                n["title"] = n["title"][:90] + "…"
            codes, kws = _tag_news(n["title"])
            n["related"] = codes
            n["related_kw"] = kws
            # A股科技新闻（命中 A股科技公司/概念 或 国内信号词）→ 标 A股
            is_cn = any(k in n["title"] for k in CN_KEYWORDS) or any(k in n["title"] for k in CN_TECH_KW)
            n["category"] = "cn" if is_cn else "global"
            out.append(n)
        # 持仓相关优先，其余按时间倒序
        out.sort(key=lambda x: (-len(x["related"]), -x["ts"]))
        # 重要新闻 → macOS 系统通知（去重 + 频率限制）
        try:
            _push_important_news(out)
        except Exception:
            pass
        return out
    items = cache_get("market_news", 300, build)
    if cat == "cn":
        items = [n for n in items if n.get("category") == "cn"]
    elif cat == "us":
        items = [n for n in items if n.get("category") != "cn"]
    if kw:
        k = kw.strip()
        items = [n for n in items
                 if k in n.get("title", "") or k in (n.get("related_kw") or [])
                 or k in (n.get("related") or [])]
    return items

def get_stock_news(code):
    code = code.upper()
    kw = KEYWORDS.get(code, code)
    def build():
        param = json.dumps({
            "uid": "", "keyword": kw, "type": ["cmsArticleWebOld"], "client": "web",
            "clientVersion": "curr", "clientType": "web",
            "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                                            "pageIndex": 1, "pageSize": 20,
                                            "preTag": "", "postTag": ""}},
        }, ensure_ascii=False)
        from urllib.parse import quote
        url = "https://search-api-web.eastmoney.com/search/jsonp?cb=cb&param=" + quote(param)
        raw = http_get(url).decode("utf-8", "ignore").strip()
        if raw.startswith("cb("):
            raw = raw[3:-1]
        d = json.loads(raw)
        arts = d.get("result", {}).get("cmsArticleWebOld", []) or []
        items = []
        for a in arts:
            items.append({
                "title": (a.get("title") or "").replace("<em>", "").replace("</em>", ""),
                "url": a.get("url", ""),
                "media": a.get("mediaName", ""),
                "time": (a.get("date", "") or "")[:16],
            })
        return {"keyword": kw, "items": items}
    return cache_get(f"stocknews_{code}", 600, build)

# ---------- 日报 ----------

def get_reports():
    files = sorted(glob.glob(os.path.join(REPORTS_DIR, "asset_report_*.html")), reverse=True)
    return [os.path.basename(f)[13:-5] for f in files]

# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index.html"):
                with open(os.path.join(ROOT, "static", "index.html"), "rb") as fp:
                    self._html(fp.read())
            elif path.startswith("/static/"):
                # 静态文件（logo.png、favicon 等）
                rel = path[len("/static/"):]
                safe = os.path.normpath(rel).lstrip(os.sep)
                fp_path = os.path.join(ROOT, "static", safe)
                if not os.path.isfile(fp_path):
                    self._json({"error": "not found"}, 404)
                    return
                ext = os.path.splitext(fp_path)[1].lower()
                mime = {".png": "image/png", ".jpg": "image/jpeg",
                        ".svg": "image/svg+xml", ".ico": "image/x-icon"}.get(ext, "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(fp_path, "rb") as fp:
                    self.wfile.write(fp.read())
            elif path == "/api/portfolio":
                # ?force=1 强制绕过缓存手动取一次最新行情（右上角刷新按钮）
                if self.path.split("?", 1)[-1] == "force=1":
                    _cache.pop("portfolio", None)
                    self._json(get_portfolio(force=True))
                else:
                    self._json(get_portfolio())
            elif path == "/api/portfolio/meta":
                self._json(get_portfolio_meta())
            elif path == "/api/markets":
                self._json(get_markets())
            elif path == "/api/preference":
                self._json(get_preference())
            elif path == "/api/kline":
                self._json(get_stock_klines())
            elif path == "/api/news":
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                self._json(get_market_news(kw=(q.get("kw") or [""])[0] or None,
                                           cat=(q.get("cat") or [""])[0] or None))
            elif path == "/api/test_notify":
                # 调试：验证 server 进程内 osascript 通知通道是否可用
                import subprocess as _sp
                try:
                    msg = "测试通知（来自 AssetHub 后端）"
                    _sp.call(["osascript", "-e",
                              'display notification "%s" with title "AssetHub 重要新闻"' % msg.replace('"', '\\"')],
                             stdout=open(os.devnull, "w"), stderr=open(os.devnull, "w"), timeout=8)
                    self._json({"ok": True})
                except Exception as e:
                    self._json({"ok": False, "err": repr(e)})
            elif path == "/api/stocknews":
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                code = (q.get("code") or [""])[0]
                if not code:
                    self._json({"error": "missing code"}, 400)
                else:
                    self._json(get_stock_news(code))
            elif path == "/api/reports":
                self._json(get_reports())
            elif path == "/api/report":
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                date = (q.get("date") or [""])[0]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    self._json({"error": "bad date"}, 400)
                    return
                fp = os.path.join(REPORTS_DIR, f"asset_report_{date}.html")
                if os.path.exists(fp):
                    with open(fp, "rb") as f:
                        self._html(f.read())
                else:
                    self._json({"error": "not found"}, 404)
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/stock":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                code = norm_code(body.get("code"))
                shares = float(body.get("shares") or 0)
                cost = float(body.get("cost_per_share") or 0)
                if not code or shares <= 0 or cost <= 0:
                    self._json({"error": "参数无效"}, 400)
                    return
                # 代码合法性：仅允许字母数字（含 sh/sz/bj 前缀），拒绝误填的名称/中文
                if not re.match(r"^[A-Za-z0-9]{1,12}$", code):
                    self._json({"error": "代码格式无效（如 NVDA 或 sh600584）"}, 400)
                    return
                # 查股票名称（腾讯行情）
                name = code
                try:
                    q = fetch_quotes([code])
                    if code in q:
                        name = q[code]["name"]
                except Exception:
                    pass
                # 读写持仓模板（先备份）
                import shutil
                with open(TEMPLATE) as fp:
                    tpl = json.load(fp)
                shutil.copy(TEMPLATE, TEMPLATE + ".bak-" +
                            datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
                stocks = tpl.setdefault("stocks", [])
                merged = False
                for s in stocks:
                    if s.get("code") == code:
                        total = s["shares"] + shares
                        s["cost_per_share"] = round(
                            (s["shares"] * s["cost_per_share"] + shares * cost) / total, 4)
                        s["shares"] = total
                        merged = True
                        break
                if not merged:
                    stocks.append({"code": code, "name": name,
                                   "shares": shares, "cost_per_share": round(cost, 4)})
                with open(TEMPLATE, "w") as fp:
                    json.dump(tpl, fp, ensure_ascii=False, indent=2)
                with _lock:
                    _cache.pop("portfolio", None)
                self._json({"ok": True, "code": code, "name": name, "merged": merged})
            elif path == "/api/stock/update":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                code = norm_code(body.get("code"))
                shares = float(body.get("shares") or 0)
                cost = float(body.get("cost_per_share") or 0)
                if not code or shares <= 0 or cost <= 0:
                    self._json({"error": "参数无效"}, 400)
                    return
                import shutil
                with open(TEMPLATE) as fp:
                    tpl = json.load(fp)
                shutil.copy(TEMPLATE, TEMPLATE + ".bak-" +
                            datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
                stocks = tpl.get("stocks", [])
                name = None
                for s in stocks:
                    if s.get("code") == code:
                        name = s.get("name") or name
                new_stocks = [s for s in stocks if s.get("code") != code]
                if len(new_stocks) == len(stocks):
                    self._json({"error": "code not found"}, 404)
                    return
                new_stocks.append({"code": code, "name": name or code,
                                   "shares": shares, "cost_per_share": round(cost, 4)})
                tpl["stocks"] = new_stocks
                with open(TEMPLATE, "w") as fp:
                    json.dump(tpl, fp, ensure_ascii=False, indent=2)
                with _lock:
                    _cache.pop("portfolio", None)
                self._json({"ok": True, "code": code, "name": name})
            elif path == "/api/fund":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                code = str(body.get("code") or "").strip()
                name = str(body.get("name") or "").strip()   # 可选，空则用 code
                market = body.get("market") or "us"
                mv = float(body.get("market_value") or 0)
                shares = float(body.get("shares") or 0)
                cost = float(body.get("cost") or 0)          # 可选，缺省 0（盈亏暂不可算）
                if not code or mv <= 0:
                    self._json({"error": "参数无效"}, 400)
                    return
                import shutil
                with open(TEMPLATE) as fp:
                    tpl = json.load(fp)
                shutil.copy(TEMPLATE, TEMPLATE + ".bak-" +
                            datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
                key = "funds" if market == "us" else "funds_cny"
                funds = tpl.setdefault(key, [])
                merged = False
                for f in funds:
                    if f.get("code") == code:
                        f["market_value"] = round(mv, 2)
                        f["shares"] = shares
                        if name:
                            f["name"] = name
                        merged = True
                        break
                if not merged:
                    rec = {"code": code, "market_value": round(mv, 2), "shares": shares,
                           "cost": round(cost, 2)}
                    if name:
                        rec["name"] = name
                    if market != "us":
                        rec["cost_per_nav"] = 0
                        rec["yield_pct"] = 0
                    funds.append(rec)
                with open(TEMPLATE, "w") as fp:
                    json.dump(tpl, fp, ensure_ascii=False, indent=2)
                with _lock:
                    _cache.pop("portfolio", None)
                self._json({"ok": True, "code": code, "name": name or code, "merged": merged})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        try:
            if path == "/api/stock":
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                code = norm_code((q.get("code") or [""])[0])
                if not code:
                    self._json({"error": "missing code"}, 400)
                    return
                import shutil
                with open(TEMPLATE) as fp:
                    tpl = json.load(fp)
                shutil.copy(TEMPLATE, TEMPLATE + ".bak-" +
                            datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
                stocks = tpl.get("stocks", [])
                before = len(stocks)
                tpl["stocks"] = [s for s in stocks if s.get("code") != code]
                if len(tpl["stocks"]) == before:
                    self._json({"error": "code not found"}, 404)
                    return
                with open(TEMPLATE, "w") as fp:
                    json.dump(tpl, fp, ensure_ascii=False, indent=2)
                with _lock:
                    _cache.pop("portfolio", None)
                self._json({"ok": True, "code": code})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

HOST = os.environ.get("ASSETHUB_HOST", "0.0.0.0")

def lan_ip():
    """获取本机局域网 IP（供手机等设备访问）"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def run_server(port=None, daemon=True):
    port = port or PORT
    srv = ThreadingHTTPServer((HOST, port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=daemon)
    t.start()
    return srv, port

if __name__ == "__main__":
    srv, port = run_server(daemon=False)
    print(f"AssetHub 服务已启动: http://127.0.0.1:{port}", flush=True)
    print(f"局域网访问: http://{lan_ip()}:{port}", flush=True)
    while True:
        time.sleep(3600)
