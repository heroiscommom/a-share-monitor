#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组合级回测（2026-08-17 新增）—— 把信号升级为策略：
  固定调仓周期（每10个交易日）在沪深300池上取评分 Top-N 等权组合，
  计算净值曲线 / 年化 / 最大回撤 / Sharpe，对比沪深300基准。

对比四种策略：
  A. 均值回归 Top20（原始评分）
  B. 均值回归 Top20（行业中性化：评分 - 行业均值）
  C. 动量 Top20
  D. 市场状态组合：上涨市→动量，其余→行业中性化均值回归

口径：双边成本 0.25%/调仓；T+1 自然满足（收盘买、10日后收盘卖）。
"""

import os
import json
import time
import datetime
import math
import urllib.request

import quant
import backtest
import sector as sector_mod
import pool_backtest as pb

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE_DIR, "data", "portfolio_backtest.json")
INDUSTRY_MAP_PATH = os.path.join(BASE_DIR, "data", "industry_map.json")

POOL_SIZE = 300
REBALANCE_DAYS = 10      # 调仓周期
TOP_N = 20               # 组合持仓数
COST_PCT = backtest.COST_PCT
MIN_HISTORY = 60


def fetch_industry_map(pool):
    """拉取池内股票的申万一级行业（新浪），带缓存；覆盖不到的归「其他」"""
    cache = pb.load_json(INDUSTRY_MAP_PATH, None)
    if cache and cache.get("date") == datetime.date.today().isoformat():
        return cache.get("map", {})
    industries = sector_mod.fetch_sector_list()
    mapping = {}
    for ind in industries:
        try:
            stocks = sector_mod.fetch_sector_stocks(ind["node"], num=100)
        except Exception:
            continue
        for s in stocks:
            mapping[s["code"]] = ind["name"]
        time.sleep(0.1)
    # 池内未覆盖的股票
    for s in pool:
        mapping.setdefault(s["code"], "其他")
    obj = {"date": datetime.date.today().isoformat(), "map": mapping}
    pb.save_json(INDUSTRY_MAP_PATH, obj)
    return mapping


def stock_bar_index(hist, target_date):
    """找历史中 date == target_date 的下标（精确匹配）"""
    lo, hi = 0, len(hist) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if hist[mid]["date"] == target_date:
            return mid
        if hist[mid]["date"] < target_date:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1


def run():
    pool = pb.load_json(pb.POOL_LIST_PATH, None)
    if not pool or len(pool) < POOL_SIZE:
        pool = pb.fetch_pool_list(POOL_SIZE)
        pb.save_json(pb.POOL_LIST_PATH, pool)

    print(f"[pool] 股票池 {len(pool)} 只；拉取历史（3年）...")
    hist_map = {}
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
        if hist:
            hist_map[s["code"]] = hist
        if (i + 1) % 50 == 0:
            print(f"  历史进度 {i + 1}/{len(pool)}")

    print("[industry] 拉取申万一级行业分类 ...")
    industry_map = fetch_industry_map(pool)
    covered = sum(1 for s in pool if industry_map.get(s["code"]) not in (None, "其他"))
    print(f"  池内行业覆盖 {covered}/{len(pool)}")

    idx = pb.fetch_index()
    idates = idx.get("dates") or []
    icloses = idx.get("closes") or []
    if len(idates) < MIN_HISTORY + REBALANCE_DAYS * 2:
        print("指数数据不足")
        return

    # 调仓日：从末尾每 10 个交易日取一个（升序）
    grid_pos = list(range(len(idates) - 1 - REBALANCE_DAYS, 0, -REBALANCE_DAYS))[::-1]
    grid_dates = [idates[p] for p in grid_pos]

    strategies = {
        "A_均值回归Top20": {"mode": "mr_raw"},
        "B_均值回归中性化Top20": {"mode": "mr_neutral"},
        "C_动量Top20": {"mode": "mom"},
        "D_市场状态组合": {"mode": "combo"},
    }
    results = {k: {"returns": [], "navs": [], "dates": []} for k in strategies}
    bench_returns = []

    # 行业中性化需要每期行业均值
    for t, d in enumerate(grid_dates):
        p = grid_pos[t]
        d_plus = idates[p + REBALANCE_DAYS]
        # 基准=买入持有指数，只扣一次性成本（不按调仓期重复扣）
        bench_ret = (icloses[p + REBALANCE_DAYS] / icloses[p] - 1) * 100
        bench_returns.append(bench_ret)

        # 当期截面：所有有数据的股票
        cross = []   # (code, name, mr, mscore, industry)
        for s in pool:
            hist = hist_map.get(s["code"])
            if not hist or len(hist) < MIN_HISTORY + 5:
                continue
            i0 = stock_bar_index(hist, d)
            i1 = stock_bar_index(hist, d_plus)
            if i0 < MIN_HISTORY or i1 <= i0:
                continue
            try:
                _, fac = quant.compute_factors(hist[: i0 + 1])
                mr = quant.compute_score(fac)
                _, mfac = quant.momentum_factors(hist[: i0 + 1])
                ms = quant.momentum_score(mfac)
            except Exception:
                continue
            cross.append((s["code"], s["name"], mr, ms, industry_map.get(s["code"], "其他")))

        if len(cross) < TOP_N * 2:
            continue

        # 行业均值（均值回归分）
        ind_means = {}
        for _, _, mr, _, ind in cross:
            ind_means.setdefault(ind, []).append(mr)
        ind_means = {k: sum(v) / len(v) for k, v in ind_means.items()}

        # 市场状态
        regime = quant.market_regime(icloses[: p + 1])["regime"]

        # 构建各策略选股
        def top_n(key_fn, exclude_none=False):
            ranked = sorted(cross, key=key_fn, reverse=True)
            return ranked[:TOP_N]

        picks = {}
        picks["A_均值回归Top20"] = top_n(lambda x: x[2])
        picks["B_均值回归中性化Top20"] = top_n(lambda x: x[2] - ind_means.get(x[4], 0))
        picks["C_动量Top20"] = top_n(lambda x: x[3])
        if regime == "上涨":
            picks["D_市场状态组合"] = picks["C_动量Top20"]
        else:
            picks["D_市场状态组合"] = picks["B_均值回归中性化Top20"]

        for name, sel in picks.items():
            rets = []
            for code, _, _, _, _ in sel:
                hist = hist_map[code]
                i0 = stock_bar_index(hist, d)
                i1 = stock_bar_index(hist, d_plus)
                if i0 >= 0 and i1 > i0 and hist[i0]["close"]:
                    rets.append((hist[i1]["close"] / hist[i0]["close"] - 1) * 100 - COST_PCT)
            if rets:
                results[name]["returns"].append(sum(rets) / len(rets))
                results[name]["dates"].append(d)

    # 指标统计
    def metrics(rets, bench):
        n = len(rets)
        if n < 5:
            return None
        nav = 1.0
        navs = []
        for r in rets:
            nav *= (1 + r / 100)
            navs.append(nav)
        total = (nav - 1) * 100
        years = n * REBALANCE_DAYS / 252
        ann = (nav ** (1 / years) - 1) * 100 if years > 0 else 0.0
        # 最大回撤：滚动峰值（不能用全局峰值，会虚高）
        peak_so_far = navs[0]
        mdd = 0.0
        for v in navs:
            peak_so_far = max(peak_so_far, v)
            mdd = max(mdd, (peak_so_far - v) / peak_so_far * 100)
        avg = sum(rets) / n
        std = (sum((r - avg) ** 2 for r in rets) / n) ** 0.5
        sharpe = avg / std * math.sqrt(252 / REBALANCE_DAYS) if std > 0 else 0.0
        win = sum(1 for r in rets if r > 0) / n * 100
        bench_total = None
        if bench:
            bn = 1.0
            for r in bench:
                bn *= (1 + r / 100)
            bench_total = (bn - 1) * 100
        return {
            "rebalances": n, "total_return": round(total, 1), "annualized": round(ann, 1),
            "max_drawdown": round(mdd, 1), "sharpe": round(sharpe, 2),
            "avg_per_rebalance": round(avg, 2), "win_rate": round(win, 1),
            "benchmark_total": round(bench_total, 1) if bench_total is not None else None,
            "excess_total": round(total - bench_total, 1) if bench_total is not None else None,
        }

    bench_metrics = metrics(bench_returns, None)
    out = {
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "methodology": (
            f"沪深300池{POOL_SIZE}只 · 每{REBALANCE_DAYS}日调仓 · Top{TOP_N}等权 · "
            f"双边成本{COST_PCT}%/次 · 行业中性化=评分减行业均值 · 市场状态组合=上涨市用动量/其余用中性化均值回归 · "
            f"基准=买入持有沪深300(不重复扣成本)"
        ),
        "caveats": (
            "⚠️ 股票池为当前市值Top300（含期内大涨的赢家），绝对收益存在幸存者偏差、偏高；"
            "相对比较（策略间、vs基准）方向可信，绝对数字请打折扣看待"
        ),
        "benchmark": bench_metrics,
        "strategies": {},
    }
    print("\n===== 组合回测（每10日调仓 Top20 等权）=====")
    print(f"基准 沪深300: 总收益 {bench_metrics['total_return']}% · 年化 {bench_metrics['annualized']}% · 最大回撤 {bench_metrics['max_drawdown']}%")
    for name in strategies:
        m = metrics(results[name]["returns"], bench_returns)
        # 保存净值序列供分析
        nav = 1.0
        series = []
        for dd, rr in zip(results[name]["dates"], results[name]["returns"]):
            nav *= (1 + rr / 100)
            series.append({"date": dd, "ret": round(rr, 2), "nav": round(nav, 3)})
        m["series"] = series
        out["strategies"][name] = m
        if m:
            print(f"\n{name}:")
            print(f"  总收益 {m['total_return']}% (超额 {m['excess_total']}%) · 年化 {m['annualized']}% · 回撤 {m['max_drawdown']}% · Sharpe {m['sharpe']} · 胜率 {m['win_rate']}%")
    pb.save_json(OUT_PATH, out)
    print(f"\n[已保存] {OUT_PATH}")


if __name__ == "__main__":
    run()
