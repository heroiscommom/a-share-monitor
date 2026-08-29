#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全A股扫描器 —— 扫沪深300+中证500（约800只），找出「超跌 + 接近支撑位」的候选股。

逻辑：量化评分 ≥ 70（超跌）且 价格在最近支撑位上方 0~3% 内 → 候选。
候选分 = 量化评分 + 接近支撑的加分（越贴近支撑分越高）。

数据源：股票池=新浪按市值排序；历史=腾讯前复权日K（缓存到 data/pool/）。
"""

import os
import json
import time
import datetime

import quant
import support_resistance

from common import http_get, load_json, save_json
import datafeed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POOL_DIR = os.path.join(BASE_DIR, "data", "pool")
SCANNER_POOL = os.path.join(BASE_DIR, "data", "scanner_pool.json")
SCANNER_OUT = os.path.join(BASE_DIR, "data", "scanner.json")

POOL_SIZE = 800
HISTORY_DAYS = 750      # 3年（2026-08-17 拉长历史）
MIN_HISTORY = 70
SCORE_THRESHOLD = 70     # 超跌评分阈值
SUPPORT_BAND = 3.0       # 价格距支撑位的最大距离%


def fetch_pool_list(size=POOL_SIZE):
    """从新浪按市值取前 N 只A股（沪深300+中证500近似）"""
    stocks = []
    page = 1
    while len(stocks) < size and page <= 10:
        url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"Market_Center.getHQNodeData?page={page}&num=100&sort=mktcap&asc=0&node=hs_a&symbol=&_s_r_a=page")
        try:
            data = json.loads(http_get(url))
        except Exception:
            break
        if not data:
            break
        for item in data:
            code = item.get("code", "")
            symbol = item.get("symbol", "")
            name = item.get("name", "")
            if not code:
                continue
            market = "sh" if symbol.startswith("sh") else "sz"
            stocks.append({"code": code, "market": market, "name": name})
            if len(stocks) >= size:
                break
        page += 1
    return stocks[:size]


def history_path(code):
    return os.path.join(POOL_DIR, f"{code}.json")


def fetch_history(code, market, days=HISTORY_DAYS):
    """腾讯前复权日K（默认 750 天，与 datafeed 共用实现）"""
    return datafeed.fetch_history(code, market, days)


def scan():
    pool = load_json(SCANNER_POOL, None)
    if not pool or len(pool) < POOL_SIZE:
        pool = fetch_pool_list(POOL_SIZE)
        save_json(SCANNER_POOL, pool)
    print(f"[scan] 股票池 {len(pool)} 只")

    # 8 线程并行：历史拉取/缓存 + 因子计算（IO 密集），800 只从 ~4 分钟降到 ~1 分钟
    import threading
    from concurrent.futures import ThreadPoolExecutor
    scanned_lock = threading.Lock()
    scanned = 0

    def one(s):
        nonlocal scanned
        hist = load_json(history_path(s["code"]), None)
        if hist is None:
            try:
                hist = fetch_history(s["code"], s["market"])
                if hist:
                    save_json(history_path(s["code"]), hist)
            except Exception:
                hist = []
            time.sleep(0.05)  # 礼貌限速（并发下分摊到各线程）
        if not hist or len(hist) < MIN_HISTORY:
            return None
        try:
            _, fac = quant.compute_factors(hist)
            score = quant.compute_score(fac)
            sr = support_resistance.compute_levels(hist)
        except Exception:
            return None
        with scanned_lock:
            scanned += 1
        if score < SCORE_THRESHOLD or not sr["supports"]:
            return None
        price = hist[-1]["close"]
        nearest = sr["supports"][0]
        dist = (price - nearest["price"]) / price * 100  # 价格距支撑位距离%
        if not (0 <= dist <= SUPPORT_BAND):
            return None
        bonus = (SUPPORT_BAND - dist) * 3
        return {
            "code": s["code"], "name": s["name"],
            "score": score,
            "signal": quant.signal_from_score(score)[0],
            "price": round(price, 2),
            "support": nearest["price"],
            "support_strength": nearest["strength"],
            "dist_to_support": round(dist, 2),
            "candidate_score": round(score + bonus, 1),
        }

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="scan") as pool_exec:
        results = list(pool_exec.map(one, pool))
    candidates = [c for c in results if c is not None]

    candidates.sort(key=lambda x: -x["candidate_score"])
    result = {
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pool_size": len(pool),
        "scanned": scanned,
        "criteria": f"评分≥{SCORE_THRESHOLD} 且 价格距支撑位 ≤{SUPPORT_BAND}%",
        "candidates": candidates[:30],
    }
    save_json(SCANNER_OUT, result)
    print(f"[scan] 扫描 {scanned} 只，候选 {len(candidates)} 只（取前30）")
    return result


if __name__ == "__main__":
    r = scan()
    print("\n=== 候选股 Top 10 ===")
    for c in r["candidates"][:10]:
        print(f"  {c['name']}({c['code']}) 评分{c['score']} 现价{c['price']} 支撑{c['support']} 距支撑{c['dist_to_support']}% 候选分{c['candidate_score']}")
