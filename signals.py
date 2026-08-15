#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
买卖点引擎 —— 综合量化数据，生成每只股票的日线/分时买点卖点。

日线买点 = 支撑位（回踩不破=买）；日线卖点 = 压力位（反弹不过=卖）
分时买点 = 分时均价线（VWAP）+ 日内低点；分时卖点 = 日内高点 + 均价线上方
"""

import support_resistance


def _dedupe(points, tol=0.005):
    """合并价格接近（<0.5%）的点位"""
    points = sorted(points, key=lambda x: x["price"])
    out = []
    for p in points:
        if out and abs(p["price"] - out[-1]["price"]) / out[-1]["price"] < tol:
            continue
        out.append(p)
    return out


def compute_intraday_points(intraday):
    """分时买点/卖点"""
    buys, sells = [], []
    if not intraday:
        return buys, sells
    minutes = intraday.get("minutes") or []
    prices = [m.get("p") for m in minutes if m.get("p") is not None]
    if len(prices) < 2:
        return buys, sells
    vwap = minutes[-1].get("avg")      # 当日均价线（成交量加权）
    day_high = max(prices)
    day_low = min(prices)
    if vwap:
        buys.append({"price": round(vwap, 2), "type": "分时均价线"})
    buys.append({"price": round(day_low, 2), "type": "日内低点"})
    sells.append({"price": round(day_high, 2), "type": "日内高点"})
    if vwap:
        sells.append({"price": round(vwap * 1.02, 2), "type": "均价线+2%"})
    return _dedupe(buys), _dedupe(sells)


def compute_signals(history, intraday):
    """返回 {daily_buy, daily_sell, intraday_buy, intraday_sell}"""
    sr = support_resistance.compute_levels(history)
    ibuy, isell = compute_intraday_points(intraday)
    return {
        "daily_buy": [{"price": s["price"], "strength": s["strength"]} for s in sr["supports"]],
        "daily_sell": [{"price": r["price"], "strength": r["strength"]} for r in sr["resistances"]],
        "intraday_buy": ibuy,
        "intraday_sell": isell,
    }


if __name__ == "__main__":
    import json
    hist = json.load(open("data/history/600036.json", "r", encoding="utf-8"))
    intra = json.load(open("data/intraday/600036.json", "r", encoding="utf-8"))
    s = compute_signals(hist, intra)
    print("日线买点(支撑):", [(p["price"], p["strength"]) for p in s["daily_buy"]])
    print("日线卖点(压力):", [(p["price"], p["strength"]) for p in s["daily_sell"]])
    print("分时买点:", [(p["price"], p["type"]) for p in s["intraday_buy"]])
    print("分时卖点:", [(p["price"], p["type"]) for p in s["intraday_sell"]])
