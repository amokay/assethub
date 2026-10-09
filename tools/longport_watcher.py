#!/usr/bin/env python3
"""LongPort 行情 watcher —— 常驻进程（AssetHub）。

网络架构（重要，改前先读）：
- openapi.longportapp.com 本机直连被墙，只能走 Clash(127.0.0.1:7897)。
- SDK 的 WS 层(reqwest WS)不走代理环境变量 → 由 tools/longport_relay.py
  在 127.0.0.1:8443 起 SNI 隧道中继，配合 /etc/hosts 两条记录把两个域名
  指到 127.0.0.1，SDK 全部流量(H http + WS)透明穿透 Clash。
- /etc/hosts（一次性，已配好）：
    127.0.0.1 openapi.longportapp.com
    127.0.0.1 openapi-quote.longportapp.com

职责：每 POLL_INTERVAL 秒拉一次持仓全量报价，夜盘时段取 overnight_quote
写入 AssetHub /api/night_quotes（10h 新鲜度，服务端夜盘时段优先采用）。
Clash 节点漂移时连接会失败 → 指数退避重试 + QuoteContext 自动重建。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO = Path("/Users/wangmingyu/WorkBuddy/2026-08-22-15-15-30/assethub")
sys.path.insert(0, str(REPO / "tools"))

POLL_INTERVAL = 30          # 正常轮询周期（秒）
RETRY_BACKOFF = 15          # 失败退避基数（秒），指数增长至上限
RETRY_MAX = 300
POST_URL = "http://127.0.0.1:8765/api/night_quotes"
FRESH_SEC = 15 * 60         # 夜盘价时间戳 15 分钟内才采用
ET = timezone(timedelta(hours=-4))   # EDT；11 月初切冬令时改 -5

SYMBOLS = ["BABA", "PDD", "TCOM", "LMT", "NVDA", "MU", "SKHY", "DRAM",
           "NASA", "STX", "NTAP", "AEP", "EOSE", "TSLA", "INTC"]


def log(msg):
    print(f"{datetime.now().strftime('%m-%d %H:%M:%S')} {msg}", flush=True)


def start_relay():
    """拉起 SNI 隧道中继（若 8443 已被占用说明已有实例在跑，跳过）。"""
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 8443))
        s.close()
        subprocess.Popen(
            [sys.executable, str(REPO / "tools" / "longport_relay.py")],
            stdout=open(REPO / "data" / "longport_relay.log", "ab"),
            stderr=subprocess.STDOUT,
        )
        log("relay spawned")
        time.sleep(1.5)
    except OSError:
        s.close()
        log("relay already running")
    except Exception as e:
        log(f"relay spawn fail: {e}")


def post_night(payload):
    req = urllib.request.Request(POST_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            log(f"POST ok: {r.read().decode()[:100]}")
            return True
    except Exception as e:
        log(f"POST fail: {e}")
        return False


def make_ctx():
    from longport.openapi import Config, QuoteContext
    cfg_d = json.load(open(REPO / "data" / "config.json"))["longport"]
    cfg = Config.from_apikey(
        cfg_d["LONGPORT_APP_KEY"], cfg_d["LONGPORT_APP_SECRET"],
        cfg_d["LONGPORT_ACCESS_TOKEN"],
        http_url="https://openapi.longportapp.com:8443",
        quote_ws_url="wss://openapi-quote.longportapp.com:8443/v2",
        enable_overnight=True,
    )
    return QuoteContext(cfg)


def poll_once(ctx):
    """拉一轮报价；有新鲜夜盘价则 POST，返回 True 表示正常。

    注意（实测确认）：
    - 字段名是 q.overnight_quote（不是 over_night_quote）。
    - SDK 返回的 timestamp 是【本机时区裸时间】（北京机器即北京时间）：
      NVDA 常规收盘 ts=04:00:00（=ET 16:00 收盘），overnight ts=15:43（=ET 03:43）。
      先按本地时区补 tzinfo，再换算 ET。
    """
    syms = [f"{c}.US" for c in SYMBOLS]
    quotes = ctx.quote(syms)
    out = []
    now = time.time()
    for q in quotes:
        on = getattr(q, "overnight_quote", None)
        if not on or not on.last_done:
            continue
        ts = on.timestamp
        if ts.tzinfo is None:
            ts = ts.astimezone()          # 裸时间按本机时区（北京 +8）补齐
        if now - ts.timestamp() > FRESH_SEC:
            continue
        code = q.symbol.split(".")[0]
        price = float(on.last_done)
        prev = float(q.prev_close or 0) or None   # 夜盘涨跌相对正规时段前收盘
        chg = round(price - prev, 4) if prev else 0.0
        pct = round(chg / prev * 100, 2) if prev else 0.0
        et = ts.astimezone(ET)
        out.append({
            "code": code, "price": round(price, 4),
            "chg": chg, "pct": pct,
            "ts": et.strftime("%b %d %I:%M%p EDT"),
            "bar_t": et.strftime("%H:%M"),
        })
    if out:
        log("night: " + ", ".join(f"{o['code']}={o['price']}" for o in out))
        post_night({"quotes": out})
    else:
        log("no fresh overnight quotes this round")
    return True


def main():
    start_relay()
    backoff = RETRY_BACKOFF
    ctx = None
    while True:
        try:
            if ctx is None:
                ctx = make_ctx()
                backoff = RETRY_BACKOFF
                log("QuoteContext connected")
            poll_once(ctx)
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            log(f"error: {str(e)[:160]} -> rebuild in {backoff}s")
            ctx = None            # 下一轮重建连接
            start_relay()         # relay 进程若已死则补种（端口占用时自动跳过）
            time.sleep(backoff)
            backoff = min(backoff * 2, RETRY_MAX)


if __name__ == "__main__":
    main()
