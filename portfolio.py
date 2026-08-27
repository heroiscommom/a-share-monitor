#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓分析与建议模块（2026-08-26 新增，纯标准库）
================================================
基于现有量化体系（评分/支撑压力v2/决策闭环/情绪周期）对持仓逐只给出建议，
并输出整体仓位意见。供 auto_report.py 生成邮件周报/日报。

config.json 新增配置（示例）：
  "portfolio": [
    {"code": "600664", "market": "sh", "name": "哈药股份", "shares": 10000, "cost": 7.50},
    {"code": "001289", "market": "sz", "name": "龙源电力", "shares": 3000,  "cost": 18.20}
  ],
  "capital": {"total": 200000, "cash": 80000}   # total=总资金(含持仓市值+现金)

建议规则（保守版，仅供参考）：
  止损: 浮亏≥8% 且 现价跌破最近支撑 → 建议止损
  止盈: 浮盈≥20% 且 接近强压力(≤1.5%)或高位风险评分 → 建议分批止盈
  低吸: 超跌评分≥70 且 现价在支撑上方0~3% 且 支撑守住率≥60% 且 浮亏<10% → 建议低吸
  减仓: 浮盈≥10% 且 压力位守住率<50%（压力易破）→ 建议减仓
  持有: 其余情况
"""

import os
import json
import datetime

import quant
import support_resistance
import zone_history

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def load_portfolio():
    """
    返回 (portfolio列表, capital字典)。
    优先从 trades.json 推导真实持仓（页面/Issue 机器人录入后自动同步，已清仓的剔除）；
    无流水时才退回 config.json 的初始种子。
    """
    cfg = load_json(CONFIG_PATH, {})
    capital = cfg.get("capital", {})
    try:
        import trade as tr
        trades = tr.load_trades().get("trades", [])
        if trades:
            pos = tr.positions_from_trades(trades)
            portfolio = []
            for code, p in pos.items():
                if (p.get("shares") or 0) <= 0:
                    continue  # 已清仓不参与建议
                market = "sh" if code.startswith("6") else ("bj" if code.startswith(("4", "8")) else "sz")
                portfolio.append({
                    "code": code, "market": market,
                    "name": p.get("name") or code,
                    "shares": p["shares"],
                    "cost": p.get("avg_cost") or 0,
                })
            if portfolio:
                portfolio.sort(key=lambda x: x["shares"] * x["cost"], reverse=True)
                # 现金推导：初始现金(建仓日快照) + 建仓日之后的资金流（买入扣款/卖出回款/盈亏记录）
                try:
                    seed_date = min(t["date"] for t in trades)
                    flow = 0.0
                    for t in trades:
                        if t["date"] <= seed_date:
                            continue
                        if t["side"] == "buy":
                            flow -= t["shares"] * t["price"] + t.get("fee", 0)
                        elif t["side"] == "sell":
                            flow += t["shares"] * t["price"] - t.get("fee", 0)
                        elif t["side"] == "pnl":
                            flow += t["shares"]
                    cash = round((capital.get("cash") or 0) + flow, 2)
                    capital = {"cash": cash, "total": 0}   # total=0 → overall_advice 用 市值+现金 兜底
                except Exception as e:
                    print(f"[warn] 现金推导失败: {e}")
                return portfolio, capital
    except Exception as e:
        print(f"[warn] trades 推导持仓失败，退回 config: {e}")
    return cfg.get("portfolio", []), capital


def advice_one(position, quote, history):
    """
    单只持仓建议。
    position: {code, market, name, shares, cost}
    quote:    最新行情 {price, change_pct} 或 None
    history:  日K列表
    返回建议 dict（含 advice/reason/pnl 等）
    """
    code = position["code"]
    name = position.get("name", code)
    cost = position.get("cost") or 0
    shares = position.get("shares") or 0
    price = (quote or {}).get("price")
    out = {
        "code": code, "name": name, "shares": shares, "cost": cost,
        "price": price, "advice": "持有", "reason": "暂无数据", "level": "hold",
    }
    if not price or not cost or not history or len(history) < 60:
        return out

    pnl_pct = (price / cost - 1) * 100
    out["pnl_pct"] = round(pnl_pct, 2)
    out["market_value"] = round(price * shares)

    # 量化评分 + 支撑压力 + 决策闭环
    try:
        _, fac = quant.compute_factors(history)
        score = quant.compute_score(fac)
        signal, sig_key = quant.signal_from_score(score)
        out["score"] = score
        out["signal"] = signal
    except Exception:
        score, signal, sig_key = None, "?", "neutral"
    try:
        lv = support_resistance.compute_levels(history)
        ctx = zone_history.build_zone_context(history, lv)
        sup = lv["supports"][0] if lv["supports"] else None
        res = lv["resistances"][0] if lv["resistances"] else None
        out["support"] = sup["price"] if sup else None
        out["resistance"] = res["price"] if res else None
        out["risk"] = ctx["risk"]
        # 守住率：最近支撑/压力
        for a in ctx["alerts"]:
            if a["side"] == "supports" and out.get("support_held") is None:
                out["support_held"] = a["held_rate"]
            if a["side"] == "resistances" and out.get("resistance_held") is None:
                out["resistance_held"] = a["held_rate"]
    except Exception:
        pass

    # ── 规则引擎（按优先级） ──
    risk_score = (out.get("risk") or {}).get("score", 50)
    sup_dist = None
    if out.get("support"):
        sup_dist = (price - out["support"]) / price * 100
    res_dist = None
    if out.get("resistance"):
        res_dist = (out["resistance"] - price) / price * 100

    # ── 狙击点位（P1c，学 DSA 的 ideal_buy/secondary_buy/stop_loss/take_profit） ──
    sniper = {}
    try:
        supports = lv["supports"]
        resistances = lv["resistances"]
        # 理想买点：守住率≥60% 的最近支撑；否则最近支撑
        good_sups = [s for s in supports if s.get("held_rate") is not None and s["held_rate"] >= 60]
        pick_sups = good_sups if good_sups else supports
        if pick_sups:
            ideal = pick_sups[0]
            sniper["ideal_buy"] = round(ideal["price"], 2)
            sniper["stop_loss"] = round(ideal["price"] * 0.98, 2)
            if len(pick_sups) > 1:
                sniper["secondary_buy"] = round(pick_sups[1]["price"], 2)
        if resistances:
            sniper["take_profit"] = round(resistances[0]["price"], 2)
    except Exception:
        pass
    out["sniper"] = sniper

    # 1. 止损：浮亏≥8% 且 跌破支撑（或价格在支撑下方）
    if pnl_pct <= -8 and (sup_dist is not None and sup_dist < 0):
        out["advice"] = "止损"
        out["level"] = "sell"
        out["reason"] = f"浮亏{pnl_pct:.1f}% 已跌破支撑{out['support']}（守住率{out.get('support_held', '-')}%），破位风险大"
    # 2. 止盈：浮盈≥20% 且 接近压力或高位风险
    elif pnl_pct >= 20 and (res_dist is not None and res_dist <= 1.5) or (pnl_pct >= 20 and risk_score < 40):
        out["advice"] = "分批止盈"
        out["level"] = "trim"
        out["reason"] = f"浮盈{pnl_pct:.1f}% 接近压力{out.get('resistance', '-')}（{res_dist and round(res_dist, 1)}%），压力守住率{out.get('resistance_held', '-')}%"
    # 3. 低吸：超跌 + 接近支撑 + 守住率高 + 浮亏可控
    elif (score is not None and score >= 70 and sup_dist is not None and 0 <= sup_dist <= 3
          and (out.get("support_held") or 0) >= 60 and pnl_pct > -10):
        out["advice"] = "低吸加仓"
        out["level"] = "buy"
        out["reason"] = f"超跌评分{score:.0f} 距支撑{out['support']}仅{sup_dist:.1f}%（守住率{out.get('support_held')}%），风险评分{risk_score}"
    # 4. 减仓：浮盈≥10% 且 压力易破（守住率<50%）
    elif pnl_pct >= 10 and (out.get("resistance_held") or 50) < 50:
        out["advice"] = "减仓"
        out["level"] = "trim"
        out["reason"] = f"浮盈{pnl_pct:.1f}% 上方压力{out.get('resistance', '-')}历史守住率仅{out.get('resistance_held')}%（易突破），先落袋部分"
    # 5. 持有（附观察提示）
    else:
        hints = []
        if risk_score >= 65:
            hints.append(f"风险评分{risk_score}偏多")
        if sup_dist is not None and sup_dist <= 1.0:
            hints.append(f"贴近支撑{out['support']}（守住率{out.get('support_held', '-')}%）")
        if res_dist is not None and res_dist <= 2.0:
            hints.append(f"上方压力{out['resistance']}约{res_dist:.1f}%")
        out["advice"] = "持有"
        out["level"] = "hold"
        out["reason"] = "；".join(hints) if hints else f"评分{score} 信号「{signal}」 风险评分{risk_score}"
    return out


def overall_advice(advices, capital, regime_desc=""):
    """
    整体仓位建议。
    capital: {total, cash}
    """
    total = capital.get("total") or 0
    cash = capital.get("cash") or 0
    mv = sum(a.get("market_value") or 0 for a in advices)
    if total <= 0:
        total = mv + cash
    pos_ratio = (mv / total * 100) if total > 0 else 0
    cash_ratio = (cash / total * 100) if total > 0 else 0

    sells = [a for a in advices if a["level"] == "sell"]
    buys = [a for a in advices if a["level"] == "buy"]
    trims = [a for a in advices if a["level"] == "trim"]
    holds = [a for a in advices if a["level"] == "hold"]

    # 仓位建议：震荡/下跌市建议 30-50% 现金；上涨市可 20-30%
    if cash_ratio >= 50:
        pos_advice = f"现金充足（{cash_ratio:.0f}%），等待确定性信号再出手，不建议为了满仓而满仓"
    elif cash_ratio <= 20:
        pos_advice = f"仓位偏高（持仓{pos_ratio:.0f}%），注意留足现金应对回撤"
    else:
        pos_advice = f"仓位{pos_ratio:.0f}%/现金{cash_ratio:.0f}%，结构均衡"

    summary = []
    if buys:
        summary.append(f"🔺 可低吸: {'、'.join(a['name'] for a in buys)}")
    if trims:
        summary.append(f"🔻 可止盈/减仓: {'、'.join(a['name'] for a in trims)}")
    if sells:
        summary.append(f"⛔ 建议止损: {'、'.join(a['name'] for a in sells)}")
    if not summary:
        summary.append("全部持有观察，无加减仓信号")

    return {
        "total": round(total), "market_value": round(mv), "cash": round(cash),
        "pos_ratio": round(pos_ratio, 1), "cash_ratio": round(cash_ratio, 1),
        "pos_advice": pos_advice,
        "summary": "；".join(summary),
        "counts": {"buy": len(buys), "trim": len(trims), "sell": len(sells), "hold": len(holds)},
        "regime": regime_desc,
    }


def format_report_text(advices, overall, extra_sections=None):
    """生成邮件纯文本报告"""
    lines = []
    lines.append("=" * 40)
    lines.append("📊 A股持仓量化建议")
    lines.append(f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if overall.get("regime"):
        lines.append(f"市场状态: {overall['regime']}")
    lines.append("=" * 40)

    lines.append(f"\n【整体仓位】总资金 {overall['total']:,} ｜ 持仓 {overall['market_value']:,} ({overall['pos_ratio']}%) ｜ 现金 {overall['cash']:,} ({overall['cash_ratio']}%)")
    lines.append(f"建议: {overall['pos_advice']}")
    lines.append(f"操作: {overall['summary']}")

    lines.append("\n【逐只持仓】")
    for a in advices:
        pnl = a.get("pnl_pct")
        pnl_txt = f"{pnl:+.1f}%" if pnl is not None else "-"
        sp = a.get("sniper") or {}
        sniper_txt = ""
        if sp:
            parts = []
            if sp.get("ideal_buy"):
                parts.append(f"理想买{sp['ideal_buy']}")
            if sp.get("secondary_buy"):
                parts.append(f"次买{sp['secondary_buy']}")
            if sp.get("stop_loss"):
                parts.append(f"止损{sp['stop_loss']}")
            if sp.get("take_profit"):
                parts.append(f"止盈{sp['take_profit']}")
            sniper_txt = " ｜ 狙击: " + " / ".join(parts)
        lines.append(
            f"• {a['name']}({a['code']}) 现价{a.get('price')} 成本{a.get('cost')} "
            f"盈亏{pnl_txt} 评分{a.get('score', '-')} {a.get('signal', '')}"
            f"\n   支撑{a.get('support', '-')}(守{a.get('support_held', '-')}%) 压力{a.get('resistance', '-')} "
            f"风险{a.get('risk', {}).get('score', '-')}({a.get('risk', {}).get('level', '')})"
            f"{sniper_txt}"
            f"\n   → 【{a['advice']}】{a['reason']}"
        )

    if extra_sections:
        for title, body in extra_sections:
            lines.append(f"\n【{title}】\n{body}")

    lines.append("\n" + "=" * 40)
    lines.append("⚠️ 本报告由量化规则自动生成，仅供研究参考，不构成投资建议。")
    lines.append("规则: 止损=浮亏≥8%且破支撑；止盈=浮盈≥20%且近压力/高位；低吸=超跌评分≥70+近支撑+守住率≥60%")
    return "\n".join(lines)


if __name__ == "__main__":
    # 演示：用 watchlist 历史数据模拟持仓
    import sys
    sys.path.insert(0, BASE_DIR)
    portfolio, capital = load_portfolio()
    if not portfolio:
        print("config.json 未配置 portfolio，使用 watchlist 演示（前5只，成本=现价*0.95）")
        cfg = load_json(CONFIG_PATH, {})
        for w in cfg.get("watchlist", [])[:5]:
            portfolio.append({"code": w["code"], "market": w["market"], "name": w["name"],
                              "shares": 1000, "cost": 0})
        if not capital:
            capital = {"total": 200000, "cash": 60000}

    advices = []
    for p in portfolio:
        hist = load_json(os.path.join(DATA_DIR, "history", f"{p['code']}.json"), [])
        quote = {"price": hist[-1]["close"]} if hist else None
        if p.get("cost", 0) <= 0 and quote:
            p["cost"] = round(quote["price"] * 0.95, 2)  # 演示成本
        a = advice_one(p, quote, hist)
        advices.append(a)
        print(f"{a['name']}({a['code']}) 现价{a.get('price')} 成本{a.get('cost')} "
              f"盈亏{a.get('pnl_pct', '-')}% → 【{a['advice']}】{a['reason']}")

    ov = overall_advice(advices, capital)
    print(f"\n仓位: {ov['pos_ratio']}% 持仓 | {ov['cash_ratio']}% 现金 → {ov['pos_advice']}")
    print(f"操作: {ov['summary']}")
