#!/usr/bin/env python3
"""LongPort OpenAPI 本地 SNI 隧道中继。

背景：openapi.longportapp.com 及行情 WS 域名本机直连被墙，只有 Clash
混合代理可达；SDK 的 WS 层不支持代理。本进程监听 127.0.0.1:8443，
对每个进来的 TLS 连接解析 ClientHello 的 SNI，再经 Clash CONNECT
隧道到对应真实主机，之后纯字节转发（TLS 端到端，无需自签证书）。

hosts 配置（一次性）：
  127.0.0.1 openapi.longportapp.com
  127.0.0.1 openapi-quote.longportapp.com

SDK 侧：http_url=https://openapi.longportapp.com:8443
        quote_ws_url=wss://openapi-quote.longportapp.com:8443/v2
"""
import asyncio
import json
import sys
import time
import urllib.request

PROXY_HOST, PROXY_PORT = "127.0.0.1", 7897
LISTEN_PORT = 8443

ROUTES = {
    "openapi.longportapp.com": 443,
    "openapi-quote.longportapp.com": 443,
}

# 机场出口的 DNS 对长桥域名被污染（解析到 nigirocloud 等假站），
# 本地国内 DNS 也被污染（返回 Facebook/Dropbox 段）。因此这里用
# DoH（经 Clash 走境外解析）拿真实 IP，CONNECT 时直接用 IP 而非域名，
# SNI 仍由 ClientHello 保留——CDN 按 SNI 路由，证书校验正常。
DOH_URL = "https://cloudflare-dns.com/dns-query?name={host}&type=A"
DOH_REFRESH = 300          # 每 5 分钟刷新一次 IP 缓存
DNS_CACHE: dict = {}       # host -> {"ip": str, "ts": float}
# 兜底 IP（DoH 全挂时用；AWS ap-east-1，2026-10-09 实测）
STATIC_IPS = {
    "openapi.longportapp.com": "16.163.3.254",
    "openapi-quote.longportapp.com": "16.163.3.254",
}


def doh_resolve(host: str):
    """经 Clash 代理用 Cloudflare DoH 解析域名，返回 A 记录列表。"""
    url = DOH_URL.format(host=host)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": f"http://{PROXY_HOST}:{PROXY_PORT}",
                                     "https": f"http://{PROXY_HOST}:{PROXY_PORT}"}))
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    with opener.open(req, timeout=10) as r:
        data = json.loads(r.read().decode())
    return [a["data"] for a in data.get("Answer", []) if a.get("type") == 1]


async def resolve_ip(host: str) -> str:
    """取 host 的真实 IP：缓存(5min) → DoH → 静态兜底。"""
    ent = DNS_CACHE.get(host)
    if ent and time.time() - ent["ts"] < DOH_REFRESH:
        return ent["ip"]
    loop = asyncio.get_running_loop()
    try:
        ips = await loop.run_in_executor(None, doh_resolve, host)
        if ips:
            DNS_CACHE[host] = {"ip": ips[0], "ts": time.time()}
            print(f"[dns] {host} -> {ips[0]} (of {ips})", flush=True)
            return ips[0]
    except Exception as e:
        print(f"[dns] {host} DoH failed: {e}", flush=True)
    ip = STATIC_IPS.get(host)
    if ip:
        print(f"[dns] {host} -> fallback {ip}", flush=True)
        return ip
    return host   # 最后回退域名（让代理自己解析）


def parse_sni(buf: bytes):
    """从 TLS ClientHello 提取 SNI host，解析失败返回 None。"""
    try:
        if buf[0] != 0x16:
            return None
        rec_len = int.from_bytes(buf[3:5], "big")
        if 5 + rec_len > len(buf):
            return None
        p = 5
        if buf[p] != 0x01:
            return None
        hs_len = int.from_bytes(buf[p+1:p+4], "big")
        p += 4
        p += 2 + 32            # version + random
        sl = buf[p]; p += 1 + sl
        cl = int.from_bytes(buf[p:p+2], "big"); p += 2 + cl
        cm = buf[p]; p += 1 + cm
        ext_len = int.from_bytes(buf[p:p+2], "big"); p += 2
        end = p + ext_len
        while p + 4 <= end:
            et, el = int.from_bytes(buf[p:p+2], "big"), int.from_bytes(buf[p+2:p+4], "big")
            p += 4
            if et == 0x0000:   # server_name
                p += 2
                if buf[p] == 0x00:
                    nl = int.from_bytes(buf[p+1:p+3], "big")
                    return buf[p+3:p+3+nl].decode()
                return None
            p += el
    except (IndexError, ValueError):
        return None
    return None


async def splice(r: asyncio.StreamReader, w: asyncio.StreamWriter, tag: str, sni: str):
    n = 0
    try:
        while True:
            data = await r.read(65536)
            if not data:
                print(f"[relay] {sni} {tag}: EOF after {n}B", flush=True)
                break
            n += len(data)
            w.write(data)
            await w.drain()
    except (ConnectionError, OSError, asyncio.IncompleteReadError) as e:
        print(f"[relay] {sni} {tag}: {type(e).__name__} {e} after {n}B", flush=True)
    finally:
        try:
            w.close()
        except Exception:
            pass


async def tunnel(host, port):
    ip = await resolve_ip(host)
    pw_r, pw_w = await asyncio.open_connection(PROXY_HOST, PROXY_PORT)
    pw_w.write((f"CONNECT {ip}:{port} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n\r\n").encode())
    await pw_w.drain()
    status = await pw_r.readline()
    ok = status.startswith(b"HTTP/1.1 200") or status.startswith(b"HTTP/1.0 200")
    while True:
        line = await pw_r.readline()
        if line in (b"\r\n", b"\n", b""):
            break
    if not ok:
        pw_w.close()
        raise ConnectionError(f"proxy refused: {status!r}")
    return pw_r, pw_w


async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter):
    peer = cw.get_extra_info("peername")
    try:
        hello = await cr.read(16384)          # ClientHello（一个包足够）
        sni = parse_sni(hello)
        port = ROUTES.get(sni)
        print(f"[relay] {peer} SNI={sni} hello={len(hello)}B", flush=True)
        if not sni or port is None:
            cw.close()
            return
        pr, pw = await tunnel(sni, port)
        pw.write(hello)                       # 回放 ClientHello
        await pw.drain()
        await asyncio.gather(splice(cr, pw, 'c->r', sni), splice(pr, cw, 'r->c', sni))
    except (ConnectionError, OSError) as e:
        print(f"[relay] {peer} {e}", flush=True)
        try:
            cw.close()
        except Exception:
            pass


async def main():
    srv = await asyncio.start_server(handle, "127.0.0.1", LISTEN_PORT)
    print(f"[relay] listening 127.0.0.1:{LISTEN_PORT} -> {PROXY_HOST}:{PROXY_PORT}"
          f" (SNI: {', '.join(ROUTES)})", flush=True)
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
