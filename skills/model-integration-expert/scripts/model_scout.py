#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开源模型猎手：在 Hugging Face / GitHub 公开 API 上检索、评估候选模型。

能力：
  - 按任务/规模检索开源模型（Hugging Face Hub 公开 API，免费无需 Key）
  - 评估"孵化可行性"：许可证是否允许商用、体积、下载量、更新时间
  - 追踪某模型是否有新版本迭代（供定期升级轮询任务使用）

只用公开只读 API，无付费、无写入、无 Key。数据来自模型创作者公开仓库。

用法：
  python3 model_scout.py search "coder" --limit 5
  python3 model_scout.py search "Qwen2.5" --json
  python3 model_scout.py check Qwen/Qwen2.5-7B-Instruct --json
  python3 model_scout.py --help

退出码：0=完成；1=网络/参数错误。
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request

HF_API = "https://huggingface.co/api/models"


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "model-scout/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def search(q, limit):
    url = HF_API + "?" + urllib.parse.urlencode({
        "search": q, "sort": "downloads", "direction": -1, "limit": limit,
        "full": "false",
    })
    data = fetch(url)
    out = []
    for m in data:
        card = m.get("cardData") or {}
        lic = card.get("license")
        if isinstance(lic, list):
            lic = lic[0] if lic else None
        out.append({
            "id": m.get("id"),
            "downloads": m.get("downloads"),
            "likes": m.get("likes"),
            "last_modified": m.get("lastModified"),
            "license": lic,
            "tags": [t for t in (m.get("tags") or []) if t in
                     ("conversational", "text-generation", "image-text-to-text", "base_model", "finance", "code")],
            "commercial_ok": (lic in ("apache-2.0", "mit", "bsd-3-clause", "bsd-2-clause")),
        })
    return out


def check(model_id):
    url = HF_API + "/" + urllib.parse.quote(model_id, safe="/")
    m = fetch(url)
    card = m.get("cardData") or {}
    lic = card.get("license")
    if isinstance(lic, list):
        lic = lic[0] if lic else None
    sib = [s.get("rfilename") for s in (m.get("siblings") or [])]
    return {
        "id": m.get("id"),
        "license": lic,
        "commercial_ok": (lic in ("apache-2.0", "mit", "bsd-3-clause", "bsd-2-clause")),
        "last_modified": m.get("lastModified"),
        "downloads": m.get("downloads"),
        "likes": m.get("likes"),
        "has_gguf": any(str(x).endswith(".gguf") for x in sib),
        "has_safetensors": any(str(x).endswith(".safetensors") for x in sib),
        "tags": (m.get("tags") or [])[:15],
    }


def print_search(results):
    print(f"{'模型ID':<42s} {'下载量':>10s} {'许可':<12s} 商用")
    print("-" * 72)
    for r in results:
        print(f"{(r['id'] or '')[:41]:<42s} {str(r['downloads']):>10s} "
              f"{str(r['license'])[:11]:<12s} {'✓' if r['commercial_ok'] else '✗'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="开源模型猎手：Hugging Face 公开检索与孵化评估（免费无Key）")
    sub = ap.add_subparsers(dest="cmd")
    ps = sub.add_parser("search", help="按关键词检索模型")
    ps.add_argument("query"); ps.add_argument("--limit", type=int, default=8)
    ps.add_argument("--json", action="store_true")
    pc = sub.add_parser("check", help="检查单个模型的许可/格式/更新时间")
    pc.add_argument("model_id"); pc.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not args.cmd:
        ap.print_help()
        return 0
    try:
        if args.cmd == "search":
            res = search(args.query, args.limit)
            if args.json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            else:
                print_search(res)
        else:
            res = check(args.model_id)
            if args.json:
                print(json.dumps(res, ensure_ascii=False, indent=2))
            else:
                print("=" * 56)
                print(f"模型：{res['id']}")
                print(f"许可证：{res['license']}  商用：{'✓ 允许' if res['commercial_ok'] else '✗ 不允许'}")
                print(f"最后更新：{res['last_modified']}")
                print(f"下载量：{res['downloads']}  点赞：{res['likes']}")
                print(f"格式：GGUF={'有' if res['has_gguf'] else '无'}  Safetensors={'有' if res['has_safetensors'] else '无'}")
                print(f"标签：{', '.join(str(t) for t in res['tags'])}")
    except Exception as e:
        print(f"请求失败（检查网络）：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
