#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prose_rewrite — 行文改写器（把不可拍的行文改成可拍的）

改写原则：**语法安全优先于改动幅度**。
能被原地替换且不会破坏句法的才动手；破坏句法的一律降级为「建议」，
由作者决定。宁可少改，不可改坏。

自动处理:
  - 弱风格空词 → 移出正文、汇总为渲染约束行
  - 「主语 + 心理动词 + 情绪词」→ 身体信号
  - 光色高危词 → 反射/照明表述（句内自动去重）
  - 画面内具体文字 → <Picture N>
  - 独立负面段 → 正面约束（仅当该模型无负面字段）
  - 叠加副词 → 折叠
  - 对白容量换算与节奏压缩：按方言语速算朗读秒数，超单次窗口时只删填充词/叠字
    （不动实词）；压不下来就只报建议，不硬改

不处理（只报不改）: 隐喻、剧情、删改情节——需要作者判断。

用法:
    python prose_rewrite.py prompt.txt
    python prose_rewrite.py prompt.txt --out rewritten.txt
    python prose_rewrite.py prompt.txt --diff
    python prose_rewrite.py prompt.txt --json
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prose_rules as R  # noqa: E402


# ---------------------------------------------------------------- 约束区

CONSTRAINT_MARKERS = (
    "渲染约束", "rendering constraint", "constraints:",
)


def split_constraints(text: str) -> tuple[str, str]:
    """把文末的约束区切出来，返回 (正文, 约束区)。"""
    lines = text.splitlines(keepends=True)
    for i in range(len(lines) - 1, -1, -1):
        low = lines[i].lower()
        if any(m in low for m in CONSTRAINT_MARKERS):
            return "".join(lines[:i]), "".join(lines[i:])
    return text, ""


# ---------------------------------------------------------------- 改写动作

def rewrite_weak_style(text, dia, changes, constraints):
    """空词从正文摘除，转成末尾的渲染约束行——原地替换会破坏句法。"""
    for pat, fix in R.WEAK_STYLE_DETERMINISTIC:
        for m in list(re.finditer(pat, text, re.I)):
            before = m.group(0)
            # 连同引导词一起摘掉：「弥漫着电影感」「充满高级感」
            lead = re.search(r"(?:弥漫着?|充满|透着|带着|很有|无比|极其|十分)*$", text[max(0, m.start() - 6): m.start()])
            drop_from = m.start() - (lead.end() if lead else 0)
            text = text[:drop_from] + text[m.end():]
            constraints.append(fix)
            changes.append({
                "kind": "weak-style-word",
                "before": before,
                "after": f"（已移出正文，转入渲染约束：{fix}）",
                "reason": "空词无法映射到像素；原地替换会破坏句法，因此移出正文",
            })
    return text


def rewrite_emotion(text, dia, changes):
    """「主语 + 心理动词 + 情绪词」→「主语 + 身体信号」。

    只处理句法明确的结构：主语后紧跟心理动词再跟情绪词。
    心理动词被身体信号取代，因此不需要保留「感到」。
    """
    pattern = re.compile(
        r"(?P<subj>[一-龥A-Za-z]{1,6})\s*"
        r"(?P<verb>感到|感觉到|觉得|显得|看起来|显得十分|变得)\s*"
        r"(?P<mid>[^\s，。；：、]{0,8}?)"
        r"(?P<adv>非常|十分|极其|特别|很|有点|些许)*\s*"
        r"(?P<emo>" + "|".join(e for e, _ in R.EMOTION_TO_BODY) + r")"
        r"(?P<tail>的|地|了)?"
        r"(?=\s*[,，。；：、!?！？\n]|$)"
    )

    def _rep(m):
        emo = m.group("emo")
        body = None
        for pat, sig in R.EMOTION_TO_BODY:
            if re.fullmatch(pat, emo):
                body = sig
                break
        if not body:
            return m.group(0)
        before = m.group(0)
        after = f"{m.group('subj')}{body}"
        changes.append({
            "kind": "emotion-unembodied",
            "before": before,
            "after": after,
            "reason": f"情绪「{emo}」改成身体信号，模型才画得出来",
        })
        return after

    return pattern.sub(_rep, text)


def dedupe_in_sentence(sentence: str) -> str:
    """同一句内重复的短语删掉一份（改写后可能出现「目光落在某处，眼神专注」×2）。

    按小句切分并保留原标点，只删除内容完全相同的后续小句。
    """
    parts = re.split(r"([，。；：、,;:!?！？])", sentence)
    seen = []
    out = []
    i = 0
    while i < len(parts):
        chunk = parts[i]
        key = chunk.strip()
        if len(key) >= 4 and any(key in s for s in seen):
            # 删掉这个小句，但它后面的标点保留（避免粘连）
            i += 2 if i + 1 < len(parts) else 1
            continue
        if len(key) >= 4:
            seen.append(key)
        out.append(chunk)
        i += 1
    return "".join(out)


def rewrite_light(text, dia, changes):
    """一次扫描替换，避免「眼中透出金光」这类叠加写法被改两次。"""
    if not dia.get("color_as_emitter_risk"):
        return text

    def _rep(m):
        fix = None
        for name, val in R.LIGHT_HAZARD_FIX.items():
            if m.group(name) is not None:
                fix = val
                break
        if not fix:
            return m.group(0)
        before = m.group(0)
        changes.append({
            "kind": "light-emitter-risk",
            "before": before,
            "after": fix,
            "reason": "颜色/光改为反射体或照明来源，避免被渲染成自发光",
        })
        return fix

    text = R.LIGHT_HAZARD_MERGED.sub(_rep, text)
    # 句内去重（按句号/问号/感叹号切，保留标点）
    out = []
    for s in re.split(r"(?<=[。！？!?])", text):
        out.append(dedupe_in_sentence(s))
    return "".join(out)


def rewrite_literal_text(text, dia, changes):
    """把引号里的具体文字换成参考图占位——保留外层动词，句法不破。"""
    if not dia.get("literal_onscreen_text_risk"):
        return text

    def _rep(m):
        before = m.group(0)
        after = m.group(0).replace(m.group(1), "<Picture N>")
        changes.append({
            "kind": "literal-onscreen-text",
            "before": before,
            "after": after,
            "reason": "画面内具体文字交给参考图或后期；模型自己画的字必然是错的",
        })
        return after

    return R.LITERAL_TEXT.sub(_rep, text)


def rewrite_negative_block(text, dia, changes):
    """没有独立负面字段的模型：负面语义翻成正面约束，原地替换不改句法。"""
    if dia.get("negative_prompt_field"):
        return text

    mapping = [
        ("blurry", "sharp focus, crisp details"),
        ("low quality", "sharp focus, crisp details"),
        ("low resolution", "fine surface texture, resolved micro-detail"),
        ("jpeg", "clean image, no compression artifacts"),
        ("deformed", "stable facial features, consistent anatomy"),
        ("extra fingers", "correct finger count, natural hand anatomy"),
        ("distorted", "correct proportions, undistorted geometry"),
        ("ghost", "clean edges, no ghosting"),
        ("overexposed", "balanced exposure, preserved highlight detail"),
        ("noise", "clean image, minimal noise"),
        ("watermark", "no text overlays, logos, or watermarks"),
        ("低清", "sharp focus, crisp details"),
        ("低质量", "sharp focus, crisp details"),
        ("变形", "stable facial features, consistent anatomy"),
        ("模糊", "sharp focus, crisp details"),
        ("鬼影", "clean edges, no ghosting"),
        ("过曝", "balanced exposure, preserved highlight detail"),
        ("噪点", "clean image, minimal noise"),
        ("水印", "no text overlays, logos, or watermarks"),
    ]

    def _rep(m):
        body = m.group(1)
        items = [i.strip() for i in re.split(r"[,，、;；]", body) if i.strip()]
        if not items:
            return m.group(0)
        positives = []
        for item in items:
            hit = None
            for k, v in mapping:
                if k.lower() in item.lower():
                    hit = v
                    break
            positives.append(hit or item)
        after = "; ".join(dict.fromkeys(positives))
        changes.append({
            "kind": "negative-prompt-block",
            "before": m.group(0).strip(),
            "after": after,
            "reason": f"{dia['display_name']} 没有负面字段，负面语义改写为正面约束",
        })
        return after

    return re.sub(
        r"(?im)^[ \t]*(?:negative\s*prompt|negative_prompt|负面提示词|反向提示词)[ \t]*[:：][ \t]*([^\n]+)",
        _rep, text)


def rewrite_redundant(text, dia, changes):
    def _rep(m):
        before = m.group(0)
        half = before[: len(before) // 2]
        changes.append({
            "kind": "redundant-adverb",
            "before": before,
            "after": half,
            "reason": "叠加副词力度不翻倍，保留一次",
        })
        return half
    return R.REDUNDANT_ADVERB.sub(_rep, text)


# ---------------------------------------------------------------- 对白容量与节奏压缩

# 对白识别（与 lint 的 check_dialogue_capacity 同口径）
DIALOGUE_TAG_RE = re.compile(r"<d>\s*(?:\[[a-zA-Z_-]+\])?\s*([^<]*)</d>", re.I)
DIALOGUE_LINE_RE = re.compile(
    r"(?m)^\s*([^:\n]{1,20})\s*[::]\s*[\"“]([^\"”]{1,200})[\"”]")

# 节奏压缩：只删「不承载信息」的填充成分，不动实词、不改句法。
# 每条必须是「删了之后句子仍然通顺、语义不变」的，宁可少删。
FILLER_PATTERNS = [
    (re.compile(r"那个(?=[，。！？\s]|$)"), ""),      # 句首/停顿填充「那个」
    (re.compile(r"就是(?=[，。！？\s]|$)"), ""),      # 填充「就是」
    (re.compile(r"其实(?=[，。！？\s]|$)"), ""),      # 填充「其实」
    (re.compile(r"然后(?=[，。！？\s]|$)"), ""),      # 填充「然后」
    (re.compile(r"(?:呃|嗯|啊)(?=[，。！？\s]|$)"), ""),  # 语气词
    (re.compile(r"([一-龥])\1{1,}"), r"\1"),          # 叠字「缓缓缓缓」→「缓缓」
    (re.compile(r"\s{2,}"), " "),                      # 多余空格
]


def dialogue_segments(text: str) -> list[tuple[str, int, int]]:
    """返回 [(对白内容, 起点, 终点)]，标签式与「角色: “…”」式都认。"""
    segs = []
    for m in DIALOGUE_TAG_RE.finditer(text):
        segs.append((m.group(1), m.start(1), m.end(1)))
    for m in DIALOGUE_LINE_RE.finditer(text):
        if m.group(1).strip() in ("Narration", "旁白", "字幕", "Title", "标题"):
            continue
        segs.append((m.group(2), m.start(2), m.end(2)))
    return segs


def compress_for_rhythm(seg: str) -> str:
    """压缩单段对白的节奏——只删填充成分，不碰实词。"""
    out = seg
    for pat, rep in FILLER_PATTERNS:
        out = pat.sub(rep, out)
    # 收尾：删填充词后残留的孤立标点（句首/句尾/连续），避免「，，其实」
    out = re.sub(r"^[，、\s]+", "", out)
    out = re.sub(r"[，、]{2,}", "，", out)
    out = re.sub(r"[，、]+(?=[。！？\n]|$)", "", out)
    return out.strip()


def rewrite_dialogue_budget(text: str, dia: dict, changes: list[dict]) -> tuple[str, dict]:
    """对白容量换算 + 节奏压缩。

    按方言语速(cps)把台词换算成朗读秒数；超过该模型单次上限(max_seconds)时，
    只对超窗的对白做节奏压缩（删填充词）。压到窗口内就替换；压不下来就只记
    「建议」，不硬改——保语义优先于压时长。

    返回 (text, budget_report)。budget_report 始终给出换算数据，即使未压缩。
    """
    cps = float(dia.get("dialogue_cps") or 4.1)
    max_s = float(dia.get("max_seconds") or 0)
    budget_chars = int(max_s * cps)

    segs = dialogue_segments(text)
    total_chars = sum(len(re.sub(r"\s+", "", s)) for s, _, _ in segs)
    total_secs = R.speaking_seconds("".join(s for s, _, _ in segs), cps)

    report = {
        "cps": cps,
        "max_seconds": max_s,
        "budget_chars": budget_chars,
        "dialogue_count": len(segs),
        "total_chars": total_chars,
        "estimated_seconds": round(total_secs, 1),
        "over_window": bool(max_s and total_secs > max_s),
        "compressed_segments": 0,
        "advice": [],
    }
    if not segs:
        return text, report

    # 从后往前替换，避免位置偏移
    new_text = text
    for seg, start, end in reversed(segs):
        seg_secs = R.speaking_seconds(seg, cps)
        if not max_s or seg_secs <= max_s:
            continue
        compressed = compress_for_rhythm(seg)
        if compressed == seg:
            report["advice"].append(
                f"对白约 {seg_secs:.1f}s 超窗口 {max_s}s，但无填充词可压——"
                f"需作者自己精简台词或拆成多次生成")
            continue
        comp_secs = R.speaking_seconds(compressed, cps)
        changes.append({
            "kind": "dialogue-rhythm-compress",
            "before": seg,
            "after": compressed,
            "reason": f"台词 {seg_secs:.1f}s 超 {max_s}s 窗口，删填充词压到 {comp_secs:.1f}s（未动实词）",
        })
        new_text = new_text[:start] + compressed + new_text[end:]
        report["compressed_segments"] += 1
        if comp_secs > max_s:
            report["advice"].append(
                f"压后仍 {comp_secs:.1f}s 超窗口——填充词有限，需作者精简核心台词")

    # 更新压缩后的总时长
    if report["compressed_segments"]:
        after_segs = [s for s, _, _ in dialogue_segments(new_text)]
        report["total_chars"] = sum(len(re.sub(r"\s+", "", s)) for s in after_segs)
        report["estimated_seconds"] = round(
            R.speaking_seconds("".join(after_segs), cps), 1)
        report["over_window"] = bool(max_s and report["estimated_seconds"] > max_s)

    return new_text, report


def cleanup_punct(text: str) -> str:
    """清理摘词后残留的重复标点与空格。"""
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[，,]\s*。", "。", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[，,]\s*(?=\n)", "", text)
    return text


def rewrite_all(text: str, dia: dict) -> tuple[str, list[dict], dict]:
    changes: list[dict] = []
    constraints: list[str] = []

    body, tail = split_constraints(text)
    body = rewrite_weak_style(body, dia, changes, constraints)
    body = rewrite_emotion(body, dia, changes)
    body = rewrite_literal_text(body, dia, changes)
    body = rewrite_negative_block(body, dia, changes)
    body = rewrite_light(body, dia, changes)
    body = rewrite_redundant(body, dia, changes)
    body, budget = rewrite_dialogue_budget(body, dia, changes)
    body = cleanup_punct(body)

    # 约束区
    if constraints:
        uniq = list(dict.fromkeys(constraints))
        block = "\n渲染约束: " + "; ".join(uniq) + "\n"
        if tail:
            tail = block + tail
        else:
            tail = block
        changes.append({
            "kind": "constraint-block",
            "before": "（无）",
            "after": block.strip(),
            "reason": "从正文摘出的空词，转成可执行的渲染约束",
        })

    result = body.rstrip() + "\n" + tail.lstrip("\n") if tail else body
    return result, changes, budget


# ---------------------------------------------------------------- 报告

def print_changes(changes, stream=sys.stdout):
    if not changes:
        print("没有可自动改写的地方。", file=stream)
        return
    print(f"\n---- 自动改写 {len(changes)} 处 ----", file=stream)
    for i, c in enumerate(changes, 1):
        print(f"\n{i}. [{c['kind']}] {c['reason']}", file=stream)
        print(f"   - {c['before']}", file=stream)
        print(f"   + {c['after']}", file=stream)


def print_diff(before: str, after: str, stream=sys.stdout):
    d = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="before",
        tofile="after",
        n=1,
    )
    out = "".join(d)
    if out:
        print("\n---- diff ----", file=stream)
        print(out, file=stream)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="行文改写器：把不可拍的行文改成可拍的。")
    p.add_argument("file", nargs="?", help="文本文件；用 - 或省略则读标准输入")
    p.add_argument("--dialect", default="generic")
    p.add_argument("--out", help="改写结果写入该文件")
    p.add_argument("--diff", action="store_true", help="输出 diff")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if args.file and args.file != "-":
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    else:
        text = sys.stdin.buffer.read().decode("utf-8", errors="replace")

    if not text.strip():
        print("输入为空。", file=sys.stderr)
        return 2

    dia = R.get_dialect(args.dialect)
    after, changes, budget = rewrite_all(text, dia)

    if args.out:
        Path(args.out).write_text(after, encoding="utf-8")
        print(f"已写入 {args.out}（{len(changes)} 处改写）")
        if args.diff:
            print_diff(text, after)
        return 0

    if args.json:
        print(json.dumps({
            "dialect": args.dialect,
            "dialect_name": dia["display_name"],
            "change_count": len(changes),
            "changes": changes,
            "dialogue_budget": budget,
            "rewritten": after,
        }, ensure_ascii=False, indent=2))
        return 0

    print(after)
    if args.diff:
        print_diff(text, after)
    print_changes(changes)
    # 对白容量换算始终可见
    if budget["dialogue_count"]:
        mark = "⚠ 超窗口" if budget["over_window"] else "✓ 窗口内"
        print(f"\n---- 对白容量：{budget['dialogue_count']} 段 / {budget['total_chars']} 字"
              f" / ≈{budget['estimated_seconds']}s (语速{budget['cps']}字/s，上限{budget['max_seconds']}s) {mark}")
        for a in budget["advice"]:
            print(f"  建议: {a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
