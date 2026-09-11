#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美元基金净值自动更新：官网 / DNB 优先，Morningstar 兜底 → 更新模板。
自动更新 nav_hist（净值序列）+ market_value（市值=份额×净值，份额由市值/最新净值反推，份额不变前提）。
富达日本（LU0997587083）官网被 Akamai 封锁、DNB 也无此标的，改由 Morningstar 公开
quote 端点兜底（见 morningstar_fetch）—— 三只美股基金因此都有自动源。
用法: python3 update_fund_nav.py
"""
import json, os, re, shutil, subprocess, sys, time, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "data", "portfolio.json")
NODE_PATH = os.environ.get("NODE_PATH",
                           os.path.expanduser("~/.workbuddy/binaries/node/workspace/node_modules"))

def _find_node():
    """定位 node 可执行文件。WorkBuddy 托管运行时升级后目录名会变（22.22.2 → 22.22.2-2 → -3 …），
    写死路径会静默失效，所以这里按 NODE_BIN → 常见候选 → 目录扫描最新版 → 系统 PATH 逐级回退。"""
    env = os.environ.get("NODE_BIN")
    if env and os.path.exists(env):
        return env
    root = os.path.expanduser("~/.workbuddy/binaries/node/versions")
    cands = [os.path.join(root, v, "bin", "node")
             for v in sorted(os.listdir(root), reverse=True)] if os.path.isdir(root) else []
    for c in cands:
        if os.path.exists(c):
            return c
    for c in ("/usr/local/bin/node", "/opt/homebrew/bin/node"):
        if os.path.exists(c):
            return c
    return "node"

NODE = _find_node()

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

def _prev_bd(ds):
    """上一个工作日。净值只在工作日公布，用来把 Morningstar 给的「前一日价」落到正确日期。
    不处理交易所节假日：万一撞上，day_pct 取 nav_hist 末两点仍然是正确的那两天。"""
    d = datetime.date.fromisoformat(ds)
    while True:
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5:
            return d.isoformat()


def morningstar_fetch(code, tries=5, gap=25):
    """Morningstar 公开 quote 端点：最新净值 / 净值日期 / 当日涨跌% / 前一日价。

    该端点有频率防护：密集请求会持续返回 HTTP 202 空响应，**静置几分钟即恢复**
    （实测首次请求即成功 → 连续打十几次后全 202 → 隔 8 分钟再打又立刻成功）。
    因此本脚本"每天一次"的频率是安全的，退避 25s 起步足以覆盖限流窗口。
    美股基金（尤其富达日本）靠它兜底；返回 None 表示重试耗尽仍拿不到。
    """
    url = "https://www.morningstar.com/api/v2/funds/%s/quote" % code
    for i in range(tries):
        if i:
            time.sleep(gap * i)                 # 退避 25s / 50s / 75s / 100s（约 4 分钟）
        try:
            j = json.loads(http_get(url, timeout=25))
            ov = (j.get("overview") or {}).get("payload", {}).get("data") or {}
            navd = ov.get("nav") or {}
            nav = navd.get("value")
            date = (((navd.get("properties") or {}).get("date") or {}).get("value") or "")[:10]
            pct = (ov.get("navReturn") or {}).get("value")
            sd = (j.get("structuredData") or {}).get("payload") or []
            prev = next((x.get("price") for x in sd
                         if isinstance(x, dict) and x.get("@type") == "PriceSpecification"), None)
            if nav and date:
                return {"nav": float(nav), "date": date,
                        "prev": float(prev) if prev else None,
                        "day_pct": float(pct) if pct is not None else None}
        except Exception:
            pass
    return None


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
        else:
            # 不再静默：暴露 node 侧真实失败原因（依赖缺失/官网反爬等）
            err = (r.stderr or r.stdout or "").strip().splitlines()
            print("  ! 富兰克林抓取异常: node 退出码 %s | %s" % (r.returncode, (err[0] if err else "无输出")[:120]))
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
            # 官网 / DNB 没有这只 → Morningstar 兜底（富达日本走这条）
            ms = morningstar_fetch(code)
            if ms:
                info = {"name": f.get("name", ""), "nav": ms["nav"], "date": ms["date"],
                        "prev": ms["prev"], "src": "morningstar"}
                print("  · %s 官网无源，改用 Morningstar：%s 净值 %s%s"
                      % (code, ms["date"], ms["nav"],
                         "（前一日 %s）" % ms["prev"] if ms.get("prev") else ""))
        if not info or not info.get("nav"):
            print(f"  ! {code} 官网与 Morningstar 都没取到净值（可能被限流），"
                  f"保持原值 {f.get('nav_date')} —— 需手动核对")
            continue
        nav, date = info["nav"], info["date"]
        nh = f.setdefault("nav_hist", {})
        if nh.get(date) == nav:
            print(f"  = {code} {date} 净值 {nav} 已存在，跳过")
            continue
        # 份额 = 市值 / 最新已知净值（份额不变前提）—— 必须在写入新净值之前取基准
        base_dates = sorted(nh.keys())
        base_nav = nh[base_dates[-1]] if base_dates else 0
        shares = ((f.get("market_value") or 0) / base_nav) if base_nav else (f.get("shares") or 0)
        # 断档补「前一日」：否则 day_pct 会拿很久以前的净值当昨天
        # （富达日本的 nav_hist 停在 08-28、最新净值 09-10，只补最新一条会算出两周累计跌幅）
        pdate, prev = None, info.get("prev")
        if prev and base_dates:
            cand = _prev_bd(date)
            if base_dates[-1] < cand < date:
                pdate = cand
        if shares <= 0:
            print(f"  ! {code} 无法推算份额，仅记录净值")
            if pdate:
                nh[pdate] = prev
            nh[date] = nav
            continue
        mv_new = round(shares * nav, 2)
        # 备份后更新
        if not changed:
            shutil.copy(TPL, TPL + ".bak-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
        if pdate:
            nh[pdate] = prev
            print(f"  + {code} 补前一日 {pdate} 净值 {prev}")
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
