#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型选型器：按「用户诉求 + 榜单 + 硬件 + 大小速度平衡」算出最合适的免费开源模型。

核心原则（不是唯参数论、不是唯榜单论）：
  - 只收录【免费且允许商用】的开源权重模型（Apache / MIT 等），付费/私有许可一律不进目录。
  - 综合分 = 诉求匹配(40%) + 硬件适配(25%) + 榜单质量(20%) + 大小速度平衡(15%)。
  - 榜单靠前 ≠ 最合适：一个 27B 模型再强，跑不动用户机器就是零分。
  - 数据在本地计算，零联网、零依赖、零付费、零 Key。

用法：
  python3 select_model.py --help
  python3 select_model.py --scene general --ram 24 --mac
  python3 select_model.py --scene coding --ram 16 --need-vision --json
  python3 select_model.py --scene lightweight --ram 8 --json
  python3 select_model.py --list

退出码：0=给出推荐；1=参数错误/无匹配模型。
"""
import argparse
import json
import sys

# 免费可商用开源模型目录（许可证白名单：Apache-2.0 / MIT）。
# size_gb 为 Q4 量化后的大致体积；speed 为相对速度档(5=最快)；quality 为榜单质量档(5=最强)。
# 场景标签：general/coding/reasoning/vision/chinese/lightweight/agent。
CATALOG = [
    {"id": "qwen3.5-9b", "name": "Qwen3.5 9B", "license": "Apache-2.0", "params_b": 9,
     "size_gb": 6.6, "speed": 4, "quality": 4, "vision": True, "chinese": 5,
     "scenes": ["general", "coding", "vision", "chinese", "agent"],
     "note": "16G内存首选，带识图，中文强，综合最均衡"},
    {"id": "qwen3.5-4b", "name": "Qwen3.5 4B", "license": "Apache-2.0", "params_b": 4,
     "size_gb": 3.2, "speed": 5, "quality": 3, "vision": True, "chinese": 5,
     "scenes": ["general", "chinese", "vision", "lightweight"],
     "note": "8-12G内存，速度快，中文好，带识图"},
    {"id": "qwen3.5-0.6b", "name": "Qwen3.5 0.6B", "license": "Apache-2.0", "params_b": 0.6,
     "size_gb": 0.5, "speed": 5, "quality": 2, "vision": False, "chinese": 4,
     "scenes": ["lightweight", "chinese"],
     "note": "手机/嵌入式也能跑，极快，适合简单任务"},
    {"id": "qwen3.5-27b", "name": "Qwen3.5 27B", "license": "Apache-2.0", "params_b": 27,
     "size_gb": 17.0, "speed": 2, "quality": 5, "vision": True, "chinese": 5,
     "scenes": ["general", "coding", "reasoning", "vision", "chinese", "agent"],
     "note": "24G显存/32G统一内存，质量顶尖，速度较慢"},
    {"id": "qwen3.5-35b-a3b", "name": "Qwen3.5 35B-A3B", "license": "Apache-2.0", "params_b": 35,
     "size_gb": 24.0, "speed": 3, "quality": 5, "vision": False, "chinese": 5,
     "scenes": ["general", "reasoning", "coding", "chinese"],
     "note": "MoE 只激活3B，纯CPU也能跑，32G内存可上"},
    {"id": "gemma4-e4b", "name": "Gemma 4 E4B", "license": "Gemma-ToU(restricted)", "params_b": 4,
     "size_gb": 3.0, "speed": 5, "quality": 3, "vision": True, "chinese": 3,
     "scenes": ["lightweight", "general", "vision"],
     "note": "8G内存可跑、支持图像；但许可为 Gemma 使用条款，非标准开源许可，商用前须自行核对条款——本工具不作为免费可商用推荐"},
    {"id": "gemma4-12b", "name": "Gemma 4 12B", "license": "Gemma-ToU(restricted)", "params_b": 12,
     "size_gb": 7.6, "speed": 4, "quality": 4, "vision": True, "chinese": 3,
     "scenes": ["general", "coding", "vision"],
     "note": "16G内存、英文推理强；但许可为 Gemma 使用条款，非标准开源许可，商用前须自行核对——本工具不作为免费可商用推荐"},
    {"id": "phi4-14b", "name": "Phi-4 14B", "license": "MIT", "params_b": 14,
     "size_gb": 8.5, "speed": 3, "quality": 4, "vision": False, "chinese": 2,
     "scenes": ["reasoning", "coding", "general"],
     "note": "小尺寸强推理，16G内存舒适"},
    {"id": "glm4.7-flash-30b", "name": "GLM-4.7-Flash 30B-A3B", "license": "MIT", "params_b": 30,
     "size_gb": 24.0, "speed": 3, "quality": 4, "vision": False, "chinese": 5,
     "scenes": ["agent", "coding", "chinese", "general"],
     "note": "MoE，工具调用/智能体任务强，适合跑任务"},
    {"id": "deepseek-r1-8b", "name": "DeepSeek-R1 Distill 8B", "license": "MIT", "params_b": 8,
     "size_gb": 5.0, "speed": 4, "quality": 3, "vision": False, "chinese": 5,
     "scenes": ["reasoning", "coding", "chinese", "lightweight"],
     "note": "思维链推理，8G显存可跑"},
]

# 免费商用许可证白名单——不在此列的模型不得进入推荐
LICENSE_WHITELIST = {"Apache-2.0", "MIT"}


def fit_by_hardware(m, ram, mac):
    """硬件能否跑 + 余量评分。Mac 统一内存可分配约 70% 给模型。"""
    usable = ram * (0.70 if mac else 0.60)
    need = m["size_gb"]
    if need > usable:
        return 0.0, False
    # 留出余量最好：占用在可用内存的 30%-70% 之间体验最佳
    ratio = need / usable if usable else 1
    if ratio <= 0.15:
        return 2.0, True   # 太富余，性能没吃满
    if ratio <= 0.70:
        return 3.0, True   # 甜点区
    return 2.5, True       # 能跑但吃紧


def score(m, scene, ram, mac, need_vision):
    if m["license"] not in LICENSE_WHITELIST:
        return None
    if need_vision and not m["vision"]:
        return None
    hw, ok = fit_by_hardware(m, ram, mac)
    if not ok:
        return None
    # 诉求匹配 40
    scene_hit = 40.0 if scene in m["scenes"] else 12.0
    if need_vision and m["vision"]:
        scene_hit += 3
    # 硬件适配 25
    hw_sc = hw / 3.0 * 25.0
    # 榜单质量 20
    q_sc = m["quality"] / 5.0 * 20.0
    # 大小速度平衡 15：速度优先但不牺牲太多质量
    bal = (m["speed"] * 0.6 + m["quality"] * 0.4) / 5.0 * 15.0
    total = scene_hit + hw_sc + q_sc + bal
    return total, {"scene": round(scene_hit, 1), "hardware": round(hw_sc, 1),
                   "quality": round(q_sc, 1), "balance": round(bal, 1)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="模型选型器：选出最适合的免费可商用开源模型（本地、零依赖）")
    ap.add_argument("--scene", default="general",
                    choices=["general", "coding", "reasoning", "vision", "chinese", "lightweight", "agent"],
                    help="用户主要诉求/场景")
    ap.add_argument("--ram", type=float, default=16, help="本机内存(GB)")
    ap.add_argument("--mac", action="store_true", help="是否 Mac 统一内存")
    ap.add_argument("--need-vision", action="store_true", help="是否需要图像/多模态输入")
    ap.add_argument("--list", action="store_true", help="列出全部模型目录")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if args.list:
        for m in CATALOG:
            print(f"{m['id']:22s} {m['size_gb']:5.1f}GB  {m['license']:11s} q{m['quality']} s{m['speed']}  {m['note']}")
        return 0

    ranked = []
    for m in CATALOG:
        r = score(m, args.scene, args.ram, args.mac, args.need_vision)
        if r:
            ranked.append((r[0], m, r[1]))
    if not ranked:
        print("没有符合条件的模型（可能内存不足或需要图像能力），请放宽条件。", file=sys.stderr)
        return 1
    ranked.sort(key=lambda x: -x[0])

    best_score, best, breakdown = ranked[0]
    result = {
        "scene": args.scene,
        "hardware": {"ram_gb": args.ram, "mac_unified": args.mac},
        "recommended": {
            "id": best["id"], "name": best["name"], "license": best["license"],
            "size_gb": best["size_gb"], "params_b": best["params_b"],
            "vision": best["vision"], "note": best["note"],
            "score": round(best_score, 1), "score_breakdown": breakdown,
            "why": f"综合分最高：诉求匹配{breakdown['scene']}、硬件适配{breakdown['hardware']}、榜单质量{breakdown['quality']}、大小速度{breakdown['balance']}",
        },
        "alternatives": [
            {"id": m["id"], "name": m["name"], "score": round(s, 1), "note": m["note"]}
            for s, m, _ in ranked[1:4]
        ],
        "disclaimer": "目录仅收录免费可商用许可证(Apache-2.0/MIT)模型；综合分为启发式估算，非绝对排名。榜单靠前不等于最合适，硬件跑得动且匹配诉求才是第一优先。",
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print(f"场景：{args.scene} | 硬件：{args.ram}GB {'Mac统一内存' if args.mac else '普通'}"
              f" | 需要图像：{'是' if args.need_vision else '否'}")
        print("=" * 60)
        r = result["recommended"]
        print(f"\n★ 推荐：{r['name']}  (综合 {r['score']})")
        print(f"  许可证：{r['license']}（免费可商用） | 体积：{r['size_gb']}GB | 参数：{r['params_b']}B")
        print(f"  图像：{'支持' if r['vision'] else '不支持'}")
        print(f"  理由：{r['why']}")
        print(f"  备注：{r['note']}")
        print("\n  备选：")
        for a in result["alternatives"]:
            print(f"    - {a['name']:24s} {a['score']}  {a['note']}")
        print(f"\n  {result['disclaimer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
