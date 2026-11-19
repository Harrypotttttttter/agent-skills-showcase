#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选题评分器：10 分制给选题打分，决定做不做、先做哪个。

把"定位三问 + 选题评分模型"代码化。五个维度各 0-2 分：
  需求强度 / 竞争度(反向) / 自身匹配 / 可延展 / 搜索价值
纯本地、零依赖、零联网。

用法：
  python3 topic_score.py --demo
  python3 topic_score.py --name "通勤包只留5件" --demand 2 --competition 1 --fit 2 --extend 1 --search 2
  python3 topic_score.py topics.csv --json
  python3 topic_score.py --help

退出码：0=完成；1=输入错误。
"""
import argparse
import csv
import json
import sys

DIMS = [
    ("demand", "需求强度", "是不是目标人群高频真实痛点/利益点"),
    ("competition", "竞争度(反向)", "分越高=竞争越小；红海给0，蓝海给2"),
    ("fit", "自身匹配", "与你的定位/人设/素材是否契合"),
    ("extend", "可延展", "能否做成系列/持续产出，而非一次性"),
    ("search", "搜索价值", "有无稳定搜索词、长尾词布局空间"),
]


def score_topic(t):
    vals = {}
    for key, name, hint in DIMS:
        v = t.get(key, 0)
        try:
            v = max(0, min(2, int(float(v))))
        except (ValueError, TypeError):
            v = 0
        vals[key] = v
    # 伪蓝海保护：竞争度=2（没人做）但需求=0，多半是没需求而非蓝海，竞争度降为0
    pseudo_blue = vals["competition"] == 2 and vals["demand"] == 0
    if pseudo_blue:
        vals["competition"] = 0
    total = sum(vals.values())
    if total >= 8:
        verdict, action = "优先做", "重点投入，准备封面+标题 A/B 两版"
    elif total >= 5:
        verdict, action = "可以做", "列入选题库，排期测试"
    else:
        verdict, action = "暂缓", "换个切角，或等有素材再做"
    return {
        "name": t.get("name", "(未命名选题)"),
        "scores": vals,
        "total": total,
        "verdict": verdict,
        "action": action,
        "pseudo_blue_note": "竞争度2但需求0：判定为伪蓝海，竞争度已降为0" if pseudo_blue else "",
    }


def print_human(res):
    print("=" * 56)
    print(f"选题：{res['name']}  |  {res['total']}/10  {res['verdict']}")
    print("=" * 56)
    for key, name, hint in DIMS:
        bar = "●" * res["scores"][key] + "○" * (2 - res["scores"][key])
        print(f"  {name:<10s} {bar} {res['scores'][key]}/2  {hint}")
    print(f"  → {res['action']}")
    if res.get("pseudo_blue_note"):
        print(f"  ! {res['pseudo_blue_note']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="选题评分器：10分制决定做不做")
    ap.add_argument("csv", nargs="?", help="CSV 文件（列：name,demand,competition,fit,extend,search）")
    ap.add_argument("--name", help="单条选题名")
    ap.add_argument("--demand", default=0); ap.add_argument("--competition", default=0)
    ap.add_argument("--fit", default=0); ap.add_argument("--extend", default=0)
    ap.add_argument("--search", default=0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        if args.demo:
            topics = [
                {"name": "通勤包只留5件", "demand": 2, "competition": 1, "fit": 2, "extend": 1, "search": 2},
                {"name": "我的日常vlog", "demand": 0, "competition": 0, "fit": 1, "extend": 1, "search": 0},
            ]
        elif args.csv:
            topics = []
            with open(args.csv, encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    topics.append({k.strip(): v for k, v in r.items()})
        elif args.name:
            topics = [{"name": args.name, "demand": args.demand, "competition": args.competition,
                       "fit": args.fit, "extend": args.extend, "search": args.search}]
        else:
            print("提供 CSV、--name 或 --demo", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"读取失败：{e}", file=sys.stderr)
        return 1

    results = [score_topic(t) for t in topics]
    results.sort(key=lambda x: -x["total"])
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print_human(r)
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
