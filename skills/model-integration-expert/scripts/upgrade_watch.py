#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型升级追踪器：检测你关注的开源模型是否有新版本/新提交，供定期升级轮询使用。

设计：本地维护一个"上次记录快照"(JSON)。每次运行对比当前公开仓库的
lastModified，若变化则报告"有新迭代，建议评估升级"。只读、免费、无需 Key。

这个脚本就是「定期升级本地模型」轮询任务的数据源——轮询任务定期调用它，
把"哪些模型更新了"作为升级建议输入，再交给人判断是否重拉权重。

用法：
  python3 upgrade_watch.py init --model Qwen/Qwen2.5-7B-Instruct --state /tmp/watch.json
  python3 upgrade_watch.py check --state /tmp/watch.json --json
  python3 upgrade_watch.py list --state /tmp/watch.json
  python3 upgrade_watch.py --help

退出码：0=完成；1=参数/网络错误。
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HF_API = "https://huggingface.co/api/models"


def fetch_last_modified(model_id, timeout=15):
    url = HF_API + "/" + urllib.parse.quote(model_id, safe="/")
    req = urllib.request.Request(url, headers={"User-Agent": "upgrade-watch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", errors="replace"))
    return d.get("lastModified"), (d.get("cardData") or {}).get("license")


def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        return {"models": {}}


def save(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main(argv=None):
    ap = argparse.ArgumentParser(description="模型升级追踪器：检测开源模型是否迭代（定期轮询数据源）")
    sub = ap.add_subparsers(dest="cmd")
    pi = sub.add_parser("init", help="登记要追踪的模型")
    pi.add_argument("--model", required=True); pi.add_argument("--state", required=True)
    pc = sub.add_parser("check", help="检查是否有新版本")
    pc.add_argument("--state", required=True); pc.add_argument("--json", action="store_true")
    pl = sub.add_parser("list", help="列出追踪清单")
    pl.add_argument("--state", required=True)
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not args.cmd:
        ap.print_help(); return 0

    state = load(args.state)
    if args.cmd == "init":
        try:
            lm, lic = fetch_last_modified(args.model)
        except Exception as e:
            print(f"无法访问模型仓库：{e}", file=sys.stderr); return 1
        state["models"][args.model] = {"last_modified": lm, "license": lic,
                                       "recorded_at": datetime.now(timezone.utc).isoformat()}
        save(args.state, state)
        print(f"已登记追踪：{args.model}（基线更新时间 {lm}）")
        return 0

    if args.cmd == "list":
        for mid, info in state["models"].items():
            print(f"  {mid:<45s} 基线 {info.get('last_modified')}")
        if not state["models"]:
            print("  追踪清单为空")
        return 0

    # check
    updates, errors = [], []
    for mid, info in state["models"].items():
        try:
            lm, lic = fetch_last_modified(mid)
        except Exception as e:
            errors.append({"model": mid, "error": str(e)})
            continue
        old = info.get("last_modified")
        if lm and lm != old:
            updates.append({"model": mid, "old": old, "new": lm,
                            "suggestion": "创作者已迭代，建议评估后重拉权重；先备份当前模型"})
            info["last_modified"] = lm
    if updates:
        save(args.state, state)
    result = {"checked_at": datetime.now(timezone.utc).isoformat(),
              "updates": updates, "errors": errors,
              "note": "本工具只报告更新，不自动升级；是否重拉权重由人决定"}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 56)
        print(f"升级检查：{len(state['models'])} 个模型，{len(updates)} 个有新版本")
        for u in updates:
            print(f"  ↑ {u['model']}")
            print(f"      {u['old']} → {u['new']}")
            print(f"      {u['suggestion']}")
        if not updates:
            print("  暂无新版本")
        for e in errors:
            print(f"  ! {e['model']}: {e['error']}")
    # 契约：全部检查失败视为本次检查未完成，返回 1；部分成功返回 0 并在结果里保留 errors
    return 1 if (errors and not updates) else 0


if __name__ == "__main__":
    raise SystemExit(main())
