#!/usr/bin/env python3
"""思维链关闭代理：为强制 thinking 的模型注入 reasoning_effort=none。

有些模型（如 Qwen3.5）template 内置强制思考，OpenAI 兼容客户端不会主动传
reasoning_effort，导致 reasoning 吞掉 max_tokens、正文为空。本代理在转发时
强制注入该参数，让任何 OpenAI 兼容客户端（含 WorkBuddy）都能拿到正常正文。

纯标准库，零依赖。只监听 127.0.0.1，不对外暴露。

用法:
    python3 think_proxy.py --listen 11435 --upstream http://127.0.0.1:11434
    python3 think_proxy.py --help
"""
import argparse
import json
import sys
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "http://127.0.0.1:11434"
INJECT = {"reasoning_effort": "none"}


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[proxy] " + (fmt % args) + "\n")

    def _relay(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        path = self.path

        # 只对 chat/completions 注入；且仅当调用方没显式指定时
        inject = False
        if path.rstrip("/").endswith("/chat/completions") and raw:
            try:
                body = json.loads(raw)
                if "reasoning_effort" not in body:
                    body.update(INJECT)
                    raw = json.dumps(body).encode()
                    inject = True
            except (ValueError, UnicodeDecodeError):
                pass

        url = UPSTREAM + path
        req = urllib.request.Request(url, data=raw if raw else None, method=self.command)
        # 透传关键请求头
        for h in ("Content-Type", "Authorization"):
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        self.log_message("%s %s inject=%s", self.command, path, inject)

        # 关键：显式绕过任何代理环境变量，直连上游（否则继承失效代理会挂起）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=600) as resp:
                data = resp.read()
                self.send_response(resp.status)
                ctype = resp.headers.get("Content-Type", "application/json")
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # 上游不可达
            msg = json.dumps({"error": {"message": "upstream error: %s" % e}},
                             ensure_ascii=False).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    do_POST = _relay
    do_GET = _relay

    def do_HEALTH(self):
        msg = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)


def main():
    ap = argparse.ArgumentParser(description="思维链关闭代理（注入 reasoning_effort=none）")
    ap.add_argument("--listen", type=int, default=11435, help="监听端口（默认11435）")
    ap.add_argument("--upstream", default="http://127.0.0.1:11434", help="上游 Ollama 地址")
    args = ap.parse_args()
    global UPSTREAM
    UPSTREAM = args.upstream.rstrip("/")
    srv = ThreadingHTTPServer(("127.0.0.1", args.listen), Proxy)
    sys.stderr.write("think_proxy 启动: 127.0.0.1:%d -> %s (注入 reasoning_effort=none)\n"
                     % (args.listen, UPSTREAM))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nthink_proxy 停止\n")


if __name__ == "__main__":
    main()
