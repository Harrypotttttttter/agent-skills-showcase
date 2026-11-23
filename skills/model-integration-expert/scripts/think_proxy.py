#!/usr/bin/env python3
"""本地模型接入代理：关闭思维链 + 输入长度保护 + 真流式转发。

解决接入 OpenAI 兼容本地模型的三个高频故障：

1. **强制思维链导致正文为空**
   有些模型（如 Qwen3.5）template 内置强制思考，OpenAI 兼容客户端不主动传
   reasoning_effort，导致 reasoning 吞掉 max_tokens、content 为空。
   → 转发时自动注入 `reasoning_effort: none`。

2. **超长 prompt 打爆上下文（发热/无反应的元凶）**
   Agent 类客户端（IDE/桌面助手）在长对话里会累积很大的历史，或触发自己的
   "上下文压缩"，把几万 token 的 prompt 直接砸给本地模型。后果是：
   prefill 耗时暴涨 → 客户端超时无响应；KV cache 撑到十几 GB → 内存吃穿 →
   swap 疯狂换页 → 机器卡顿发热。
   → 转发前按 token 预算裁剪历史：保留 system 与最近若干轮，丢弃最旧内容。
   裁剪会在响应里回带一条说明，客户端可感知（不静默丢内容）。

3. **流式响应被全量缓冲导致首字节超时**
   用 `resp.read()` 全量读上游响应时，客户端要等整段生成完才收到首字节，
   长文本可达 90+ 秒，直接触发客户端"请求超时"。
   → 流式请求改用 chunked 边读边发，首字节立即可达。

纯标准库，零第三方依赖。只监听 127.0.0.1，不对外暴露。

用法:
    python3 think_proxy.py --listen 11435 --upstream http://127.0.0.1:11434
    python3 think_proxy.py --listen 11435 --max-prompt-tokens 6000
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

# 默认输入预算（token）。要略小于上游模型的 context window，为输出留出余量。
# 8192 窗口 → 6000 输入 + 2048 输出 是稳妥组合。
MAX_PROMPT_TOKENS = 6000

# 粗略 token 估算：CJK 约 1 字 1 token，ASCII 约 4 字符 1 token。
# 估算只用于"是否超预算"的判断，宁可略微高估（早一点裁剪）也不要低估。
def estimate_tokens(text):
    if not isinstance(text, str):
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff":
            cjk += 1
        else:
            other += 1
    return cjk + other // 4


def content_text(content):
    """把 OpenAI 风格 content（字符串或多段数组）抽成纯文本，用于估算。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for seg in content:
            if isinstance(seg, dict):
                if isinstance(seg.get("text"), str):
                    parts.append(seg["text"])
                elif isinstance(seg.get("content"), str):
                    parts.append(seg["content"])
        return "\n".join(parts)
    return ""


def trim_messages(messages, budget):
    """按 token 预算裁剪历史，保留 system 与最近若干轮。

    返回 (裁剪后的 messages, 被丢弃的轮数)。永远保留最后一条 user 消息，
    保证"用户当前这句话"一定进模型。
    """
    if not isinstance(messages, list) or not messages:
        return messages, 0

    total = 0
    for m in messages:
        total += estimate_tokens(content_text(m.get("content"))) + 4  # 每条消息的角色开销

    if total <= budget:
        return messages, 0

    system_msgs = [m for m in messages if m.get("role") == "system"]
    conv = [m for m in messages if m.get("role") != "system"]

    # system 占的预算
    sys_cost = sum(estimate_tokens(content_text(m.get("content"))) + 4 for m in system_msgs)
    remaining = max(budget - sys_cost, 1000)

    # 从最新往回保留
    kept = []
    dropped = 0
    for m in reversed(conv):
        cost = estimate_tokens(content_text(m.get("content"))) + 4
        if kept and cost + sum(
            estimate_tokens(content_text(k.get("content"))) + 4 for k in kept
        ) > remaining:
            dropped += 1
            continue
        kept.append(m)
        if dropped and len(kept) >= 1:
            # 已开始丢弃说明超出预算，继续往前更早的都不留
            break
    kept.reverse()
    if not kept:
        kept = conv[-1:]
        dropped = len(conv) - 1

    # 明确统计真实被丢弃的条数
    dropped = len(conv) - len(kept)
    return system_msgs + kept, dropped


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[proxy] " + (fmt % args) + "\n")

    def _relay(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        path = self.path

        inject = False
        stream = False
        trimmed = 0
        in_tokens = 0

        if path.rstrip("/").endswith("/chat/completions") and raw:
            try:
                body = json.loads(raw)

                # 1) 关闭强制思维链
                if "reasoning_effort" not in body:
                    body.update(INJECT)
                    inject = True

                # 2) 输入长度保护：超预算就裁剪最旧的历史
                msgs = body.get("messages")
                if isinstance(msgs, list):
                    in_tokens = sum(
                        estimate_tokens(content_text(m.get("content"))) + 4 for m in msgs
                    )
                    if in_tokens > MAX_PROMPT_TOKENS:
                        new_msgs, trimmed = trim_messages(msgs, MAX_PROMPT_TOKENS)
                        if trimmed:
                            body["messages"] = new_msgs
                            body["_proxy_trimmed"] = trimmed

                raw = json.dumps(body).encode()
                stream = bool(body.get("stream"))
            except (ValueError, UnicodeDecodeError):
                pass

        url = UPSTREAM + path
        req = urllib.request.Request(url, data=raw if raw else None, method=self.command)
        for h in ("Content-Type", "Authorization"):
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        self.log_message(
            "%s %s inject=%s stream=%s in_tok=%d trimmed=%d",
            self.command, path, inject, stream, in_tokens, trimmed,
        )

        # 显式绕过任何代理环境变量直连上游（否则继承失效代理会挂起）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=600) as resp:
                ctype = resp.headers.get("Content-Type", "application/json")
                if stream:
                    # 真流式：chunked 边读边发，首字节立即可达
                    self.send_response(resp.status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while True:
                        chunk = resp.read(1024)
                        if not chunk:
                            break
                        self.wfile.write(("%X\r\n" % len(chunk)).encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                else:
                    data = resp.read()
                    # 裁剪信息回带给客户端（不静默丢内容）
                    if trimmed:
                        try:
                            j = json.loads(data.decode("utf-8", "replace"))
                            note = ("[本地模型代理] 输入超长，已裁剪最早 %d 条历史消息"
                                    "以适配本地上下文窗口。" % trimmed)
                            ch = (j.get("choices") or [{}])[0]
                            msg = ch.get("message")
                            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                                msg["content"] = note + "\n\n" + msg["content"]
                            data = json.dumps(j, ensure_ascii=False).encode("utf-8")
                        except Exception:
                            pass
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")
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


def main():
    global UPSTREAM, MAX_PROMPT_TOKENS
    ap = argparse.ArgumentParser(
        description="本地模型接入代理（关闭思维链 + 输入长度保护 + 真流式转发）")
    ap.add_argument("--listen", type=int, default=11435, help="监听端口（默认11435）")
    ap.add_argument("--upstream", default="http://127.0.0.1:11434", help="上游 Ollama 地址")
    ap.add_argument("--max-prompt-tokens", type=int, default=MAX_PROMPT_TOKENS,
                    help="输入 token 预算，超出则裁剪最旧历史（默认6000）")
    args = ap.parse_args()
    UPSTREAM = args.upstream.rstrip("/")
    MAX_PROMPT_TOKENS = max(1000, args.max_prompt_tokens)
    srv = ThreadingHTTPServer(("127.0.0.1", args.listen), Proxy)
    sys.stderr.write(
        "think_proxy 启动: 127.0.0.1:%d -> %s\n"
        "  注入 reasoning_effort=none | 输入预算 %d tokens（超出裁剪最旧历史）\n"
        % (args.listen, UPSTREAM, MAX_PROMPT_TOKENS))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nthink_proxy 停止\n")


if __name__ == "__main__":
    main()
