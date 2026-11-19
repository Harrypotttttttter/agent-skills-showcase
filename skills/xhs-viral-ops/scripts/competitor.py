#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""竞品特点拆解器：按 8 维框架对一条对标笔记结构化拆解 + 给对标笔记打分。

纯本地、零依赖、零联网。你不需付费 API——把你看竞品笔记时肉眼记录的
信息填进来即可（或手工录入），脚本负责结构化、打分、找差距。

8 维：封面 / 标题 / 选题 / 首图点击钩子 / 结构 / 可存性 / 互动设计 / 人设。
每维 0-5 分，总分 40。同时支持"我方 vs 对标"对比，输出差距维度。

用法：
  python3 competitor.py --demo
  python3 competitor.py note.json
  python3 competitor.py mine.json --vs rival.json
  python3 competitor.py note.json --json
  python3 competitor.py --help

退出码：0=完成；1=输入错误。
"""
import argparse
import json
import sys

DIMS = [
    ("cover", "封面", "版式是否高点击、主标字数、配色对比"),
    ("title_formula", "标题", "公式类型、关键词位置、钩子"),
    ("topic", "选题", "需求类型(痛点/好奇/利益/身份)、竞争度"),
    ("hook", "首图钩子", "前三秒/前三行是否给结论或悬念"),
    ("structure", "结构", "清单/教程/测评/避坑/故事，共几点"),
    ("saveable", "可存性", "是否值得收藏：清单/表格/步骤"),
    ("interaction", "互动设计", "结尾是否留口子、评论引导"),
    ("persona", "人设", "主页三件套、关注理由是否清晰"),
]


def score(note):
    scores = {}
    for key, name, hint in DIMS:
        v = note.get(key, 0)
        if isinstance(v, dict):
            v = v.get("score", 0)
        try:
            v = int(float(v))
        except (ValueError, TypeError):
            v = 0
        scores[key] = min(5, max(0, v))
    total = sum(scores.values())
    return scores, total


def analyze(note):
    scores, total = score(note)
    dims = []
    for key, name, hint in DIMS:
        dims.append({"key": key, "name": name, "score": scores[key], "hint": hint,
                     "note": note.get(key + "_note", "")})
    weakest = sorted(dims, key=lambda d: d["score"])[:3]
    level = "爆款骨架" if total >= 32 else ("有潜力" if total >= 24 else "骨架偏弱")
    return {
        "title": note.get("title", "(未命名)"),
        "total": total,
        "level": level,
        "dims": dims,
        "weakest": [d["name"] for d in weakest],
    }


def print_human(res, vs=None):
    print("=" * 60)
    print(f"竞品拆解：{res['title']}  |  总分 {res['total']}/40  {res['level']}")
    print("=" * 60)
    for d in res["dims"]:
        bar = "█" * d["score"] + "░" * (5 - d["score"])
        print(f"  {d['name']:<6s} {bar} {d['score']}/5  {d['note']}")
    print(f"  最弱三环：{'、'.join(res['weakest'])}")
    if vs:
        print("\n--- 对标差距（我方 - 竞品）---")
        a = {d["key"]: d["score"] for d in res["dims"]}
        b = {d["key"]: d["score"] for d in vs["dims"]}
        for key, name, _ in DIMS:
            diff = a[key] - b[key]
            mark = "落后" if diff < 0 else ("领先" if diff > 0 else "持平")
            if diff != 0:
                print(f"  {name:<6s} {mark} {abs(diff)} 分  ({a[key]} vs {b[key]})")


def main(argv=None):
    ap = argparse.ArgumentParser(description="竞品特点拆解器：8维框架 + 打分 + 对标差距")
    ap.add_argument("file", nargs="?", help="笔记 JSON 文件")
    ap.add_argument("--vs", help="另一份笔记 JSON，用于对比差距")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--demo", action="store_true", help="用内置样例演示")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    demo_note = {
        "title": "通勤包我只留这5件，其他全退了",
        "cover": {"score": 5}, "cover_note": "米白底+6大字+5包叠放",
        "title_formula": {"score": 5}, "title_formula_note": "清单+排除，关键词靠前",
        "topic": {"score": 4}, "topic_note": "上班族每天背，需求强、竞争中等",
        "hook": {"score": 4}, "hook_note": "首图直接给5件清单",
        "structure": {"score": 5}, "structure_note": "5个包×价格/为什么留/缺点",
        "saveable": {"score": 5}, "saveable_note": "清单型，天然可存",
        "interaction": {"score": 3}, "interaction_note": "结尾无提问",
        "persona": {"score": 3}, "persona_note": "主页未突出通勤场景",
    }
    demo_rival = {
        "title": "打工人通勤包分享",
        "cover": {"score": 3}, "title_formula": {"score": 2}, "title_formula_note": "泛标题",
        "topic": {"score": 3}, "hook": {"score": 2},
        "structure": {"score": 3}, "saveable": {"score": 2},
        "interaction": {"score": 2}, "persona": {"score": 2},
    }

    try:
        if args.demo:
            note = demo_note
            rival = demo_rival
        elif args.file:
            note = json.load(open(args.file, encoding="utf-8"))
            rival = json.load(open(args.vs, encoding="utf-8")) if args.vs else None
        else:
            print("请提供 JSON 文件、--vs 或 --demo", file=sys.stderr)
            return 1
    except Exception as e:
        print(f"读取失败：{e}", file=sys.stderr)
        return 1

    res = analyze(note)
    vsres = analyze(rival) if rival else None
    if args.json:
        print(json.dumps({"analysis": res, "vs": vsres}, ensure_ascii=False, indent=2))
    else:
        print_human(res, vsres)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
