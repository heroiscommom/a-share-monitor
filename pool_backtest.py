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


def threshold_table(samples):
    rows = []
    for th in [50, 55, 60, 65, 70, 75, 78, 80, 82, 85, 88, 90]:
        g = [s for s in samples if s["score"] >= th]
        if not g:
            continue
        win = sum(1 for s in g if s["fwd_return"] > 0) / len(g) * 100
        avg = sum(s["fwd_return"] for s in g) / len(g)
        rows.append({"threshold": th, "count": len(g), "win_rate": round(win, 1), "avg_return": round(avg, 2)})
    return rows


INDEX_CACHE = os.path.join(BASE_DIR, "data", "index_cache.json")


def fetch_index(days=320):
    """拉取沪深300日K收盘价（腾讯），带当日缓存"""
    cache = load_json(INDEX_CACHE, None)
    today = datetime.date.today().isoformat()
    if cache and cache.get("date") == today and cache.get("closes"):
        return cache["closes"]
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
        f"sh000300,day,,,{320},qfq"
    )
    data = json.loads(http_get(url))
    node = (data.get("data") or {}).get("sh000300") or {}
    klines = node.get("qfqday") or node.get("day") or []
    closes = [float(k[2]) for k in klines if len(k) >= 3]
    save_json(INDEX_CACHE, {"date": today, "closes": closes})
    return closes


def index_benchmark(closes, forward_days):
    """沪深300 同口径基准：非重叠 10 日收益（同样扣成本，公平对比）"""
    rets = []
    i = 0
    while i <= len(closes) - forward_days - 1:
        if closes[i] and closes[i + forward_days]:
            rets.append((closes[i + forward_days] / closes[i] - 1) * 100 - backtest.COST_PCT)
        i += forward_days
    if not rets:
        return None
    return {
        "avg_return": round(sum(rets) / len(rets), 2),
        "win_rate": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1),
        "samples": len(rets),
    }


def walk_forward_oos(pool, hist_map):
    """
    样本外验证（walk-forward）：
      每只股票历史前 60% 作训练集、后 40% 作测试集；
      在训练集上网格搜索最优评分阈值（样本≥30），再在测试集上评估。
    """
    train_all, test_all = [], []
    for s in pool:
        hist = hist_map.get(s["code"])
        if not hist or len(hist) < MIN_HISTORY + FORWARD_DAYS + 20:
            continue
        split = int(len(hist) * 0.6)
        if split > MIN_HISTORY + FORWARD_DAYS:
            train_all += backtest.backtest_stock(hist[:split], FORWARD_DAYS, s["code"])
        if len(hist) - split > MIN_HISTORY + FORWARD_DAYS:
            test_all += backtest.backtest_stock(hist[split:], FORWARD_DAYS, s["code"])
    if len(train_all) < 100 or len(test_all) < 50:
        return {"error": f"样本不足(train={len(train_all)}, test={len(test_all)})"}

    # 训练集上选阈值：最大化胜率，样本数≥30
    best_th, best_wr, best_avg = None, -1.0, None
    for th in range(50, 91):
        g = [x for x in train_all if x["score"] >= th]
        if len(g) < 30:
            continue
        wr = sum(1 for x in g if x["fwd_return"] > 0) / len(g)
        avg = sum(x["fwd_return"] for x in g) / len(g)
        if wr > best_wr or (wr == best_wr and avg > (best_avg or -999)):
            best_th, best_wr, best_avg = th, wr, avg

    if best_th is None:
        return {"error": "训练集无法选出阈值"}

    test_g = [x for x in test_all if x["score"] >= best_th]
    bench = index_benchmark(fetch_index(), FORWARD_DAYS)
    test_n = len(test_g)
    test_wr = round(sum(1 for x in test_g if x["fwd_return"] > 0) / test_n * 100, 1) if test_n else None
    test_avg = round(sum(x["fwd_return"] for x in test_g) / test_n, 2) if test_n else None

    oos = {
        "method": "walk-forward：前60%训练选阈值 → 后40%测试评估",
        "train_n": len(train_all),
        "train_threshold": best_th,
        "train_win_rate": round(best_wr * 100, 1),
        "test_n": test_n,
        "test_win_rate": test_wr,
        "test_avg_return": test_avg,
        "benchmark": bench,
        "alpha_vs_index": round(test_avg - bench["avg_return"], 2) if test_n and bench else None,
    }
    if test_n and bench:
        oos["conclusion"] = (
            f"样本外：评分≥{best_th} 胜率 {test_wr}% vs 沪深300基准 {bench['win_rate']}%"
            f"（+{round(test_wr - bench['win_rate'], 1)}pp）；10日平均 {test_avg}% vs {bench['avg_return']}%"
            f"（超额 {oos['alpha_vs_index']}%）"
        )
    return oos


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
    hist_map = {}
    for i, s in enumerate(pool):
        hist = hist_map.get(s["code"])
        if hist is None:
            hist = load_json(history_path(s["code"]), None)
            if hist is None:
                try:
                    hist = fetch_history(s["code"], s["market"])
                    if hist:
                        save_json(history_path(s["code"]), hist)
                except Exception:
                    hist = []
                time.sleep(0.06)
        if not hist:
            continue
        hist_map[s["code"]] = hist
        if len(hist) >= MIN_HISTORY + FORWARD_DAYS:
            try:
                all_samples.extend(backtest.backtest_stock(hist, FORWARD_DAYS, s["code"]))
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
            "thresholds": threshold_table(all_samples),
            "conclusion": backtest.make_conclusion(groups, ic),
        }
    result["methodology"] = (
        f"非重叠抽样(每{FORWARD_DAYS}日1样本) · 双边成本{backtest.COST_PCT}% · "
        f"剔除一字涨停买入/一字跌停卖出 · 阈值表为全样本(样本内)"
    )
    result["oos"] = walk_forward_oos(pool, hist_map)
    if isinstance(result.get("oos"), dict) and result["oos"].get("conclusion"):
        result["conclusion"] += "；" + result["oos"]["conclusion"]
    result["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(POOL_BACKTEST_PATH, result)
    print("\n===== 股票池回测结果 =====")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
