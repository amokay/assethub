#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AssetHub 数据健康自检：验证数据源、K 线、价格一致性、基金数据，防回归。
用法: python3 check_data.py    （有任一 ❌ 时退出码非 0）
"""
import os, sys, json, time, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import server

issues = []

def check(name, ok, detail=""):
    mark = "OK " if ok else "FAIL"
    print(f"  [{mark}] {name} {detail}")
    if not ok:
        issues.append(name)

NOW = datetime.datetime.now()
ET = NOW.astimezone(datetime.timezone(datetime.timedelta(hours=-4)))

print("=" * 60)
print(f"AssetHub 数据自检  {NOW:%Y-%m-%d %H:%M}（美东 {ET:%m-%d %H:%M}）")
print("=" * 60)

print("\n--- 1. 数据源可达性 ---")
hdrs = {"User-Agent": server.UA, "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
sources = [
    ("腾讯行情", "https://qt.gtimg.cn/q=usNVDA", None),
    ("新浪行情", "https://hq.sinajs.cn/list=gb_nvda",
     {"Referer": "https://finance.sina.com.cn"}),
    ("Nasdaq API", "https://api.nasdaq.com/api/quote/NVDA/chart?assetclass=stocks&type=intraday", hdrs),
    ("Binance", "https://data-api.binance.vision/api/v3/ticker/24hr?symbol=BTCUSDT", None),
    ("腾讯分钟(K线)", "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/minute/query?code=sh600584", None),
    ("天天基金", "http://fund.eastmoney.com/pingzhongdata/017436.js", None),
]
for name, url, h in sources:
    try:
        raw = server.http_get(url, headers=h, timeout=8)
        check(name, bool(raw and len(raw) > 10), f"({len(raw) if raw else 0}B)")
    except Exception as e:
        check(name, False, str(e)[:50])

print("\n--- 2. 美股 K 线完整性（周末/凌晨 bug 防回归） ---")
try:
    with server._lock:
        server._cache.pop("stock_klines", None)
    kl = server.get_stock_klines()
    us = [k for k in kl if k[:2] not in ("sh", "sz", "bj", "hk")]
    check("美股 K 线有数据", len(us) > 0, f"({len(us)} 只)")
    empty = [k for k in us if not (kl[k].get("pts") or [])]
    check("美股 K 线全部有点", not empty, f"空: {empty[:3]}" if empty else "")
    if us and kl.get(us[0], {}).get("pts"):
        # 盘前(4:00-9:30)数据稀疏，阈值放宽；盘中/盘后要求完整
        cur_et = ET.time()
        min_pts = 30 if datetime.time(4, 0) <= cur_et < datetime.time(9, 30) else 100
        check(f"每只点数合理(>{min_pts})", min(len(kl[k]["pts"]) for k in us) > min_pts,
              f"({min(len(kl[k]['pts']) for k in us)}~{max(len(kl[k]['pts']) for k in us)})")
except Exception as e:
    check("K 线构建", False, str(e)[:60])

print("\n--- 3. 价格一致性（腾讯 vs 新浪扩展时段，差 >5% 报警） ---")
try:
    us_codes = [s["code"] for s in json.load(open(server.TEMPLATE)).get("stocks", [])
                if s["code"][:2] not in ("sh", "sz", "bj", "hk")]
    tencent = server.fetch_quotes(us_codes[:8])
    sina = server._fetch_sina_after_hours(us_codes[:8])
    bad = []
    for c in tencent:
        if c in sina and tencent[c]["price"] > 0:
            diff = abs(tencent[c]["price"] - sina[c]["price"]) / tencent[c]["price"] * 100
            if diff > 5:
                bad.append(f"{c}: 腾讯{tencent[c]['price']} vs 新浪{sina[c]['price']} ({diff:.1f}%)")
    check("腾讯/新浪价格差 <5%", not bad, "; ".join(bad[:3]))
except Exception as e:
    check("价格交叉验证", False, str(e)[:60])

print("\n--- 4. 市场指标（黄金数字与 K 线一致性提示） ---")
try:
    with server._lock:
        server._cache.pop("markets", None)
    mkt = server.get_markets()
    for m in mkt.get("us", []):
        if m["code"] in ("GC", "CL", "BTC"):
            sp = m.get("spark") or []
            tail = sp[-1] if sp else None
            if tail:
                diff = abs(m["price"] - tail) / m["price"] * 100
                check(f"{m['label']} 数字/线差<3%", diff < 3,
                      f"(价 {m['price']} vs 线末 {round(tail,2)}, {diff:.2f}%)")
            else:
                check(f"{m['label']} 有走势线", False)
except Exception as e:
    check("市场指标", False, str(e)[:60])

print("\n--- 5. 基金数据（nav_hist/market_value 同步） ---")
try:
    tpl = json.load(open(server.TEMPLATE))
    for f in tpl.get("funds", []):
        nh = f.get("nav_hist") or {}
        mv = f.get("market_value", 0)
        check(f"{f['code'][-4:]} 净值序列", len(nh) >= 2,
              f"({len(nh)} 天, 最新 {sorted(nh.keys())[-1] if nh else '无'}, 总值 {mv})")
except Exception as e:
    check("基金数据", False, str(e)[:60])

print("\n" + "=" * 60)
if issues:
    print(f"自检发现问题 ({len(issues)}): {issues}")
    sys.exit(1)
else:
    print("全部正常 ✅")
    sys.exit(0)
