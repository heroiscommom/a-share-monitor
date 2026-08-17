#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日选股清单（D策略落地版，2026-08-17 新增）—— 收盘后运行：

  1. 市场状态判断（沪深300 20日趋势）
  2. 上涨市 → 动量评分 Top10；下跌/震荡市 → 行业中性化均值回归 Top10
  3. 附带支撑位/止损位/入选理由（可直接当操作清单）
  4. 推送：Server酱微信一条 + 写入 digest.json（进收盘邮件）+ 落盘 data/picks.json

用法：
  python3 picks.py            # 正常跑（无 SERVERCHAN_KEY 则跳过微信）
  python3 picks.py --top 5    # 只出前5
"""

import os
import sys
import json
import time
import datetime

import quant
import support_resistance
import pool_backtest as pb
from portfolio_backtest import fetch_industry_map

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PICKS_PATH = os.path.join(BASE_DIR, "data", "picks.json")
DIGEST_PATH = os.path.join(BASE_DIR, "data", "digest.json")
POOL_SIZE = 300
MIN_HISTORY = 70


def send_wechat(title, desp):
    key = os.environ.get("SERVERCHAN_KEY")
    if not key:
        print("[wechat] 未配置 SERVERCHAN_KEY，跳过")
        return False
    import urllib.parse
    import urllib.request
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("code") == 0:
            print("[wechat] 微信已推送")
            return True
        print(f"[wechat] 推送失败: {resp}")
        return False
    except Exception as e:
        print(f"[wechat] 推送异常: {e}")
        return False


def main():
    top_n = 10
    if "--top" in sys.argv:
        try:
            top_n = int(sys.argv[sys.argv.index("--top") + 1])
        except (ValueError, IndexError):
            pass

    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")

    pool = pb.load_json(pb.POOL_LIST_PATH, None)
    if not pool or len(pool) < POOL_SIZE:
        pool = pb.fetch_pool_list(POOL_SIZE)
        pb.save_json(pb.POOL_LIST_PATH, pool)
    print(f"[pool] 股票池 {len(pool)} 只")

    # 行业分类（带缓存）
    print("[industry] 行业分类 ...")
    industry_map = fetch_industry_map(pool)

    # 市场状态
    idx = pb.fetch_index(250)
    regime = quant.market_regime(idx.get("closes") or [])
    print(f"[regime] {regime['desc']}")

    # 逐个计算评分 + 支撑压力
    cross = []
    for i, s in enumerate(pool):
        hist = pb.load_json(pb.history_path(s["code"]), None)
        if hist is None or len(hist) < pb.HISTORY_DAYS - 50:
            try:
                hist = pb.fetch_history(s["code"], s["market"])
                if hist:
                    pb.save_json(pb.history_path(s["code"]), hist)
            except Exception:
                hist = []
            time.sleep(0.05)
        if not hist or len(hist) < MIN_HISTORY:
            continue
        name = s["name"]
        if "ST" in name.upper():
            continue
        try:
            _, fac = quant.compute_factors(hist)
            mr = quant.compute_score(fac)
            _, mfac = quant.momentum_factors(hist)
            ms = quant.momentum_score(mfac)
        except Exception:
            continue
        sr = support_resistance.compute_levels(hist)
        price = hist[-1]["close"]
        support = sr["supports"][0] if sr.get("supports") else None
        resist = sr["resistances"][0] if sr.get("resistances") else None
        ind = industry_map.get(s["code"], "其他")
        # 一字涨停买不进标记
        prev = hist[-2]["close"] if len(hist) > 1 else price
        limit_up = price >= prev * 1.095 and price == hist[-1]["high"]
        cross.append({
            "code": s["code"], "name": name, "industry": ind,
            "mr": mr, "ms": ms, "price": round(price, 2),
            "support": round(support["price"], 2) if support else None,
            "support_strength": support["strength"] if support else None,
            "resistance": round(resist["price"], 2) if resist else None,
            "limit_up": bool(limit_up),
        })
        if (i + 1) % 100 == 0:
            print(f"  进度 {i + 1}/{len(pool)}")

    if len(cross) < top_n:
        print("候选不足")
        return

    # 行业均值（均值回归分，用于中性化）
    ind_means = {}
    for c in cross:
        ind_means.setdefault(c["industry"], []).append(c["mr"])
    ind_means = {k: sum(v) / len(v) for k, v in ind_means.items()}
    for c in cross:
        c["mr_neutral"] = round(c["mr"] - ind_means.get(c["industry"], 0), 1)

    # D策略选股
    if regime["regime"] == "上涨":
        ranked = sorted(cross, key=lambda x: x["ms"], reverse=True)
        strategy = "动量Top（上涨市，动量有效）"
    else:
        ranked = sorted(cross, key=lambda x: x["mr_neutral"], reverse=True)
        strategy = "行业中性化均值回归Top（下跌/震荡市）"

    top = ranked[:top_n]

    # 输出行
    lines = [f"📋 选股清单（{today}）", f"市场：{regime['desc']}", f"策略：{strategy}", ""]
    for i, c in enumerate(top, 1):
        if regime["regime"] == "上涨":
            why = f"动量{ c['ms']:.0f}分"
        else:
            why = f"中性化{c['mr_neutral']:.0f}分(超跌{c['mr']:.0f})"
        dist = f"距支撑{(c['price'] / c['support'] - 1) * 100:.1f}%" if c.get("support") else ""
        stop = f"止损{c['support']}" if c.get("support") else f"止损{round(c['price'] * 0.95, 2)}"
        flag = " ⚠️涨停" if c.get("limit_up") else ""
        lines.append(
            f"{i}. {c['name']}({c['code']}) {why} 现价{c['price']}{flag}\n"
            f"   支撑{c['support']}({c['support_strength']}) {dist} | {stop}"
        )
    body = "\n".join(lines)
    print("\n" + body)

    # 微信推送
    send_wechat(f"【选股清单】{today} {regime['regime']}市 Top{top_n}", body)

    # 写入 digest.json（收盘邮件汇总）
    digest = pb.load_json(DIGEST_PATH, {"items": []})
    digest["picks"] = {
        "date": today, "regime": regime["regime"], "strategy": strategy,
        "top": top,
    }
    pb.save_json(DIGEST_PATH, digest)
    print("[digest] 选股清单已写入日报")

    # 落盘（看板展示）
    pb.save_json(PICKS_PATH, {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "regime": regime, "strategy": strategy,
        "candidates": top,
    })
    print(f"[saved] {PICKS_PATH}")


if __name__ == "__main__":
    main()
