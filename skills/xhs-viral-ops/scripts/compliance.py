#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内容合规机检：发布前扫描标题/正文/话题，拦截违禁词 + 提示搬运风险。

纯本地、零依赖、零联网。内置词表，不调用任何付费接口。

两类命中：
  HARD  = 必须替换（绝对化/功效宣称/诱导互动/站外导流/虚假种草等），命中即退出码1
  SOFT  = 需人工确认上下文（种草/平替/亲测等），仅提示

另含原创度检测：可传入"对标爆款原文"，检测是否有整句照抄——拆解爆款
的骨架可以用，原句照搬不能用（既违规又限流）。

用法：
  python3 compliance.py copy.txt
  python3 compliance.py copy.txt --reference viral.txt --json
  echo "文案" | python3 compliance.py -
  python3 compliance.py --demo
  python3 compliance.py --help

退出码：0=无HARD(可发)；1=命中HARD(须改)；2=用法/文件错误。
"""
import argparse
import json
import re
import sys

# HARD 违禁词 -> 建议替换（按类别，量级参考公开平台规则）
HARD = {
    # 绝对化用语（用明确短语，避免单字"最"误伤"最后/最近/最初"）
    "最好": ("绝对化用语", "较/相对/我个人觉得"),
    "最便宜": ("绝对化用语", "价格亲民"),
    "最强": ("绝对化用语", "表现不错"),
    "全网最": ("绝对化用语", "比较/相对"),
    "第一": ("绝对化用语", "靠前/之一"),
    "顶级": ("绝对化用语", "品质不错"),
    "国家级": ("绝对化用语", "符合标准"),
    "100%": ("绝对化用语", "大部分/通常"),
    "绝对": ("绝对化用语", "比较/大概率"),
    # 功效宣称
    "根治": ("医疗功效", "有帮助/可缓解"),
    "治愈": ("医疗功效", "有帮助"),
    "美白": ("功效宣称", "提亮观感"),
    "祛斑": ("功效宣称", "淡化观感"),
    "三天见效": ("功效宣称", "坚持使用观察"),
    "无效退款": ("承诺", "以实际体验为准"),
    # 诱导互动
    "点赞过万": ("诱导互动", "喜欢的话可以收藏"),
    "转发抽奖": ("诱导互动", "删去抽奖诱导"),
    "不转不是": ("诱导互动", "删去道德绑架"),
    "求赞": ("诱导互动", "删去直接索赞"),
    # 站外导流
    "加微信": ("站外导流", "站内交流"),
    "私聊我": ("站外导流", "评论区交流"),
    "v我": ("站外导流", "站内交流"),
    "扫码": ("站外导流", "删去扫码引导"),
    # 虚假种草/金融
    "已下单": ("虚假种草", "删去无依据购买表述"),
    "稳赚": ("金融承诺", "删去收益承诺"),
    "躺赚": ("金融承诺", "删去收益承诺"),
    "暴富": ("金融承诺", "删去夸大表述"),
}
SOFT = ["种草", "平替", "无广", "亲测", "怼", "绝了", "yyds", "宝藏", "无限回购"]


def scan_copy(text):
    # 跳过 fenced 代码块，避免把示例当违规
    cleaned = re.sub(r"```.*?```", "", text, flags=re.S)
    hard_hits, soft_hits = [], []
    for word, (cat, fix) in HARD.items():
        for m in re.finditer(re.escape(word), cleaned):
            hard_hits.append({"word": word, "category": cat, "fix": fix, "pos": m.start()})
    for word in SOFT:
        if word in cleaned:
            soft_hits.append({"word": word, "note": "需结合上下文人工确认"})
    hard_hits.sort(key=lambda x: x["pos"])
    return hard_hits, soft_hits


def check_originality(copy, reference):
    """检测与对标原文的整句重合（按句比对，长度>=10字视为整句）。"""
    def sents(t):
        return [s.strip() for s in re.split(r"[。！？!?\n]", t) if len(s.strip()) >= 10]
    ref = set(sents(reference))
    copied = [s for s in sents(copy) if s in ref]
    return copied


def print_human(hard, soft, copied):
    print("=" * 60)
    print("发布前合规机检")
    print("=" * 60)
    if not hard and not soft and not copied:
        print("  ✓ 无 HARD 命中、无搬运嫌疑，可发布")
        return
    if hard:
        print(f"\n[HARD] {len(hard)} 处，必须替换：")
        seen = set()
        for h in hard:
            k = (h["word"], h["category"])
            if k in seen:
                continue
            seen.add(k)
            print(f"  · 「{h['word']}」({h['category']}) → {h['fix']}")
    if soft:
        print(f"\n[SOFT] {len(soft)} 处，人工确认上下文：{', '.join(s['word'] for s in soft)}")
    if copied:
        print(f"\n[搬运风险] {len(copied)} 句与对标原文完全相同：")
        for s in copied[:5]:
            print(f"  · {s[:40]}...")
        print("  → 只借鉴骨架(选题/结构/公式)，表层(措辞/经历/图)必须换成自己的")


def main(argv=None):
    ap = argparse.ArgumentParser(description="内容合规机检：违禁词 + 搬运风险（本地、零依赖、零付费）")
    ap.add_argument("file", nargs="?", help="文案文件；- 或省略读 stdin")
    ap.add_argument("--reference", help="对标爆款原文，用于整句照抄检测")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--demo", action="store_true", help="内置样例演示")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    demo_copy = "这款面霜全网最便宜，三天见效，加微信私聊我，点赞过万抽奖！亲测好用。"
    demo_ref = "这款面霜全网最便宜，三天见效，加微信私聊我。亲测好用，真的绝了。"

    try:
        if args.demo:
            text, ref = demo_copy, demo_ref
        else:
            if args.file and args.file != "-":
                text = open(args.file, encoding="utf-8").read()
            else:
                text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
            ref = open(args.reference, encoding="utf-8").read() if args.reference else None
    except Exception as e:
        print(f"读取失败：{e}", file=sys.stderr)
        return 2

    if not text.strip():
        print("输入为空", file=sys.stderr)
        return 2

    hard, soft = scan_copy(text)
    copied = check_originality(text, ref) if ref else []
    if args.json:
        print(json.dumps({
            "hard_count": len(hard), "hard": hard,
            "soft_count": len(soft), "soft": soft,
            "copied_sentences": copied,
            "verdict": "BLOCK" if hard else ("REVIEW" if (soft or copied) else "PASS"),
        }, ensure_ascii=False, indent=2))
    else:
        print_human(hard, soft, copied)
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
