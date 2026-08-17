#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票池回测 v3（2026-08-17 严谨版）
- 非重叠抽样 + 双边成本 + 一字涨停/跌停剔除（复用 backtest.py）
- walk-forward 样本外验证：固定网格阈值 vs 分位数动态阈值
- 市场状态分层：按样本日期对应沪深300 20日状态（震荡/上涨/下跌）分别统计
- 动量因子验证：mscore 阈值表 + 各状态下强势组表现
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
INDEX_CACHE = os.path.join(BASE_DIR, "data", "index_cache.json")

POOL_SIZE = 300
FORWARD_DAYS = 10
MIN_HISTORY = 60
HISTORY_DAYS = 750      # 3年（2026-08-17 拉长历史）

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as r:
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


def threshold_table(samples, key="score", ths=None):
    rows = []
    for th in (ths or [50, 55, 60, 65, 70, 75, 78, 80, 82, 85, 88, 90]):
        g = [s for s in samples if s.get(key) is not None and s[key] >= th]
        if not g:
            continue
        win = sum(1 for s in g if s["fwd_return"] > 0) / len(g) * 100
        avg = sum(s["fwd_return"] for s in g) / len(g)
        rows.append({"threshold": th, "count": len(g), "win_rate": round(win, 1), "avg_return": round(avg, 2)})
    return rows


def fetch_index(days=HISTORY_DAYS):
    """沪深300 日K（腾讯），当日缓存"""
    cache = load_json(INDEX_CACHE, None)
    today = datetime.date.today().isoformat()
    if cache and cache.get("date") == today and cache.get("closes") and len(cache["closes"]) >= days * 0.9:
        return cache
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
        f"sh000300,day,,,{days},qfq"
    )
    data = json.loads(http_get(url))
    node = (data.get("data") or {}).get("sh000300") or {}
    klines = node.get("qfqday") or node.get("day") or []
    closes = [float(k[2]) for k in klines if len(k) >= 3]
    dates = [k[0] for k in klines if len(k) >= 3]
    obj = {"date": today, "closes": closes, "dates": dates}
    save_json(INDEX_CACHE, obj)
    return obj


def index_benchmark(closes, forward_days):
    """沪深300 同口径基准：非重叠 10 日收益（同样扣成本）"""
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


def build_regime_map(idx):
    """date -> 市场状态（当日往前20日的沪深300趋势）"""
    dates = idx.get("dates") or []
    closes = idx.get("closes") or []
    regime_map = {}
    for i in range(20, len(dates)):
        regime_map[dates[i]] = quant.market_regime(closes[: i + 1])["regime"]
    return regime_map


def bucket_stats(samples):
    if not samples:
        return {"n": 0}
    win = sum(1 for s in samples if s["fwd_return"] > 0) / len(samples) * 100
    avg = sum(s["fwd_return"] for s in samples) / len(samples)
    return {"n": len(samples), "win_rate": round(win, 1), "avg_return": round(avg, 2)}


def regime_analysis(samples, regime_map):
    """按市场状态分层：全体 / 超跌组(评分≥75) / 强势组(动量≥70)"""
    buckets = {"震荡": [], "上涨": [], "下跌": []}
    for s in samples:
        r = regime_map.get(s.get("date"))
        if r in buckets:
            buckets[r].append(s)

    out = {"method": "按样本日期对应沪深300 20日状态分层（震荡/上涨/下跌）", "states": {}}
    for name, group in buckets.items():
        mr = [s for s in group if s.get("score") is not None and s["score"] >= 75]
        mom = [s for s in group if s.get("mscore") is not None and s["mscore"] >= 70]
        out["states"][name] = {
            "all": bucket_stats(group),
            "超跌组(评分≥75)": bucket_stats(mr),
            "强势组(动量≥70)": bucket_stats(mom),
        }
    # 结论：均值回归在哪个状态有效、动量在哪个状态有效
    lines = []
    for name in ("震荡", "上涨", "下跌"):
        st = out["states"][name]
        a = st["all"]
        mr = st["超跌组(评分≥75)"]
        mo = st["强势组(动量≥70)"]
        if mr.get("n", 0) >= 20:
            lines.append(f"{name}市: 超跌组胜率{mr.get('win_rate')}%/收益{mr.get('avg_return')}% vs 全体{ a.get('win_rate') }%/{a.get('avg_return')}%")
        if mo.get("n", 0) >= 20:
            lines.append(f"{name}市: 强势组胜率{mo.get('win_rate')}%/收益{mo.get('avg_return')}%")
    out["conclusion"] = "；".join(lines) if lines else "各状态样本不足"
    return out


def walk_forward_oos(pool, hist_map, regime_map):
    """walk-forward：前60%训练选阈值 → 后40%测试；固定网格 vs 分位数动态阈值"""
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

    # 基准对齐测试期日历窗口（公平对比）
    idx = fetch_index()
    test_dates = [x.get("date") for x in test_all if x.get("date")]
    bench = None
    if test_dates:
        t0, t1 = min(test_dates), max(test_dates)
        idates = idx.get("dates") or []
        icloses = idx.get("closes") or []
        win_closes = [c for d, c in zip(idates, icloses) if t0 <= d <= t1]
        if len(win_closes) > FORWARD_DAYS + 5:
            bench = index_benchmark(win_closes, FORWARD_DAYS)

    def evaluate(samples, th):
        g = [x for x in samples if x.get("score") is not None and x["score"] >= th]
        if not g:
            return None
        wr = round(sum(1 for x in g if x["fwd_return"] > 0) / len(g) * 100, 1)
        avg = round(sum(x["fwd_return"] for x in g) / len(g), 2)
        return {"threshold": th, "n": len(g), "win_rate": wr, "avg_return": avg}

    # 1) 固定网格：训练集上最大化胜率（样本≥30）
    best_th, best_wr = None, -1.0
    for th in range(50, 91):
        g = [x for x in train_all if x.get("score") is not None and x["score"] >= th]
        if len(g) < 30:
            continue
        wr = sum(1 for x in g if x["fwd_return"] > 0) / len(g)
        if wr > best_wr:
            best_th, best_wr = th, wr
    grid_train = evaluate(train_all, best_th) if best_th else None
    grid_test = evaluate(test_all, best_th) if best_th else None

    # 2) 分位数动态阈值：训练集评分 90 分位（下限70）
    tr_scores = sorted(x["score"] for x in train_all if x.get("score") is not None)
    q_th = tr_scores[int(len(tr_scores) * 0.9) - 1] if tr_scores else None
    q_th = max(70, int(q_th)) if q_th is not None else None
    q_train = evaluate(train_all, q_th) if q_th else None
    q_test = evaluate(test_all, q_th) if q_th else None

    oos = {
        "method": "walk-forward：前60%训练选阈值 → 后40%测试评估",
        "train_n": len(train_all),
        "test_n": len(test_all),
        "benchmark": bench,
        "grid": {"train": grid_train, "test": grid_test},
        "quantile": {"pct": 90, "train": q_train, "test": q_test},
    }
    # 结论
    lines = []
    for tag, q in (("网格阈值", grid_test), ("分位数阈值", q_test)):
        if q and bench:
            lines.append(
                f"{tag}≥{q['threshold']}: 样本外胜率{q['win_rate']}% vs 基准{bench['win_rate']}%"
                f"（{round(q['win_rate'] - bench['win_rate'], 1)}pp）；10日平均{q['avg_return']}% vs {bench['avg_return']}%"
                f"（超额{round(q['avg_return'] - bench['avg_return'], 2)}%）"
            )
    oos["conclusion"] = "；".join(lines) if lines else "样本不足"
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
            if hist is None or len(hist) < HISTORY_DAYS - 50:  # 缓存不足则重拉（3年）
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

    # 3. 指数 + 市场状态
    idx = fetch_index()
    regime_map = build_regime_map(idx)

    # 4. 统计
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
            "thresholds": threshold_table(all_samples, "score"),
            "momentum_thresholds": threshold_table(all_samples, "mscore", [50, 55, 60, 65, 70, 75, 80]),
            "conclusion": backtest.make_conclusion(groups, ic),
        }
    result["methodology"] = (
        f"非重叠抽样(每{FORWARD_DAYS}日1样本) · 双边成本{backtest.COST_PCT}% · "
        f"剔除一字涨停买入/一字跌停卖出 · 历史{HISTORY_DAYS}天"
    )
    result["regimes"] = regime_analysis(all_samples, regime_map)
    result["oos"] = walk_forward_oos(pool, hist_map, regime_map)
    if isinstance(result.get("oos"), dict) and result["oos"].get("conclusion"):
        result["conclusion"] += "；" + result["oos"]["conclusion"]
    if isinstance(result.get("regimes"), dict) and result["regimes"].get("conclusion"):
        result["conclusion"] += "；市场状态分层：" + result["regimes"]["conclusion"]
    result["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(POOL_BACKTEST_PATH, result)
    print("\n===== 股票池回测结果（严谨版v3）=====")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
