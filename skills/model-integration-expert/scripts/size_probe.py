#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型体积探针：从 Ollama registry 查模型的【真实下载体积】，不靠估算。

这是选型准确性的关键——之前所有体积都是拍脑袋估计，导致"以为跑得动实际跑不动"。
本工具直接读 registry manifest 的 layer.size 求和，得到字节级精确体积。

同时按运行时预算判定能否跑：
  运行时占用 ≈ 模型权重 + KV cache 预留 + 推理开销
  Mac 统一内存可给 Ollama 的上限约 85%（留 15% 给系统）
  KV cache 预留按上下文档位给：8K→0.5GB, 32K→2GB, 128K→8GB（vibecoding 通常要 32K+）

用法：
  python3 size_probe.py devstral:24b qwen3.6:27b-coding qwen2.5-coder:14b
  python3 size_probe.py qwen3.8:27b --ram 24 --mac --ctx 32k
  python3 size_probe.py --list-contexts
  python3 size_probe.py devstral:24b --json
  python3 size_probe.py --help

退出码：0=全部可跑；1=存在跑不动的模型；2=参数/网络错误。
"""
import argparse
import json
import sys
import urllib.request
import urllib.error

REGISTRY = "https://registry.ollama.ai/v2/library/{model}/manifests/{tag}"

# KV cache 经验预留（GB），随上下文长度增长。vibecoding 建议 32k 起。
CTX_CACHE = {"8k": 0.6, "16k": 1.2, "32k": 2.2, "64k": 4.5, "128k": 8.0}


def parse_spec(spec):
    if ":" in spec:
        model, tag = spec.split(":", 1)
    else:
        model, tag = spec, "latest"
    return model, tag


def probe(model, tag, timeout=20):
    url = REGISTRY.format(model=model, tag=tag)
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.docker.distribution.manifest.v2+json",
        "User-Agent": "size-probe/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    layers = d.get("layers") or []
    if not layers:
        return None, "manifest 无 layers 字段"
    total = sum(int(l.get("size", 0)) for l in layers)
    return total, None


def judge(size_gb, ram, mac, ctx):
    """按运行时预算判定能否流畅跑。"""
    usable = ram * (0.85 if mac else 0.75)  # 可给 Ollama 的上限
    kv = CTX_CACHE[ctx]
    need = size_gb + kv + 0.8  # 权重 + KV + 推理开销余量
    headroom = usable - need
    if headroom < 0:
        verdict = "RUNNOT"      # 跑不动，别装
    elif headroom < 1.5:
        verdict = "TIGHT"       # 能跑但紧，长上下文会 swap
    elif headroom < 4:
        verdict = "OK"          # 舒服
    else:
        verdict = "COMFORT"     # 很宽裕
    return {"usable_gb": round(usable, 1), "kv_reserve_gb": kv,
            "runtime_need_gb": round(need, 1), "headroom_gb": round(headroom, 1),
            "verdict": verdict}


def main(argv=None):
    ap = argparse.ArgumentParser(description="查 Ollama 模型真实体积 + 运行时可行性判定")
    ap.add_argument("specs", nargs="*", help="模型 spec，如 devstral:24b")
    ap.add_argument("--ram", type=float, default=None, help="本机内存 GB；给则判定可行性")
    ap.add_argument("--mac", action="store_true", help="Mac 统一内存")
    ap.add_argument("--ctx", default="32k", choices=sorted(CTX_CACHE), help="上下文档位（默认32k，vibecoding建议）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list-contexts", action="store_true", help="列出上下文档位与 KV 预留")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if args.list_contexts:
        print("上下文档位 → KV cache 经验预留（GB）：")
        for k, v in CTX_CACHE.items():
            print(f"  {k:6s} {v}GB")
        print("vibecoding 建议 32k 起（读代码库需要长上下文）。")
        return 0

    if not args.specs:
        ap.print_help()
        return 2

    results, any_runnnot = [], False
    for spec in args.specs:
        model, tag = parse_spec(spec)
        size_bytes, err = probe(model, tag)
        if err:
            results.append({"spec": spec, "error": err})
            continue
        size_gb = round(size_bytes / 1024 / 1024 / 1024, 2)
        item = {"spec": spec, "model": model, "tag": tag,
                "download_size_gb": size_gb, "source": "registry manifest layer.size（实测）"}
        if args.ram is not None:
            j = judge(size_gb, args.ram, args.mac, args.ctx)
            item["runtime"] = j
            if j["verdict"] == "RUNNOT":
                any_runnnot = True
        results.append(item)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print("=" * 64)
        print("模型真实体积（registry 实测）" + (f" | {args.ram}GB {'Mac' if args.mac else ''} | ctx={args.ctx}" if args.ram else ""))
        print("=" * 64)
        for r in results:
            if "error" in r:
                print(f"  {r['spec']:28s} 查询失败: {r['error']}")
                continue
            line = f"  {r['spec']:28s} {r['download_size_gb']:6.2f}GB"
            if "runtime" in r:
                rt = r["runtime"]
                flag = {"RUNNOT": "跑不动", "TIGHT": "吃紧", "OK": "舒服", "COMFORT": "宽裕"}[rt["verdict"]]
                line += f" | 需 {rt['runtime_need_gb']}GB(KV {rt['kv_reserve_gb']}) | 余 {rt['headroom_gb']}GB → {flag}"
            print(line)
        if args.ram:
            print("\n  判定：跑不动=别装；吃紧=长上下文会swap；舒服/宽裕=可放心用")
    return 1 if any_runnnot else 0


if __name__ == "__main__":
    raise SystemExit(main())
