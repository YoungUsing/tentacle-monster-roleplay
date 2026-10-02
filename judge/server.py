#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Laya 本地开发服务器：静态文件 + 反向代理（零依赖）

用法:
    python serve.py                                  # 8080 端口，代理到 http://localhost:8000
    python serve.py -p 3000                          # 换端口
    python serve.py --laya http://127.0.0.1:8000     # 换后端地址
    python serve.py -d ./                            # 指定静态目录
    python serve.py --no-proxy                       # 只做静态服务（仍带 CORS 头）

浏览器访问 http://localhost:8080/ 打开调试台，
前端把 baseUrl 填成 /api 即可，请求 /api/v1/systemone
会被转发到 LAYA_URL/v1/systemone，不存在跨域问题。
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Handler(SimpleHTTPRequestHandler):
    """静态文件 + /api 反向代理"""

    laya_url = "http://localhost:8000"
    proxy_enabled = True
    timeout = 300  # 秒，模型首次加载可能较慢

    # ---------- 给所有响应追加 CORS 头 ----------
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Max-Age", "86400")
        super().end_headers()

    # ---------- 预检请求 ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- GET ----------
    def do_GET(self):
        if self.proxy_enabled and self.path.startswith("/api/"):
            self._proxy()
        else:
            super().do_GET()

    # ---------- POST ----------
    def do_POST(self):
        if self.proxy_enabled and self.path.startswith("/api/"):
            self._proxy()
        else:
            self.send_error(404, "Not Found")

    # ---------- 反向代理核心 ----------
    def _proxy(self):
        # /api/v1/systemone -> /v1/systemone
        target_path = self.path[len("/api"):] or "/"
        url = self.laya_url + target_path

        # 读取原始请求体
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None

        # 构造上游请求，只转发必要的头
        req = urllib.request.Request(url, data=body, method=self.command)
        for h in ("Content-Type", "Authorization", "Accept"):
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)

        # 发起请求
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                data = resp.read()
                ctype = resp.headers.get("Content-Type", "application/json")
                cenc = resp.headers.get("Content-Encoding")
        except urllib.error.HTTPError as e:
            status = e.code
            data = e.read()
            ctype = (e.headers.get("Content-Type") if e.headers else None) or "application/json"
            cenc = e.headers.get("Content-Encoding") if e.headers else None
        except urllib.error.URLError as e:
            status = 502
            data = json.dumps(
                {
                    "error": f"无法连接 Laya 服务 ({self.laya_url})",
                    "detail": str(e.reason),
                    "hint": "确认已运行 laya-serve，且 --laya 地址正确",
                },
                ensure_ascii=False,
            ).encode("utf-8")
            ctype = "application/json; charset=utf-8"
            cenc = None

        # 回写响应
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if cenc:
            self.send_header("Content-Encoding", cenc)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser(description="Laya 本地开发服务器")
    ap.add_argument("-p", "--port", type=int, default=8080, help="监听端口（默认 8080）")
    ap.add_argument("-b", "--bind", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("-d", "--dir", default=".", help="静态文件根目录（默认当前目录）")
    ap.add_argument("--laya", default="http://localhost:8000", help="Laya 后端地址")
    ap.add_argument("--no-proxy", action="store_true", help="关闭反向代理，只做静态服务")
    args = ap.parse_args()

    Handler.laya_url = args.laya.rstrip("/")
    Handler.proxy_enabled = not args.no_proxy

    directory = os.path.abspath(args.dir)
    if not os.path.isdir(directory):
        sys.exit(f"静态目录不存在: {directory}")

    # 把静态目录通过闭包传给 handler
    def make_handler(*a, **kw):
        return Handler(*a, directory=directory, **kw)

    httpd = Server((args.bind, args.port), make_handler)

    print("─" * 56)
    print(f"  静态目录  : {directory}")
    if Handler.proxy_enabled:
        print(f"  反向代理  : /api/*  ->  {Handler.laya_url}/*")
    else:
        print("  反向代理  : 已关闭")
    print(f"  访问地址  : http://{args.bind}:{args.port}/")
    print("─" * 56)
    print("  按 Ctrl+C 停止\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()