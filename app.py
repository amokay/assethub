#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AssetHub 桌面应用入口（pywebview 原生窗口）"""
import os, sys, socket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402


def find_port(start=8765, end=8799):
    for p in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start


def main():
    port = find_port()
    server.run_server(port=port, daemon=True)

    import webview
    webview.create_window(
        "AssetHub",
        f"http://127.0.0.1:{port}",
        width=1280, height=880, min_size=(960, 640),
    )
    # Dock 图标：不设运行时图标，直接走 bundle AppIcon.icns 的系统渲染管线
    # （macOS 对 icns 自动套 squircle 圆角；运行时 setApplicationIconImage_ 是原样
    #  显示，会导致打开/关闭状态图标渲染不一致）
    webview.start()  # 阻塞，窗口关闭后进程退出


if __name__ == "__main__":
    main()
