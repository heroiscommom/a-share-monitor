#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
决策信号结果追踪（2026-08-26 新增，P1b）
================================================
把每次 auto_report 的持仓建议落库，10 个交易日后自动回填实际结果，
统计「建议准确率」——这是比回测更接近实盘真实表现的数据。

data/signal_history.json:
  {"signals": [{"id", "date", "code", "name", "advice", "level",
                "price", "support", "resistance", "filled", "result"}]}
  result: "win" | "fail" | "hold" | null

判定口径（10 交易日，扣 0.5% 成本余量）：
  buy类(低吸加仓):  10日后收盘 > 信号日收盘 → win
  sell类(止损/止盈/减仓): 10日后收盘 < 信号日收盘 → win（卖出躲过下跌）
  hold: 中性，只标记不参与准确率
"""

import os
import json
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SIGNAL_PATH = os.path.join(DATA_DIR, "signal_history.json")

FORWARD_DAYS = 10
COST_BUFFER = 0.005  # 0.5% 成本余量

BUY_LEVELS = ("buy",)
SELL_LEVELS = ("sell", "trim")


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def record_signals(advices, date=None):
    """记录一批建议（同日期同代码去重更新）"""
    date = date or datetime.date.today().isoformat()
    obj = load_json(SIGNAL_PATH, {"signals": []})
    existing = {s["code"]: s for s in obj["signals"] if s["date"] == date}
    for a in advices:
        if a.get("price") is None or a.get("advice") in (None, "数据缺失"):
            continue
        if a["code"] in existing:
            continue  # 同日已记录
        obj["signals"].append({
            "id": len(obj["signals"]) + 1,
            "date": date,
            "code": a["code"],
            "name": a.get("name", a["code"]),
            "advice": a["advice"],
            "level": a.get("level", "hold"),
            "price": a["price"],
            "support": a.get("support"),
            "resistance": a.get("resistance"),
            "filled": False,
            "result": None,
        })
    obj["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(SIGNAL_PATH, obj)
    return len(advices)


def _history_for(code):
    hist = load_json(os.path.join(DATA_DIR, "history", f"{code}.json"), [])
    if hist:
        return hist
    return load_json(os.path.join(DATA_DIR, "pool", f"{code}.json"), [])


def check_and_fill():
    """回填所有到期未判定信号"""
    obj = load_json(SIGNAL_PATH, {"signals": []})
    n_filled = 0
    for s in obj["signals"]:
        if s.get("filled"):
            continue
        hist = _history_for(s["code"])
        if len(hist) < 20:
            continue
        target = s["date"]
        idx = -1
        for i, h in enumerate(hist):
            if h["date"] == target:
                idx = i
                break
        if idx < 0 or idx + FORWARD_DAYS >= len(hist):
            continue  # 数据不足，等下次
        p10 = hist[idx + FORWARD_DAYS]["close"]
        p0 = s["price"]
        level = s.get("level", "hold")
        if level in BUY_LEVELS:
            s["result"] = "win" if p10 > p0 * (1 + COST_BUFFER) else "fail"
        elif level in SELL_LEVELS:
            s["result"] = "win" if p10 < p0 * (1 - COST_BUFFER) else "fail"
        else:
            s["result"] = "hold"
        s["filled"] = True
        s["p10"] = round(p10, 2)
        n_filled += 1
    if n_filled:
        obj["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_json(SIGNAL_PATH, obj)
    return n_filled


def accuracy_stats():
    """建议准确率统计"""
    obj = load_json(SIGNAL_PATH, {"signals": []})
    filled = [s for s in obj["signals"] if s.get("filled") and s.get("result") in ("win", "fail")]
    buy = [s for s in filled if s["level"] in BUY_LEVELS]
    sell = [s for s in filled if s["level"] in SELL_LEVELS]
    total = [s for s in filled]

    def stat(name, group):
        if not group:
            return None
        win = sum(1 for s in group if s["result"] == "win")
        return {"name": name, "n": len(group), "win_rate": round(win / len(group) * 100, 1),
                "wins": win}

    out = {
        "total_signals": len(obj["signals"]),
        "pending": sum(1 for s in obj["signals"] if not s.get("filled")),
        "buy": stat("买入类", buy),
        "sell": stat("卖出类", sell),
        "all": stat("全部已判定", total),
    }
    return out


def stats_text(stats):
    """格式化为报告文本"""
    if not stats or stats["total_signals"] == 0:
        return "暂无信号记录（每天报告自动积累）"
    lines = [f"累计信号 {stats['total_signals']} 条（待判定 {stats['pending']}）"]
    for k in ("all", "buy", "sell"):
        st = stats.get(k)
        if st:
            lines.append(f"{st['name']}: {st['n']} 条 准确率 {st['win_rate']}%（胜 {st['wins']}）")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if "--fill" in sys.argv:
        n = check_and_fill()
        print(f"回填 {n} 条")
    st = accuracy_stats()
    print(stats_text(st))
