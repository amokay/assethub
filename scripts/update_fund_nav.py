#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美元基金净值自动更新：调 fetch_fund_nav.js 抓官网最新净值 → 更新模板。
自动更新 nav_hist（净值序列）+ market_value（市值=份额×净值，份额由市值/最新净值反推，份额不变前提）。
富达日本（LU0997587083）官网反爬暂无法自动抓取，保留手动（卡片"修改"按钮）。
用法: python3 update_fund_nav.py
"""
import json, os, re, shutil, subprocess, sys, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "data", "portfolio.json")
NODE = os.environ.get("NODE_BIN", "/Users/wangmingyu/.workbuddy/binaries/node/versions/22.22.2/bin/node")
NODE_PATH = os.environ.get("NODE_PATH", "/Users/wangmingyu/.workbuddy/binaries/node/workspace/node_modules")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "Chrome/120.0 Safari/537.36")

def http_get(url, timeout=18):
    """直连抓取（不走本地代理，官网反爬对直连更友好）"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return op.open(req, timeout=timeout).read().decode("utf-8", "ignore")

def dnb_fetch(isin, slug, name):
    """贝莱德等经 DNB（挪威基金平台，Morningstar 数据每日更新，服务端渲染可抓）
    页面含 'NAV/Price|xxx US dollars' 及日期。返回 {isin: {name, nav, date}}"""
    url = "https://m.dnb.no/en/saving/mutual-funds/fund-list/d/%s-%s" % (slug, isin)
    raw = http_get(url)
    txt = re.sub(r"<[^>]+>", "|", raw)
    txt = re.sub(r"\|+", "|", txt)
    m = re.search(r"NAV/Price\s*\|\s*([\d,.]+)\s*US dollars", txt)
    if not m:
        return {}
    nav = float(m.group(1).replace(",", ""))
    # 日期格式如 '04 Sep 2026' / '4 September 2026'
    dm = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{4})", txt)
    if not dm:
        return {}
    months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
              "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
    date = "%s-%02d-%02d" % (dm.group(3), months[dm.group(2)[:3]], int(dm.group(1)))
    return {isin: {"name": name, "nav": nav, "date": date}}

def fetch():
    env = dict(os.environ)
    env["NODE_PATH"] = NODE_PATH
    out = {}
    # 1) 富兰克林官网（puppeteer 无头）
    try:
        r = subprocess.run([NODE, os.path.join(ROOT, "scripts", "fetch_fund_nav.js")],
                           capture_output=True, text=True, timeout=240, env=env)
        if r.returncode == 0:
            out.update(json.loads(r.stdout))
    except Exception as e:
        print("  ! 富兰克林抓取异常:", str(e)[:80])
    # 2) 贝莱德经 DNB（curl 直连）
    try:
        out.update(dnb_fetch("LU0056508442",
                             "blackrock-global-funds-world-technology-fund-a2", "贝莱德世界科技"))
    except Exception as e:
        print("  ! 贝莱德(DNB) 抓取异常:", str(e)[:80])
    return out

def main():
    data = fetch()
    print("官网抓取结果:", json.dumps(data, ensure_ascii=False))
    tpl = json.load(open(TPL))
    changed = []
    for f in tpl.get("funds", []):
        code = f.get("code", "")
        info = data.get(code)
        if not info or not info.get("nav"):
            print(f"  - {code} 无自动源/抓取失败，跳过（可手动维护）")
            continue
        nav, date = info["nav"], info["date"]
        nh = f.setdefault("nav_hist", {})
        if date in nh:
            print(f"  = {code} {date} 净值 {nav} 已存在，跳过")
            continue
        # 份额 = 市值 / 最新已知净值（份额不变前提）
        base_dates = sorted(nh.keys())
        if base_dates:
            base_nav = nh[base_dates[-1]]
            shares = (f.get("market_value") or 0) / base_nav if base_nav else 0
        else:
            shares = f.get("shares") or 0
        if shares <= 0:
            print(f"  ! {code} 无法推算份额，仅记录净值")
            nh[date] = nav
            continue
        mv_new = round(shares * nav, 2)
        # 备份后更新
        if not changed:
            shutil.copy(TPL, TPL + ".bak-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
        nh[date] = nav
        f["nav"] = nav
        f["nav_date"] = date
        f["market_value"] = mv_new
        f["cost"] = f.get("cost") or round(shares * (f.get("cost_per_nav") or 0), 2)
        print(f"  ✓ {code} {date} 净值 {nav} → 市值 {mv_new}")
        changed.append(code)
    if changed:
        json.dump(tpl, open(TPL, "w"), ensure_ascii=False, indent=2)
        print(f"已更新模板并备份：{changed}")
    else:
        print("无新净值可更新")

if __name__ == "__main__":
    main()
