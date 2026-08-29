#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动报告：回测摘要 + 持仓建议 + 市场/情绪/龙头 → 邮件推送（2026-08-26 新增）
=====================================================================
用法:
  python3 auto_report.py            # 生成报告并发送邮件（无 SMTP 凭据则只打印）
  python3 auto_report.py --dry-run  # 只打印不发送

数据来源（优先仓库内 data/，缺失时现场拉取）：
  - data/quant.json       市场状态/自选股评分
  - data/backtest.json    股票池回测结论（周级更新）
  - data/picks.json       今日选股清单（D策略）
  - data/dragon_head.json 龙头梯队 + 情绪周期
  - data/snapshot.json    自选股行情（持仓不在自选时现场拉）
  - data/history/*.json   日K（持仓不在自选时现场拉 250 天）

GitHub Actions 定时：auto-report.yml 每天 15:35（收盘后）自动执行。
"""

import os
import sys
import json
import time
import datetime

from common import http_get, load_json
import datafeed
from notify import send_email

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

import portfolio as pf


def fetch_quote(code, market):
    """腾讯实时行情（持仓不在自选时用）"""
    return datafeed.fetch_quote(code, market)


def fetch_history(code, market, days=250):
    """腾讯前复权日K（持仓不在自选时用）"""
    try:
        return datafeed.fetch_history(code, market, days)
    except Exception:
        return []


EMO_POS_CAP = {"冰点": 20, "回暖": 50, "活跃": 70, "高潮": 50}


def market_section(pos_ratio=None):
    """市场状态 + 情绪周期 + 龙头摘要 + 仓位约束偏差（P2+）"""
    lines = []
    q = load_json(os.path.join(DATA_DIR, "quant.json"), {})
    regime = q.get("market_regime") or {}
    if regime.get("desc"):
        lines.append(f"市场状态: {regime['desc']}")

    d = load_json(os.path.join(DATA_DIR, "dragon_head.json"), {})
    s = d.get("sentiment") or {}
    if s:
        t = s.get("today") or {}
        sm = s.get("state_machine") or {}
        zbc = sm.get("zbc_rate")
        zbc_txt = f"炸板率{round(zbc * 100)}%" if zbc is not None else ""
        lines.append(f"情绪周期: {t.get('state', '-')}（涨停{t.get('zt_count', '-')}只/最高{t.get('max_lbc', '-')}板 {zbc_txt}，{sm.get('direction', '')}）｜ {s.get('trend', {}).get('desc', '')}")
        if sm.get("position_advice"):
            lines.append(f"仓位建议: {sm['position_advice']}")
        # 仓位约束偏差：情绪状态机上限 vs 实际持仓占比
        if pos_ratio is not None and sm.get("state"):
            cap = EMO_POS_CAP.get(sm["state"])
            if cap:
                if pos_ratio > cap:
                    lines.append(f"仓位约束: {sm['state']}市上限{cap}%，当前持仓{pos_ratio}% → ⚠️ 超配{pos_ratio - cap:.0f}%，建议减仓至{cap}%以内")
                elif pos_ratio < cap * 0.6:
                    lines.append(f"仓位约束: {sm['state']}市上限{cap}%，当前持仓{pos_ratio}% → 低配，可加仓空间{cap - pos_ratio:.0f}%（≤{cap}%）")
                else:
                    lines.append(f"仓位约束: {sm['state']}市上限{cap}%，当前持仓{pos_ratio}% → 结构合理")
    tiers = d.get("tiers") or {}
    if tiers.get("S") or tiers.get("A"):
        sa = (tiers.get("S") or [])[:2] + (tiers.get("A") or [])[:3]
        names = "、".join(f"{it['name']}{it['lbc']}板" for it in sa)
        lines.append(f"龙头观察: {names}")
    return "\n".join(lines) if lines else "暂无市场数据"


def backtest_section():
    """回测摘要（读 data/backtest.json 股票池回测结论）"""
    lines = []
    bt = load_json(os.path.join(DATA_DIR, "backtest.json"), {})
    if bt.get("conclusion"):
        lines.append(bt["conclusion"])
    picks = load_json(os.path.join(DATA_DIR, "picks.json"), {})
    stocks = picks.get("stocks") or []
    if stocks:
        top = stocks[:5]
        lines.append("今日选股清单(D策略): " + "、".join(f"{s.get('name', s.get('code'))}({s.get('score', '-')}分)" for s in top))
    return "\n".join(lines) if lines else "回测数据待更新（周一自动跑）"


def portfolio_section():
    """持仓建议（config portfolio，无则演示 watchlist）"""
    portfolio, capital = pf.load_portfolio()
    demo = False
    if not portfolio:
        demo = True
        cfg = pf.load_json(pf.CONFIG_PATH, {})
        for w in cfg.get("watchlist", [])[:5]:
            portfolio.append({"code": w["code"], "market": w["market"], "name": w["name"],
                              "shares": 1000, "cost": 0})
        if not capital:
            capital = {"total": 200000, "cash": 60000}

    snap = load_json(os.path.join(DATA_DIR, "snapshot.json"), {})
    quote_map = {q["code"]: q for q in (snap.get("quotes") or [])}

    advices = []
    for p in portfolio:
        code, market = p["code"], p["market"]
        hist = load_json(os.path.join(DATA_DIR, "history", f"{code}.json"), [])
        quote = quote_map.get(code)
        if not hist or not quote:
            # 现场拉取
            if not quote:
                quote = fetch_quote(code, market)
            if not hist:
                hist = fetch_history(code, market)
        if p.get("cost", 0) <= 0 and quote and quote.get("price"):
            p["cost"] = round(quote["price"] * 0.95, 2)  # 演示成本
        a = pf.advice_one(p, quote, hist)
        if a.get("price") is None:
            a["advice"], a["reason"] = "数据缺失", f"{code} 行情获取失败"
        advices.append(a)
        time.sleep(0.1)

    regime_desc = (load_json(os.path.join(DATA_DIR, "quant.json"), {}).get("market_regime") or {}).get("desc", "")
    overall = pf.overall_advice(advices, capital, regime_desc)
    return advices, overall, demo


def build_report():
    advices, overall, demo = portfolio_section()
    # 打脸复盘（P1-5）：建议落库 + 到期回填 + 昨日对照（演示持仓不记录）
    check_lines = []
    stat_line = ""
    if not demo:
        try:
            import advice_history
            advice_history.record_daily_advice(advices)
            advice_history.backfill_evaluations()
            check_lines = advice_history.today_check(advices)
            stat_line = advice_history.stats_text()
        except Exception as e:
            print(f"[warn] 打脸复盘失败: {e}")
    # 信号落库（P1b：真实建议→10日后自动回填判定）
    if not demo:
        try:
            import signal_history
            signal_history.record_signals(advices)
        except Exception as e:
            print(f"[warn] 信号落库失败: {e}")
    extra = [("市场与情绪", market_section(overall.get("pos_ratio"))), ("回测摘要", backtest_section())]
    if check_lines:
        extra.append(("昨日建议对照（初判）", "\n".join(check_lines)))
    if stat_line and not stat_line.startswith("建议命中统计：数据积累中"):
        extra.append(("建议命中复盘", stat_line))
    body = pf.format_report_text(advices, overall, extra)
    # AI 决策报告（有 DEEPSEEK_API_KEY 时追加，失败不影响主报告）
    try:
        import ai_report
        payload = ai_report.build_data_payload()
        ai_text = ai_report.call_deepseek(payload)
        if ai_text:
            body += "\n\n" + "=" * 40 + "\n🤖 AI 决策报告\n" + "=" * 40 + "\n" + ai_text
    except Exception as e:
        print(f"[warn] AI 报告失败: {e}")
    if demo:
        body += "\n\n⚠️ 当前为演示持仓（watchlist 前5只，成本=现价×0.95）。请在 config.json 配置真实 portfolio 与 capital。"
    return body, overall


def main():
    dry = "--dry-run" in sys.argv
    body, overall = build_report()
    print(body)
    if dry:
        print("\n[dry-run] 不发送")
        return
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    marker = os.path.join(DATA_DIR, f"report_sent_{date_str}.txt")
    # 当日已发过则跳过（GitHub 定时 + 本机兜底双触发时防重复邮件）
    if os.path.exists(marker):
        print(f"[skip] 今日({date_str})报告已发送过（{marker} 存在），跳过")
        return
    subject = f"【A股量化报告】{date_str} 仓位{overall['pos_ratio']}%｜{overall['summary'][:40]}"
    ok = send_email(subject, body)
    if ok:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now().isoformat())
        print(f"[sent] 邮件已发送，写入去重标记 {marker}")
    else:
        print("[warn] 邮件发送失败/未配置，不写标记（下次重试）")


if __name__ == "__main__":
    main()
