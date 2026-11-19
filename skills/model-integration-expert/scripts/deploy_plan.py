#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地部署规划器：按硬件算出能跑的模型规模，给出 Ollama 部署命令与 WorkBuddy 接入配置。

核心：把"接入一个免费开源模型到本地"拆成可执行的步骤清单，并生成
WorkBuddy models.json 所需的配置条目（OpenAI 兼容端点）。

不自动执行任何安装/改配置动作——只产出命令和配置，由用户确认后自行执行。
零依赖、零联网、零付费。

用法：
  python3 deploy_plan.py qwen3.5-9b --ram 24 --mac
  python3 deploy_plan.py qwen3.5-4b --ram 8 --json
  python3 deploy_plan.py --help

退出码：0=生成方案；1=参数错误/模型未知。
"""
import argparse
import json
import sys

# 模型 -> Ollama 标签映射（免费开源模型）
OLLAMA_TAGS = {
    "qwen3.5-9b": "qwen3.5:9b",
    "qwen3.5-4b": "qwen3.5:4b",
    "qwen3.5-0.6b": "qwen3.5:0.6b",
    "qwen3.5-27b": "qwen3.5:27b",
    "qwen3.5-35b-a3b": "qwen3.5:35b-a3b",
    "gemma4-e4b": "gemma4:e4b",
    "gemma4-12b": "gemma4:12b",
    "phi4-14b": "phi4:14b",
    "glm4.7-flash-30b": "glm4.7-flash:30b",
    "deepseek-r1-8b": "deepseek-r1:8b",
}

# 模型 Q4 量化后的大致体积（GB），用于硬件可行性校验
MODEL_SIZE_GB = {
    "qwen3.5-9b": 6.6, "qwen3.5-4b": 3.2, "qwen3.5-0.6b": 0.5,
    "qwen3.5-27b": 17.0, "qwen3.5-35b-a3b": 24.0, "gemma4-e4b": 3.0,
    "gemma4-12b": 7.6, "phi4-14b": 8.5, "glm4.7-flash-30b": 24.0,
    "deepseek-r1-8b": 5.0,
}

# 许可标注：只推荐免费可商用模型；受限许可要显式警告
MODEL_LICENSE = {
    "gemma4-e4b": "Gemma-ToU(restricted)", "gemma4-12b": "Gemma-ToU(restricted)",
}


def plan(model_id, ram, mac):
    tag = OLLAMA_TAGS.get(model_id)
    if not tag:
        return None
    usable = ram * (0.70 if mac else 0.60)
    need = MODEL_SIZE_GB.get(model_id, 0)
    warnings = []
    if need > usable:
        warnings.append(
            f"硬件不足：模型约需 {need}GB，本机约可分配 {usable:.1f}GB——建议换更小模型或加内存")
    lic = MODEL_LICENSE.get(model_id, "Apache-2.0/MIT")
    if "restricted" in lic:
        warnings.append(
            f"许可提醒：{model_id} 使用 {lic}，非标准开源许可，商用前请自行核对条款")
    steps = [
        {"n": 1, "action": "安装 Ollama（本地推理运行时，免费开源）",
         "cmd": "curl -fsSL https://ollama.com/install.sh | sh",
         "note": "macOS 也可下载 Ollama.app；安装后后台服务默认监听 11434 端口"},
        {"n": 2, "action": "拉取模型权重（首轮下载，之后离线可用）",
         "cmd": f"ollama pull {tag}",
         "note": "只从官方 Ollama 库拉取，无付费、无 API Key"},
        {"n": 3, "action": "验证本地推理可用",
         "cmd": f"ollama run {tag} '用一句话介绍你自己'",
         "note": "能返回文字即表示本地服务正常"},
        {"n": 4, "action": "确认 OpenAI 兼容端点在线",
         "cmd": "curl -s http://localhost:11434/v1/models",
         "note": "返回 JSON 模型列表即表示可被 WorkBuddy 接入"},
    ]
    # WorkBuddy models.json 配置条目（OpenAI 兼容协议）
    wb_config = {
        "id": model_id,
        "name": model_id,
        "vendor": "LocalOllama",
        "url": "http://localhost:11434/v1/chat/completions",
        "apiKey": "ollama",
        "supportsToolCall": True,
        "supportsImages": model_id in ("qwen3.5-9b", "qwen3.5-4b", "qwen3.5-27b", "gemma4-e4b", "gemma4-12b"),
        "supportsReasoning": model_id in ("deepseek-r1-8b", "qwen3.5-27b", "phi4-14b"),
        "useCustomProtocol": False,
        "description": f"本地 Ollama 部署的 {model_id}，免费开源，数据不出本机",
    }
    return {
        "model": model_id,
        "ollama_tag": tag,
        "license": lic,
        "hardware": {"ram_gb": ram, "mac_unified": mac, "usable_for_model_gb": round(usable, 1),
                     "model_size_gb": need, "feasible": need <= usable},
        "warnings": warnings,
        "steps": steps,
        "workbuddy_models_json_entry": wb_config,
        "how_to_install_config": [
            "打开 ~/.workbuddy/models.json（数组结构）",
            "把 workbuddy_models_json_entry 整条追加进数组",
            "重启 WorkBuddy，在模型列表选择该条目即可使用",
            "提示：改配置前先备份 models.json",
        ],
        "safety": "本规划器只生成命令与配置，不自动执行安装、拉模型或改配置；所有动作由你确认后自行完成。数据全程留在本机。",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="本地部署规划器：生成 Ollama 部署命令 + WorkBuddy 接入配置")
    ap.add_argument("model", help="模型 ID（见 select_model.py --list）")
    ap.add_argument("--ram", type=float, default=16, help="本机内存 GB")
    ap.add_argument("--mac", action="store_true", help="Mac 统一内存")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    p = plan(args.model, args.ram, args.mac)
    if not p:
        print(f"未知模型：{args.model}（用 select_model.py --list 查可用 ID）", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print(f"本地部署方案：{p['model']}  (Ollama: {p['ollama_tag']})")
        print("=" * 60)
        print(f"硬件：{args.ram}GB {'Mac统一内存' if args.mac else '普通'}，约 {p['hardware']['usable_for_model_gb']}GB 可分配给模型")
        if p["warnings"]:
            print("\n  ⚠ 提醒：")
            for w in p["warnings"]:
                print(f"    ! {w}")
        print("\n部署步骤（按顺序执行）：")
        for s in p["steps"]:
            print(f"\n  {s['n']}. {s['action']}")
            print(f"     $ {s['cmd']}")
            print(f"       {s['note']}")
        print("\nWorkBuddy 接入配置（追加到 ~/.workbuddy/models.json）：")
        print(json.dumps(p["workbuddy_models_json_entry"], ensure_ascii=False, indent=2))
        print("\n接入步骤：")
        for i, s in enumerate(p["how_to_install_config"], 1):
            print(f"  {i}. {s}")
        print(f"\n  {p['safety']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
