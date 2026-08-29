#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日持仓建议记录 + 回填判定 + 打脸复盘（P1-5，2026-08-27 新增）
================================================
- record_daily_advice(advices)   : auto_report 每天把规则引擎建议落库
- backfill_evaluations()         : 超过 EVAL_DAYS 交易日（≈14自然日）的建议回填判定
- today_check(advices)           : 昨日建议 vs 今日行情的当日快速对照（日报/AI 用）
- stats_text()                   : 累计命中率统计（周报用）

判定口径：
  看多（持有/低吸/加仓）：跌破止损位 → 打脸；较建议日收盘涨≥3% → 应验；跌≥3% → 打脸；其余持平
  看空（减仓/止盈/止损）：较建议日收盘跌≥3% → 应验（躲过下跌）；涨≥3% → 打脸；其余持平
"""

import os
import json
import datetime

from common import load_json, save_json, market_of
import datafeed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
ADVICE_PATH = os.path.join(DATA_DIR, "advice_history.json")

EVAL_CALENDAR_DAYS = 14   # ≈10个交易日
HIT_PCT = 3.0             # 涨跌≥3% 才算应验/打脸


def fetch_quote(code, market):
    """腾讯实时行情（快，单只）"""
    return datafeed.fetch_quote(code, market)


def _item_from_advice(a):
    """从 portfolio.advice_one 的输出提取落库字段"""
    sniper = a.get("sniper") or {}
    return {
        "code": a.get("code"),
        "name": a.get("name"),
        "advice": a.get("advice", "持有"),
        "level": a.get("level", "hold"),
        "reason": (a.get("reason") or "")[:120],
        "price": a.get("price"),
        "stop": sniper.get("stop_loss"),
        "target": sniper.get("take_profit"),
        "support": a.get("support"),
    }


def record_daily_advice(advices):
    """把今天的建议落库（同一天只记一次）"""
    today = datetime.date.today().strftime("%Y-%m-%d")
    data = load_json(ADVICE_PATH, {"entries": []})
    if any(e.get("date") == today for e in data.get("entries", [])):
        return 0
    items = [_item_from_advice(a) for a in advices if a.get("code")]
    data.setdefault("entries", []).append({"date": today, "items": items, "verdicts": {}})
    data["entries"] = data["entries"][-90:]   # 只留90天
    save_json(ADVICE_PATH, data)
    return len(items)


def _verdict(item, cur_price):
    """单条建议判定。返回 (result, detail) 或 None"""
    if not cur_price:
        return None
    entry = item.get("price")
    if not entry:
        return None
    stop = item.get("stop")
    if stop and cur_price <= stop:
        return ("打脸", f"跌破止损{stop}，现价{cur_price}")
    long_bias = item.get("level") in ("hold", "buy")
    chg = (cur_price / entry - 1) * 100
    if long_bias:
        if chg >= HIT_PCT:
            return ("应验", f"上涨{chg:+.1f}%")
        if chg <= -HIT_PCT:
            return ("打脸", f"下跌{chg:.1f}%")
        return ("持平", f"{chg:+.1f}%")
    else:
        if chg <= -HIT_PCT:
            return ("应验", f"下跌{chg:.1f}%，回避正确")
        if chg >= HIT_PCT:
            return ("打脸", f"上涨{chg:+.1f}%")
        return ("持平", f"{chg:+.1f}%")


def backfill_evaluations():
    """对超过评估窗口的建议回填判定（每只一次）"""
    data = load_json(ADVICE_PATH, {"entries": []})
    entries = data.get("entries", [])
    today = datetime.date.today()
    changed = 0
    for e in entries:
        try:
            e_date = datetime.datetime.strptime(e["date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if (today - e_date).days < EVAL_CALENDAR_DAYS:
            continue
        verdicts = e.setdefault("verdicts", {})
        for it in e["items"]:
            code = it.get("code")
            if not code or code in verdicts:
                continue
            q = fetch_quote(code, market_of(code))
            if not q:
                continue
            v = _verdict(it, q["price"])
            if v:
                verdicts[code] = {"result": v[0], "detail": v[1], "eval_date": today.strftime("%Y-%m-%d")}
                changed += 1
    if changed:
        save_json(ADVICE_PATH, data)
    return changed


def _yesterday_entries(data, today):
    """昨天的建议条目（用于当日快速对照）"""
    entries = data.get("entries", [])
    if not entries:
        return []
    last = entries[-1]
    try:
        if (today - datetime.datetime.strptime(last["date"], "%Y-%m-%d").date()).days != 1:
            return []
    except (ValueError, TypeError):
        return []
    return last.get("items", [])


def today_check(advices):
    """昨日建议 vs 今日现价 的当日快速对照（初判，正式判定以回填为准）"""
    data = load_json(ADVICE_PATH, {"entries": []})
    today = datetime.date.today()
    y_items = _yesterday_entries(data, today)
    if not y_items:
        return []
    # 今日现价：优先用传入 advices（auto_report 已拉过），否则现场拉
    cur = {a.get("code"): a.get("price") for a in advices if a.get("price")}
    lines = []
    for it in y_items:
        code = it.get("code")
        price = cur.get(code) or (fetch_quote(code, market_of(code)) or {}).get("price")
        if not price:
            continue
        v = _verdict(it, price)
        if not v:
            continue
        emoji = {"应验": "✅", "打脸": "❌", "持平": "➖"}[v[0]]
        lines.append(f"{emoji} {it.get('name', code)}：昨日建议[{it.get('advice', '?')}]，今日{v[1]}")
    return lines


def stats_text(days=90):
    """累计命中率一句话（含最近打脸明细）"""
    data = load_json(ADVICE_PATH, {"entries": []})
    total = {"应验": 0, "打脸": 0, "持平": 0}
    facepalms = []
    for e in data.get("entries", []):
        for code, v in (e.get("verdicts") or {}).items():
            r = v.get("result")
            if r in total:
                total[r] += 1
                if r == "打脸":
                    name = next((it.get("name", code) for it in e.get("items", []) if it.get("code") == code), code)
                    facepalms.append(f"{e['date']} {name}：{v.get('detail', '')}")
    n = total["应验"] + total["打脸"]
    if n == 0:
        return "建议命中统计：数据积累中（14天后开始回填）"
    hit = total["应验"] / n * 100
    txt = f"建议命中率 {hit:.0f}%（应验{total['应验']} / 打脸{total['打脸']} / 持平{total['持平']}）"
    if facepalms:
        txt += "；最近打脸：" + "；".join(facepalms[-3:])
    return txt


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        # 自检：造几条假建议验证判定逻辑
        items = [
            {"code": "600001", "name": "A", "advice": "持有", "level": "hold", "price": 10.0, "stop": 9.0},
            {"code": "600002", "name": "B", "advice": "持有", "level": "hold", "price": 10.0, "stop": 9.5},
            {"code": "600003", "name": "C", "advice": "减仓", "level": "trim", "price": 10.0, "stop": None},
            {"code": "600004", "name": "D", "advice": "持有", "level": "hold", "price": 10.0, "stop": None},
        ]
        for it, px in zip(items, [10.5, 9.4, 9.6, 10.1]):
            print(it["name"], it["advice"], f"现价{px} →", _verdict(it, px))
    else:
        print("record:", record_daily_advice([]) if False else "用 auto_report 集成调用")
