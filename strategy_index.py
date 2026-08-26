#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略索引（P1a yaml 化）
================================================
读取 strategies/*.yaml（迷你解析，纯标准库，不引入 pyyaml），
输出 data/strategies.json 供前端动态渲染打法选择器。

yaml 子集格式：
  key: value
  key: [a, b, c]
  - {key: x, label: y, weight: n}
"""

import os
import json
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STRATEGIES_DIR = os.path.join(BASE_DIR, "strategies")
OUT_PATH = os.path.join(BASE_DIR, "data", "strategies.json")


def parse_mini_yaml(text):
    """解析 yaml 子集 → dict"""
    out = {}
    current_list = None
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - "):
            item = line.strip()[3:]
            if current_list is not None:
                if item.startswith("{"):
                    # {key: x, label: y, weight: n}
                    kv = {}
                    for part in item.strip("{}").split(","):
                        if ":" in part:
                            k, v = part.split(":", 1)
                            kv[k.strip()] = v.strip().strip('"').strip("'")
                    current_list.append(kv)
                else:
                    current_list.append(item)
            continue
        if line.startswith("- "):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip()
            if v.startswith("[") and v.endswith("]"):
                v = [x.strip() for x in v[1:-1].split(",") if x.strip()]
            elif re.fullmatch(r"-?\d+", v):
                v = int(v)
            elif re.fullmatch(r"-?\d+\.\d+", v):
                v = float(v)
            if k == "factors":
                current_list = []
                out[k] = current_list
            else:
                out[k] = v
                if k == "signal_rules":
                    current_list = None
    return out


def build_index():
    strategies = []
    if not os.path.isdir(STRATEGIES_DIR):
        return strategies
    for fn in sorted(os.listdir(STRATEGIES_DIR)):
        if not fn.endswith(".yaml"):
            continue
        try:
            with open(os.path.join(STRATEGIES_DIR, fn), "r", encoding="utf-8") as f:
                data = parse_mini_yaml(f.read())
            if data.get("name"):
                data["file"] = fn
                strategies.append(data)
        except Exception as e:
            print(f"[strategy] 解析 {fn} 失败: {e}")
    return strategies


def main():
    strategies = build_index()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump({"strategies": strategies}, f, ensure_ascii=False, indent=2)
    print(f"strategies.json: {len(strategies)} 个策略")
    for s in strategies:
        print(f"  {s.get('name')} ({s.get('display_name')}) 适用{s.get('market_regimes')}")


if __name__ == "__main__":
    main()
