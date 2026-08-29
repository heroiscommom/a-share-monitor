#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开盘前早报（2026-08-27 新增）
================================================
每天 9:00（北京时间）推送一条微信：市场状态 + 情绪仓位约束 + 持仓狙击点位 + 今日关注。

数据来源（仓库内 data/，全部由 monitor 收盘后生成）：
  - quant.json            市场状态（沪深300 20日趋势）
  - dragon_head.json      情绪状态机（state/仓位建议/炸板率）+ 断板低吸
  - picks.json            D策略今日选股清单
  - snapshot.json         自选股行情
  - trades/config         真实持仓（load_portfolio 推导）

用法：
  python3 morning_report.py            # 生成并微信推送（无 SERVERCHAN_KEY 则只打印）
  python3 morning_report.py --dry-run  # 只打印
"""

import os
import sys
import json
import datetime
import urllib.request
import urllib.parse

from common import load_json
import datafeed
from notify import send_wechat

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

EMO_POS_CAP = {"冰点": 20, "回暖": 50, "活跃": 70, "高潮": 50}


def fetch_quote(code, market):
    """腾讯实时行情（持仓不在自选时用）"""
    return datafeed.fetch_quote(code, market)


def market_line():
    q = load_json(os.path.join(DATA_DIR, "quant.json"), {})
    regime = q.get("market_regime") or {}
    return regime.get("desc", "指数数据不可用")


def sentiment_lines():
    """情绪状态机 + 仓位约束（含偏差）"""
    d = load_json(os.path.join(DATA_DIR, "dragon_head.json"), {})
    s = d.get("sentiment") or {}
    lines = []
    if s:
        t = s.get("today") or {}
        sm = s.get("state_machine") or {}
        zbc = sm.get("zbc_rate")
        zbc_txt = f"炸板率{round(zbc * 100)}%" if zbc is not None else ""
        lines.append(f"{t.get('state', '-')}（昨涨停{t.get('zt_count', '-')}/最高{t.get('max_lbc', '-')}板 {zbc_txt}，{sm.get('direction', '')}）")
        if sm.get("position_advice"):
            lines.append(f"{sm['position_advice']}")
    return lines


def position_lines():
    """真实持仓建议（load_portfolio 从 trades 推导）+ 狙击点位"""
    import portfolio as pf
    portfolio, capital = pf.load_portfolio()
    if not portfolio:
        return ["暂无持仓（去交易页录入）"], None
    snap = load_json(os.path.join(DATA_DIR, "snapshot.json"), {})
    qm = {q["code"]: q for q in (snap.get("quotes") or [])}
    lines = []
    mv = 0
    for p in portfolio:
        hist = load_json(os.path.join(DATA_DIR, "history", f"{p['code']}.json"), [])
        q = qm.get(p["code"]) or fetch_quote(p["code"], p["market"]) or {}
        a = pf.advice_one(p, q, hist)
        sniper = a.get("sniper") or {}
        pnl = a.get("pnl_pct")
        pnl_txt = f"{pnl:+.1f}%" if pnl is not None else ""
        parts = [f"• {a['name']}({a['code']}) {a.get('price') or '-'}（{pnl_txt}）→ 【{a['advice']}】"]
        extras = []
        if sniper.get("ideal_buy"):
            extras.append(f"理想买{sniper['ideal_buy']}")
        if sniper.get("stop_loss"):
            extras.append(f"止损{sniper['stop_loss']}")
        if sniper.get("take_profit"):
            extras.append(f"止盈{sniper['take_profit']}")
        if extras:
            parts.append(" ".join(extras))
        lines.append(" ".join(parts))
        mv += (a.get("market_value") or 0)
    cash = capital.get("cash") or 0
    total = cash + mv
    pos_ratio = (mv / total * 100) if total > 0 else 0
    return lines, round(pos_ratio, 1)


def focus_lines():
    """今日关注：D策略选股 top3 + 断板低吸 top2"""
    lines = []
    picks = load_json(os.path.join(DATA_DIR, "picks.json"), {})
    for c in (picks.get("candidates") or [])[:3]:
        lines.append(f"• {c['name']}({c['code']}) {c.get('industry', '')} 现价{c.get('price', '-')} 支撑{c.get('support', '-')}")
    d = load_json(os.path.join(DATA_DIR, "dragon_head.json"), {})
    for c in (d.get("break_low") or [])[:2]:
        held = f"守{c['support_held']}%" if c.get("support_held") is not None else ""
        lines.append(f"• 断板低吸 {c['name']}({c['code']}) 昨{c.get('prev_lbc')}板 支撑{c.get('support', '-')}{held}")
    if not lines:
        lines.append("• 今日暂无重点（收盘后自动生成）")
    return lines


def build():
    date_str = datetime.date.today().strftime("%m-%d")
    lines = []
    lines.append("=" * 32)
    lines.append(f"🌅 A股早报 {date_str}")
    lines.append("=" * 32)
    lines.append(f"【市场】{market_line()}")
    lines.extend(f"【情绪】{x}" for x in sentiment_lines())
    pos_lines, pos_ratio = position_lines()
    lines.append("【持仓】")
    lines.extend(pos_lines)
    # 仓位约束偏差
    d = load_json(os.path.join(DATA_DIR, "dragon_head.json"), {})
    sm = (d.get("sentiment") or {}).get("state_machine") or {}
    if pos_ratio is not None and sm.get("state"):
        cap = EMO_POS_CAP.get(sm["state"])
        if cap:
            if pos_ratio > cap:
                lines.append(f"【仓位】当前{pos_ratio}% 超上限{cap}% → ⚠️ 减仓{pos_ratio - cap:.0f}%")
            elif pos_ratio < cap * 0.6:
                lines.append(f"【仓位】当前{pos_ratio}% 上限{cap}% → 可加仓{cap - pos_ratio:.0f}%")
            else:
                lines.append(f"【仓位】当前{pos_ratio}% 上限{cap}% → 结构合理")
    lines.append("【今日关注】")
    lines.extend(focus_lines())
    zbc = sm.get("zbc_rate")
    if zbc is not None and zbc >= 0.45:
        lines.append(f"【风险】昨日炸板率{round(zbc * 100)}%偏高，注意高位股退潮")
    lines.append("")
    lines.append("⚠️ 量化自动生成，仅供参考，不构成投资建议")
    return "\n".join(lines)


def main():
    dry = "--dry-run" in sys.argv
    text = build()
    print(text)
    if dry:
        print("\n[dry-run] 不发送")
        return
    send_wechat(f"🌅 A股早报 {datetime.date.today().strftime('%m-%d')}", text)


if __name__ == "__main__":
    main()
