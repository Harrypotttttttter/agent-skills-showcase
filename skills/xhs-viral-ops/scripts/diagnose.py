#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小红书数据诊断器：输入你自己的笔记数据，定位增长瓶颈。

纯本地计算，零依赖、零联网、零付费 API。数据只在你自己机器上处理。

支持两种输入：
  1) CSV（每行一篇笔记，列名见 --help 或 reference）
  2) 单篇交互参数（--impressions 等）

核心逻辑：把「指标达标线 + 诊断树」代码化。先按曝光量分层，再定位
掉的是哪一环（点击/互动/收藏/涨粉/完读），给出原因和建议动作。
所有阈值是行业公开复盘的经验量级，不是官方数据、不是及格线——只用于
「看趋势、跟自己同赛道比」，这一点会在输出里明确声明。

用法：
  python3 diagnose.py data.csv
  python3 diagnose.py data.csv --json
  python3 diagnose.py --impressions 2000 --reads 120 --likes 8 --collects 3 --comments 1 --fans 0
  python3 diagnose.py --help

退出码：0=分析完成；1=输入错误/字段缺失。
"""
import argparse
import csv
import json
import sys

# 经验达标线（图文口径，来源：行业公开复盘汇总量级，非官方、无对照组）
BANDS = {
    "ctr": {"low": 8.0, "high": 15.0},        # 阅读/曝光 %
    "engage": {"low": 5.0, "high": 10.0},     # (赞+藏+评)/阅读 %
    "collect": {"good": 3.0},                 # 收藏/阅读 %
    "follow": {"good": 1.0},                  # 涨粉/阅读 %
    "readthrough": {"good": 30.0},            # 完读/阅读 %
}

# 曝光分层：不同量级看不同的问题
EXPOSURE_TIERS = [
    (0, 500, "冷启动层", "曝光没出去——首图点击率或账号权重问题"),
    (500, 3000, "初级流量池", "互动率决定能不能进下一层池"),
    (3000, 10000, "中级流量池", "收藏率与完读率决定能走多远"),
    (10000, 10**12, "大流量池", "涨粉率与人设决定是否沉淀"),
]


def pct(a, b):
    return (a / b * 100.0) if b else 0.0


def tier_of(impr):
    for lo, hi, name, desc in EXPOSURE_TIERS:
        if lo <= impr < hi:
            return name, desc
    return "未知", ""


def diagnose_one(rec):
    """对单篇笔记做诊断，返回结构化结果。"""
    impr = rec.get("impressions") or 0
    reads = rec.get("reads") or 0
    likes = rec.get("likes") or 0
    collects = rec.get("collects") or 0
    comments = rec.get("comments") or 0
    shares = rec.get("shares") or 0
    fans = rec.get("fans") or 0
    readthrough = rec.get("readthrough") or 0  # 滑到底人数；没有则按 0 处理并提示

    m = {
        "ctr": pct(reads, impr),
        "engage": pct(likes + collects + comments + shares, reads),
        "collect": pct(collects, reads),
        "follow": pct(fans, reads),
        "readthrough": pct(readthrough, reads) if readthrough else None,
    }
    tier, tier_desc = tier_of(impr)

    findings = []
    # 数据一致性：矛盾数据不能当健康处理
    if impr > 0 and reads == 0:
        findings.append(("ERROR", "data-inconsistent",
            f"曝光 {impr:.0f} 但阅读为 0，数据矛盾",
            "核对后台字段：阅读是否被误填为 0；数据不对时诊断无意义"))
    elif reads > impr > 0:
        findings.append(("ERROR", "data-inconsistent",
            f"阅读 {reads:.0f} 大于曝光 {impr:.0f}，数据矛盾",
            "核对字段是否填反（曝光应≥阅读）"))

    # 第一优先：曝光量——决定看哪一层的问题
    if impr < 500:
        findings.append(("ERROR", "exposure-low",
            f"曝光仅 {impr}，未出冷启动池",
            "先查首图点击率与账号权重：换封面版式+标题公式；查站内信有无违规；别原样重发"))

    # 点击率
    if reads and impr:
        if m["ctr"] < BANDS["ctr"]["low"]:
            findings.append(("WARN", "ctr-low",
                f"点击率 {m['ctr']:.1f}% < {BANDS['ctr']['low']}%",
                "封面/标题没吸引力：换高点击版式、标题套清单/痛点/好奇公式"))
        elif m["ctr"] > BANDS["ctr"]["high"]:
            findings.append(("INFO", "ctr-high",
                f"点击率 {m['ctr']:.1f}% 优秀",
                "封面标题有效，沉淀这套版式到选题库"))

    # 互动率
    if reads:
        if m["engage"] < BANDS["engage"]["low"]:
            if m["collect"] < BANDS["collect"]["good"]:
                findings.append(("WARN", "collect-thin",
                    f"收藏率 {m['collect']:.1f}% < {BANDS['collect']['good']}%",
                    "缺『可存』内容：加清单/表格/步骤，让内容有保存价值"))
            if pct(comments, reads) < 0.5:
                findings.append(("WARN", "comment-thin",
                    f"评论率 {pct(comments, reads):.2f}% 偏低",
                    "结尾没留互动口子：加开放式提问，自己先补一条评论引导"))
            if m["readthrough"] is not None and m["readthrough"] < BANDS["readthrough"]["good"]:
                findings.append(("WARN", "readthrough-low",
                    f"完读率 {m['readthrough']:.1f}% < {BANDS['readthrough']['good']}%",
                    "前3行没给结论：重写开头，把结论/利益点前置"))
            elif m["readthrough"] is None:
                findings.append(("INFO", "readthrough-unknown",
                    "未提供完读人数，无法判断正文结构",
                    "若后台有『滑到底』数据请补 readthrough 字段"))

        # 涨粉率
        if impr >= 3000 and m["follow"] < BANDS["follow"]["good"]:
            findings.append(("WARN", "follow-low",
                f"涨粉率 {m['follow']:.2f}% < {BANDS['follow']['good']}%",
                "内容有价值但人设没立住：主页三件套(简介/背景/置顶)补齐，正文给出关注理由"))

    # 点赞权重提醒
    if likes > collects * 3 and likes > 50:
        findings.append(("INFO", "like-vs-collect",
            "点赞远高于收藏，点赞权重最低",
            "小红书收藏才是硬指标：把内容做成值得存的工具型笔记"))

    if not findings:
        findings.append(("INFO", "healthy", "各项指标在经验区间内", "保持节奏，做单变量测试继续优化"))

    return {
        "title": rec.get("title", "(未命名)"),
        "exposure_tier": tier,
        "tier_desc": tier_desc,
        "metrics": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()},
        "findings": findings,
    }


def load_csv(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rec = {}
            for k, v in r.items():
                if v is None or v == "":
                    rec[k.strip()] = 0
                    continue
                try:
                    rec[k.strip()] = float(v)
                except ValueError:
                    rec[k.strip()] = v.strip()
            rows.append(rec)
    return rows


def print_human(results):
    print("=" * 60)
    print("小红书数据诊断（经验阈值，看趋势用，不是官方及格线）")
    print("=" * 60)
    for r in results:
        print(f"\n【{r['title']}】 曝光层级：{r['exposure_tier']} — {r['tier_desc']}")
        m = r["metrics"]
        print(f"  点击率 {m['ctr']}% | 互动率 {m['engage']}% | 收藏率 {m['collect']}%"
              f" | 涨粉率 {m['follow']}% | 完读率 {m['readthrough'] if m['readthrough'] is not None else 'N/A'}%")
        for lvl, code, msg, act in r["findings"]:
            print(f"  [{lvl}] {msg}")
            print(f"      → {act}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="小红书数据诊断器：定位增长瓶颈（本地、零依赖、零付费）")
    ap.add_argument("csv", nargs="?", help="CSV 文件；省略则用单篇参数")
    ap.add_argument("--impressions", type=float, default=0, help="曝光量")
    ap.add_argument("--reads", type=float, default=0, help="阅读量")
    ap.add_argument("--likes", type=float, default=0, help="点赞")
    ap.add_argument("--collects", type=float, default=0, help="收藏")
    ap.add_argument("--comments", type=float, default=0, help="评论")
    ap.add_argument("--shares", type=float, default=0, help="分享")
    ap.add_argument("--fans", type=float, default=0, help="新增关注")
    ap.add_argument("--readthrough", type=float, default=0, help="滑到底人数（可省）")
    ap.add_argument("--title", default="单篇笔记", help="笔记标题")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if args.csv:
        try:
            rows = load_csv(args.csv)
        except Exception as e:
            print(f"读取 CSV 失败：{e}", file=sys.stderr)
            return 1
        if not rows:
            print("CSV 无数据行", file=sys.stderr)
            return 1
        results = [diagnose_one(r) for r in rows]
    else:
        if not args.impressions and not args.reads:
            print("请提供 CSV 或至少 --impressions/--reads", file=sys.stderr)
            return 1
        results = [diagnose_one({
            "title": args.title, "impressions": args.impressions, "reads": args.reads,
            "likes": args.likes, "collects": args.collects, "comments": args.comments,
            "shares": args.shares, "fans": args.fans, "readthrough": args.readthrough,
        })]

    if args.json:
        print(json.dumps({
            "disclaimer": "达标线为行业公开复盘经验量级，非官方数据、非及格线；看趋势、同赛道同量级对比用。",
            "results": results,
        }, ensure_ascii=False, indent=2))
    else:
        print_human(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
