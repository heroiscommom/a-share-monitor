#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
决策闭环模块（2026-08-26 移植自密集成交区项目 zone_history.py，纯标准库版）

为每个支撑/压力位提供「历史表现」统计，让推送/看板不只是报点位，还报胜率：
  1. 历史守住率：回溯全历史中该价位被触及后，是「守住反弹」还是「被穿透」
  2. 触及预警：当前价到各价位的距离分级（danger/warning/normal）+ 操作建议
  3. 综合风险评分：基于最近支撑/压力距离 + 历史守住率的 0-100 分

核心思路：分析只告诉你「位置」，决策闭环告诉你「该不该动」。
"""

import datetime


def _hold_window(df, i, days=5):
    """返回 i 之后 days 天的 close 列表（不足则返回空）"""
    return [h["close"] for h in df[i + 1:i + 1 + days]]


def analyze_zone_history(history, levels, touch_tol=0.005, window=5):
    """
    对 compute_levels() 返回的每个支撑/压力位，回溯全历史统计守住率。

    参数:
        history:    [{date,open,close,high,low,volume}, ...]（越长越好，建议≥250日）
        levels:     compute_levels() 的返回 {supports:[...], resistances:[...]}
        touch_tol:  触及判定容差（价格进入价位 ±0.5% 视为触及）
        window:     触及后观察窗口（交易日）

    返回:
        {supports: [{price, held, broke, touch, held_rate, ...}], resistances: [...]}
    """
    n = len(history)
    out = {"supports": [], "resistances": []}
    if n < 60:
        return out

    for side in ("supports", "resistances"):
        for lvl in levels.get(side, []):
            price = lvl["price"]
            held = broke = 0
            # 只用「过去」的触及事件（当前价附近的判定放在后面单独做）
            for i in range(20, n - window - 1):
                h = history[i]
                if side == "supports":
                    # 支撑位：当日最低价触及（low <= price*1.005）
                    if h["low"] <= price * (1 + touch_tol) and h["close"] >= price * (1 - touch_tol):
                        future = _hold_window(history, i, window)
                        if len(future) < 3:
                            continue
                        # 守住：之后最低收盘未跌破支撑 2%
                        if min(future) >= price * 0.98:
                            held += 1
                        else:
                            broke += 1
                else:
                    # 压力位：当日最高价触及（high >= price*0.995）
                    if h["high"] >= price * (1 - touch_tol) and h["close"] <= price * (1 + touch_tol):
                        future = _hold_window(history, i, window)
                        if len(future) < 3:
                            continue
                        # 守住：之后最高收盘未突破压力 2%
                        if max(future) <= price * 1.02:
                            held += 1
                        else:
                            broke += 1
            total = held + broke
            out[side].append({
                "price": round(price, 2),
                "held": held,
                "broke": broke,
                "touch": total,
                "held_rate": round(held / total * 100, 1) if total else 0.0,
                "confidence": "高" if total >= 10 else ("中" if total >= 4 else "低"),
            })
    return out


def touch_alerts(current_price, levels, zone_history, threshold=5.0):
    """
    触及预警：当前价到每个支撑/压力位的距离分级。

    返回 [{price, side, kind, distance_pct, alert_level, alert_text, progress, advice, held_rate}]
    """
    alerts = []
    for side, label in (("supports", "支撑"), ("resistances", "压力")):
        hist_map = {h["price"]: h for h in zone_history.get(side, [])}
        for lvl in levels.get(side, []):
            price = lvl["price"]
            dist_pct = abs(lvl["distance_pct"])
            if dist_pct <= 1.0:
                level, text = "danger", "⚡ 即将触及"
            elif dist_pct <= 3.0:
                level, text = "warning", "🔶 正在接近"
            else:
                level, text = "normal", "🔹 距离较远"
            progress = max(0, min(100, int((1 - dist_pct / threshold) * 100)))
            h = hist_map.get(round(price, 2), {})
            hr = h.get("held_rate", 0.0)
            conf = h.get("confidence", "-")

            if side == "supports":
                if level == "danger":
                    advice = f"接近支撑{price}（历史守住率{hr}%/{conf}），关注反弹信号"
                elif level == "warning":
                    advice = f"向支撑{price}靠近（守住率{hr}%），准备观察入场时机"
                else:
                    advice = f"距支撑{price}较远（守住率{hr}%），等待回调"
            else:
                if level == "danger":
                    advice = f"接近压力{price}（历史守住率{hr}%），关注突破或减仓"
                elif level == "warning":
                    advice = f"向压力{price}靠近（守住率{hr}%），关注减仓时机"
                else:
                    advice = f"距压力{price}较远（守住率{hr}%），持仓观察"

            alerts.append({
                "price": round(price, 2),
                "side": side,
                "kind": label,
                "strength": lvl.get("strength", ""),
                "distance_pct": round(dist_pct, 2),
                "alert_level": level,
                "alert_text": text,
                "progress": progress,
                "advice": advice,
                "held_rate": hr,
                "confidence": conf,
            })
    alerts.sort(key=lambda a: a["distance_pct"])
    return alerts


def risk_score(alerts):
    """
    综合风险评分（0-100，越高越偏多/可关注）：
      - 最近支撑越近 → 加分（跌无可跌）
      - 最近压力越近 → 减分（上方有套牢盘）
      - 支撑历史守住率高 → 加分
      - 压力历史守住率高（从未突破）→ 减分
    """
    if not alerts:
        return {"score": 50, "level": "观望", "summary": "暂无数据"}
    supports = [a for a in alerts if a["side"] == "supports"]
    resistances = [a for a in alerts if a["side"] == "resistances"]
    min_sd = min((a["distance_pct"] for a in supports), default=5.0)
    min_rd = min((a["distance_pct"] for a in resistances), default=5.0)
    sr_hold = max((a["held_rate"] for a in supports), default=50.0)
    rr_hold = max((a["held_rate"] for a in resistances), default=50.0)

    score = 50.0
    score += max(0, (3.0 - min_sd)) * 8          # 支撑近 +24
    score -= max(0, (3.0 - min_rd)) * 8          # 压力近 -24
    score += (sr_hold - 50) * 0.25               # 支撑守住率高 +
    score -= (rr_hold - 50) * 0.25               # 压力难破 -
    score = max(0, min(100, round(score)))

    if score >= 65:
        level, summary = "可关注", f"距支撑{min_sd}%、压力{min_rd}%，支撑守住率{sr_hold}%"
    elif score >= 40:
        level, summary = "观望", f"距支撑{min_sd}%、压力{min_rd}%，攻守均衡"
    else:
        level, summary = "回避", f"上方压力{min_rd}%较近（守住率{rr_hold}%），追高风险大"
    return {"score": score, "level": level, "summary": summary}


def build_zone_context(history, levels):
    """
    一键生成决策闭环上下文（monitor/scanner/picks 直接调用）：
      返回 {zone_history, alerts, risk}
    """
    zh = analyze_zone_history(history, levels)
    current = history[-1]["close"]
    alerts = touch_alerts(current, levels, zh)
    risk = risk_score(alerts)
    return {"zone_history": zh, "alerts": alerts, "risk": risk}


if __name__ == "__main__":
    import json
    import sys
    import support_resistance
    hist = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "data/history/600036.json", "r", encoding="utf-8"))
    lv = support_resistance.compute_levels(hist)
    ctx = build_zone_context(hist, lv)
    print(f"当前价 {hist[-1]['close']}  |  风险评分 {ctx['risk']['score']}（{ctx['risk']['level']}）{ctx['risk']['summary']}")
    for a in ctx["alerts"]:
        print(f"  [{a['alert_text']}] {a['kind']}{a['price']} 距{a['distance_pct']}% 守住率{a['held_rate']}%({a['confidence']}) 强度{a['strength']} → {a['advice']}")
