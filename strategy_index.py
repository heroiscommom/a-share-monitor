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


def _scalar(v):
    """yaml 标量：列表 / 整数 / 浮点 / 字符串"""
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        return [x.strip() for x in v[1:-1].split(",") if x.strip()]
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def parse_mini_yaml(text):
    """
    解析 yaml 子集 → dict（2026-08 重构：修复嵌套解析）
    支持：
      key: value
      key: [a, b, c]
      signal_rules:          # 嵌套 dict（缩进子键）
        strong: 70
      factors:               # 对象列表
        - {key: x, label: y, weight: n}
    """
    out = {}
    current_list = None
    signal_rules = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()

        # 列表项：- item 或 - {key: v, ...}
        if stripped.startswith("- "):
            item = stripped[2:]
            if current_list is not None:
                if item.startswith("{"):
                    kv = {}
                    for part in item.strip("{}").split(","):
                        if ":" in part:
                            k, v = part.split(":", 1)
                            kv[k.strip()] = _scalar(v.strip().strip('"').strip("'"))
                    current_list.append(kv)
                else:
                    current_list.append(item)
            continue

        if ":" not in stripped:
            continue
        k, v = stripped.split(":", 1)
        k, v = k.strip(), v.strip()

        # signal_rules 的缩进子键 → 归入当前 dict
        if signal_rules is not None and indent > 0:
            if v:
                signal_rules[k] = _scalar(v)
            continue

        if k == "signal_rules":
            signal_rules = {}
            out[k] = signal_rules
            continue
        if k == "factors":
            current_list = []
            out[k] = current_list
            continue

        signal_rules = None  # 退出嵌套模式
        out[k] = _scalar(v) if v else v
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
