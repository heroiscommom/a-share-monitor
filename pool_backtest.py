#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池回测 —— 用近似沪深300的池子（按市值取前 N 只A股）检验量化因子有效性。

数据源：
  股票池：新浪按市值排序的 A股列表
  历史：腾讯前复权日K（缓存到 data/pool/）
回测：复用 backtest.py 的滚动回测逻辑，输出 IC + 分组收益。
"""

import os
import json
import time
import datetime
import urllib.request

import quant
import backtest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POOL_DIR = os.path.join(BASE_DIR, "data", "pool")
POOL_LIST_PATH = os.path.join(BASE_DIR, "data", "pool_list.json")
POOL_BACKTEST_PATH = os.path.join(BASE_DIR, "data", "pool_backtest.json")

POOL_SIZE = 300
FORWARD_DAYS = 10
MIN_HISTORY = 60
HISTORY_DAYS = 250

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


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


def fetch_pool_list(size=POOL_SIZE):
    """从新浪按市值取前 N 只A股"""
    stocks = []
    page = 1
    while len(stocks) < size and page <= 10:
        url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page=%d&num=100&sort=mktcap&asc=0&node=hs_a&symbol=&_s_r_a=page"
            % page
        )
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
    symbol = f"{market}{code}"
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
        f"{symbol},day,,,{days},qfq"
    )
    data = json.loads(http_get(url))
    node = (data.get("data") or {}).get(symbol) or {}
    klines = node.get("qfqday") or node.get("day") or []
    out = []
    for k in klines:
        if len(k) < 6:
            continue
        try:
            out.append({
                "date": k[0],
                "open": float(k[1]), "close": float(k[2]),
                "high": float(k[3]), "low": float(k[4]), "volume": float(k[5]),
            })
        except (ValueError, IndexError):
            continue
    return out


def main():
    # 1. 股票池
    pool = load_json(POOL_LIST_PATH, None)
    if not pool or len(pool) < POOL_SIZE:
        pool = fetch_pool_list(POOL_SIZE)
        save_json(POOL_LIST_PATH, pool)
    print(f"[pool] 股票池 {len(pool)} 只")

    # 2. 拉历史（带缓存）+ 回测
    all_samples = []
    valid = 0
    for i, s in enumerate(pool):
        hist = load_json(history_path(s["code"]), None)
        if hist is None:
            try:
                hist = fetch_history(s["code"], s["market"])
                if hist:
                    save_json(history_path(s["code"]), hist)
            except Exception:
                hist = []
            time.sleep(0.06)
        if hist and len(hist) >= MIN_HISTORY + FORWARD_DAYS:
            try:
                all_samples.extend(backtest.backtest_stock(hist, FORWARD_DAYS))
                valid += 1
            except Exception:
                pass
        if (i + 1) % 50 == 0:
            print(f"  进度 {i + 1}/{len(pool)}，有效 {valid} 只，样本 {len(all_samples)} 个")

    # 3. 统计
    if not all_samples:
        result = {"total_samples": 0, "ic": None, "conclusion": "样本不足"}
    else:
        groups = backtest.group_stats(all_samples)
        scores = [s["score"] for s in all_samples]
        rets = [s["fwd_return"] for s in all_samples]
        ic = round(backtest.pearson(scores, rets), 4)
        result = {
            "pool_size": len(pool),
            "valid_stocks": valid,
            "forward_days": FORWARD_DAYS,
            "total_samples": len(all_samples),
            "groups": groups,
            "ic": ic,
            "conclusion": backtest.make_conclusion(groups, ic),
        }
    result["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(POOL_BACKTEST_PATH, result)
    print("\n===== 股票池回测结果 =====")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
