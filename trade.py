#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易记录模块（2026-08-26 新增，P2）
================================================
手工录入每一笔买卖 → 自动推导当前持仓（加权平均成本）+ 已实现/浮动盈亏。
这是个人交易系统最核心的「数据资产」：建议→执行→记录→复盘 闭环的中间环节。

用法：
  python3 trade.py buy  600664 900 9.107          # 买入 900股 @9.107
  python3 trade.py sell 600664 300 8.50           # 卖出 300股 @8.50
  python3 trade.py pnl  002900 -4961 "哈三联清仓"  # 记录一笔已实现盈亏（不计持仓）
  python3 trade.py list                            # 查看全部流水
  python3 trade.py pos                             # 查看当前持仓 + 盈亏
  python3 trade.py undo                            # 撤销最后一条

数据：data/trades.json（与代码一起提交，GitHub Actions 也能读）
"""

import os
import sys
import json
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_PATH = os.path.join(BASE_DIR, "data", "trades.json")


def load_trades():
    if os.path.exists(TRADES_PATH):
        try:
            with open(TRADES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"trades": []}


def save_trades(obj):
    os.makedirs(os.path.dirname(TRADES_PATH), exist_ok=True)
    with open(TRADES_PATH, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def next_id(trades):
    return max((t.get("id", 0) for t in trades), default=0) + 1


def add_trade(side, code, shares, price, name="", reason="手动录入", fee=0.0, date=None):
    obj = load_trades()
    t = {
        "id": next_id(obj["trades"]),
        "date": date or datetime.date.today().isoformat(),
        "code": code,
        "name": name or code,
        "side": side,            # buy / sell / pnl
        "shares": float(shares),
        "price": float(price),
        "fee": float(fee),
        "reason": reason,
    }
    obj["trades"].append(t)
    obj["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_trades(obj)
    print(f"✓ 已记录: [{t['date']}] {side.upper()} {name}({code}) {shares}股 @{price} 手续费{fee}")


def positions_from_trades(trades):
    """
    从流水推导当前持仓（先进先出/加权平均成本）。
    返回 {code: {name, shares, avg_cost, total_cost, realized_pnl}}
    """
    pos = {}
    for t in sorted(trades, key=lambda x: (x["date"], x["id"])):
        code = t["code"]
        if code not in pos:
            pos[code] = {"name": t.get("name", code), "shares": 0.0,
                         "avg_cost": 0.0, "total_cost": 0.0, "realized_pnl": 0.0}
        p = pos[code]
        if t["side"] == "buy":
            cost = t["shares"] * t["price"] + t.get("fee", 0)
            new_shares = p["shares"] + t["shares"]
            p["avg_cost"] = (p["total_cost"] + cost) / new_shares if new_shares else 0
            p["total_cost"] += cost
            p["shares"] = new_shares
        elif t["side"] == "sell":
            # 卖出：按平均成本结算已实现盈亏
            sell_val = t["shares"] * t["price"] - t.get("fee", 0)
            cost_basis = p["avg_cost"] * t["shares"]
            p["realized_pnl"] += sell_val - cost_basis
            p["shares"] = max(0, p["shares"] - t["shares"])
            p["total_cost"] = p["avg_cost"] * p["shares"]
        elif t["side"] == "pnl":
            p["realized_pnl"] += t["shares"]  # shares 字段存盈亏金额
    # 清仓的股票（shares=0）也保留已实现盈亏信息
    return pos


def show_positions(quotes=None):
    obj = load_trades()
    pos = positions_from_trades(obj["trades"])
    if not pos:
        print("暂无交易记录")
        return
    total_mv = 0.0
    total_pnl = 0.0
    print(f"{'代码':<8}{'名称':<10}{'持仓':>8}{'均价':>10}{'现价':>10}{'浮动盈亏':>10}{'已实现':>10}")
    for code, p in sorted(pos.items()):
        mv = 0.0
        if p["shares"] > 0:
            price = (quotes or {}).get(code)
            price = price if price is not None else 0
            mv = p["shares"] * price
            float_pnl = mv - p["avg_cost"] * p["shares"]
            total_mv += mv
            total_pnl += float_pnl
            print(f"{code:<8}{p['name']:<10}{p['shares']:>8.0f}{p['avg_cost']:>10.3f}"
                  f"{price if price else 0:>10.2f}{float_pnl:>+10.0f}{p['realized_pnl']:>+10.0f}")
        else:
            print(f"{code:<8}{p['name']:<10}{'清仓':>8}{'':>10}{'':>10}{'':>10}{p['realized_pnl']:>+10.0f}")
        total_pnl += p["realized_pnl"]
    print("-" * 66)
    print(f"持仓市值 {total_mv:,.0f} ｜ 总盈亏(浮动+已实现) {total_pnl:+,.0f}")


def show_trades(limit=30):
    obj = load_trades()
    trades = obj["trades"][-limit:]
    if not trades:
        print("暂无交易记录")
        return
    for t in reversed(trades):
        side = {"buy": "买入", "sell": "卖出", "pnl": "盈亏"}[t["side"]]
        if t["side"] == "pnl":
            print(f"[{t['date']}] #{t['id']} {side} {t['name']}({t['code']}) {t['shares']:+.0f} ｜ {t.get('reason', '')}")
        else:
            print(f"[{t['date']}] #{t['id']} {side} {t['name']}({t['code']}) {t['shares']:.0f}股 @{t['price']} ｜ {t.get('reason', '')}")


def undo():
    obj = load_trades()
    if not obj["trades"]:
        print("无可撤销记录")
        return
    t = obj["trades"].pop()
    save_trades(obj)
    print(f"✗ 已撤销: #{t['id']} {t['side']} {t['name']}({t['code']})")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "buy" and len(args) >= 4:
        add_trade("buy", args[1], args[2], args[3], name=args[4] if len(args) > 4 else "",
                  reason=args[5] if len(args) > 5 else "买入")
    elif cmd == "sell" and len(args) >= 4:
        add_trade("sell", args[1], args[2], args[3], name=args[4] if len(args) > 4 else "",
                  reason=args[5] if len(args) > 5 else "卖出")
    elif cmd == "pnl" and len(args) >= 3:
        add_trade("pnl", args[1], args[2], 0, name=args[3] if len(args) > 3 else "",
                  reason="已实现盈亏记录")
    elif cmd == "list":
        show_trades()
    elif cmd == "pos":
        show_positions()
    elif cmd == "undo":
        undo()
    else:
        print("用法见文档头注释")


if __name__ == "__main__":
    main()
