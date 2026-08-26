#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
支撑位/压力位计算引擎 —— 基于日K历史自动识别关键价位。

算法（三种方法交叉验证，来源越多价位越「强」）：
  1. N日高低点（20/60日最高=压力、最低=支撑）
  2. 摆动高低点（局部极值，左右各3天）
  3. 成交密集区（密度剖面 v2：100档 [low,high]区间覆盖 + 成交量加权 + 高斯平滑 + 峰值检测多区）

v2（2026-08-26 升级）：
  - 原 volume_profile 只用「收盘价单点」归 20 档、取 top3，丢失区间覆盖与量加权信息
  - 新 density_zones：每根K线 [low,high] 覆盖所有档位并按成交量加权 → 高斯平滑去噪
    → 峰值检测一次找多个密集区（含显著性过滤），输出「区间」而非单点
  - 纯标准库实现（自写高斯核卷积 + 峰值检测），保持零依赖
"""

import math


# ═══════════════════════════════════════════
# 密度剖面 v2（纯标准库）
# ═══════════════════════════════════════════

def _gauss_smooth(arr, sigma=2.0):
    """一维高斯平滑（自写核卷积，替代 scipy.ndimage.gaussian_filter1d）"""
    if len(arr) < 3:
        return list(arr)
    r = max(1, int(3 * sigma))
    kernel = [math.exp(-x * x / (2 * sigma * sigma)) for x in range(-r, r + 1)]
    ks = sum(kernel)
    kernel = [k / ks for k in kernel]
    n = len(arr)
    out = []
    for i in range(n):
        s = 0.0
        for kx, kv in enumerate(kernel):
            j = i + kx - r
            if 0 <= j < n:
                s += arr[j] * kv
        out.append(s)
    mx = max(out) or 1.0
    return [x / mx for x in out]


def _peak_prominence(dens, i):
    """峰值显著性：峰高 - 左右两侧第一个不低于它的点中较高一侧的高度"""
    n = len(dens)
    v = dens[i]
    l = i - 1
    while l >= 0 and dens[l] < v:
        l -= 1
    lb = dens[l] if l >= 0 else 0.0
    r = i + 1
    while r < n and dens[r] < v:
        r += 1
    rb = dens[r] if r < n else 0.0
    return v - max(lb, rb)


def _find_peaks(dens, min_dist=3, top_n=6):
    """
    峰值检测：全部局部极大值 → 按密度降序贪心取 top_n 个不相邻峰。
    （比显著性过滤更稳健：单主峰 + 多副峰场景都能给出多个候选）
    """
    n = len(dens)
    cands = []
    for i in range(1, n - 1):
        if dens[i] > dens[i - 1] and dens[i] >= dens[i + 1]:
            cands.append((dens[i], i))
    cands.sort(key=lambda x: -x[0])
    picked = []
    for v, i in cands:
        if len(picked) >= top_n:
            break
        if all(abs(i - j) >= min_dist for _, j in picked):
            picked.append((v, i))
    picked.sort(key=lambda x: x[1])
    return [i for _, i in picked]


def density_profile(history, days=60, bins=50, sigma=2.0):
    """
    价格密度剖面 v2：把窗口内价格等分为 bins 档，
    每根K线 [low,high] 覆盖的所有档位 + 当日成交量权重，
    归一化后高斯平滑。

    返回 (levels, density, lo, hi)
      levels:  各档中心价
      density: 归一化密度（0~1）
    """
    data = history[-days:]
    lo = min(h["low"] for h in data)
    hi = max(h["high"] for h in data)
    if hi <= lo:
        return [], [], lo, hi
    step = (hi - lo) / bins
    dens = [0.0] * bins
    for h in data:
        l_idx = max(0, int((h["low"] - lo) / step))
        h_idx = min(bins - 1, int((h["high"] - lo) / step))
        w = h.get("volume") or 1.0
        for j in range(l_idx, h_idx + 1):
            dens[j] += w
    mx = max(dens) or 1.0
    dens = [d / mx for d in dens]
    dens = _gauss_smooth(dens, sigma)
    levels = [round(lo + (i + 0.5) * step, 2) for i in range(bins)]
    return levels, dens, lo, hi


def density_zones(history, days=60, bins=50, n=3, sigma=1.5, max_width_pct=0.35):
    """
    密集成交区检测 v2：密度剖面 + 峰值检测，输出多个「区间」。

    返回 [{center, low, high, strength, volume, zone_type, distance_pct}]
      strength: 归一化峰高（0~1）
      low/high: 区间（65% 峰高截断，宽度上限为窗口价差的 max_width_pct）
    """
    data = history[-days:]
    if len(data) < 10:
        return []
    levels, dens, lo, hi = density_profile(data, days, bins, sigma)
    if not dens:
        return []
    step = (hi - lo) / bins
    n_bins = len(dens)
    window_width = hi - lo

    peaks = _find_peaks(dens, min_dist=max(3, n_bins // 15), top_n=n * 2 + 1)
    if not peaks:
        peaks = [int(max(range(n_bins), key=lambda i: dens[i]))]

    current = float(data[-1]["close"])
    zones = []
    # 按强度降序处理（主峰优先）
    for pk in sorted(peaks, key=lambda p: -dens[p])[:n]:
        cutoff = dens[pk] * 0.65
        l = pk
        while l > 0 and dens[l] > cutoff:
            l -= 1
        r = pk
        while r < n_bins - 1 and dens[r] > cutoff:
            r += 1
        zone_low = lo + l * step
        zone_high = lo + (r + 1) * step
        # 宽度超限 → 收紧截断到 80% 峰高
        if zone_high - zone_low > window_width * max_width_pct:
            cutoff2 = dens[pk] * 0.8
            l2 = pk
            while l2 > 0 and dens[l2] > cutoff2:
                l2 -= 1
            r2 = pk
            while r2 < n_bins - 1 and dens[r2] > cutoff2:
                r2 += 1
            zone_low, zone_high = lo + l2 * step, lo + (r2 + 1) * step
        # 仍超限 → 固定窄带 ±3 bin
        if zone_high - zone_low > window_width * max_width_pct:
            zone_low = lo + max(0, pk - 3) * step
            zone_high = lo + min(n_bins, pk + 4) * step
        # 过窄保底
        if zone_high - zone_low < step * 2:
            zone_low = max(lo, lo + (pk - 1) * step)
            zone_high = min(hi, lo + (pk + 2) * step)
        center = levels[pk]

        # 区域与已选区重叠 >50% → 跳过（保留更强的峰）
        if any(
            min(zone_high, z["high"]) - max(zone_low, z["low"]) > 0.5 * (zone_high - zone_low)
            for z in zones
        ):
            continue

        # 区域内重叠成交量
        vol = 0.0
        for h in data:
            if h["low"] <= zone_high and h["high"] >= zone_low:
                vol += h.get("volume") or 0.0

        dist_pct = (center - current) / current * 100
        zones.append({
            "center": round(center, 2),
            "low": round(zone_low, 2),
            "high": round(zone_high, 2),
            "strength": round(float(dens[pk]), 4),
            "volume": round(vol),
            "zone_type": "support" if dist_pct < 0 else "resistance",
            "distance_pct": round(dist_pct, 2),
        })
    zones.sort(key=lambda z: -z["strength"])
    return zones


# ═══════════════════════════════════════════
# 兼容保留：旧版单档成交量分布（v1）
# ═══════════════════════════════════════════

def volume_profile(history, days=60, bins=20):
    """成交量价格分布，返回 [(价位, 成交量)] 按成交量降序（v1 保留，仅兼容）"""
    data = history[-days:]
    lo = min(h["low"] for h in data)
    hi = max(h["high"] for h in data)
    if hi <= lo:
        return []
    step = (hi - lo) / bins
    vols = [0.0] * bins
    for h in data:
        idx = int((h["close"] - lo) / step)
        idx = max(0, min(bins - 1, idx))
        vols[idx] += h["volume"]
    result = []
    for i, v in enumerate(vols):
        result.append((round(lo + (i + 0.5) * step, 2), v))
    result.sort(key=lambda x: -x[1])
    return result


def _strength(n):
    return "强" if n >= 3 else ("中" if n == 2 else "弱")


def compute_levels(history):
    """返回 {supports: [...], resistances: [...]}，各取最近3个"""
    if len(history) < 30:
        return {"supports": [], "resistances": []}

    current = history[-1]["close"]
    levels = {}  # price -> {"sources": [], "volume": 0, "zone": {...}}

    def add(price, source, vol=0.0, zone=None):
        if price is None or price <= 0:
            return
        price = round(price, 2)
        if price not in levels:
            levels[price] = {"sources": [], "volume": 0.0, "zone": None}
        if source not in levels[price]["sources"]:
            levels[price]["sources"].append(source)
        levels[price]["volume"] += vol
        if zone and levels[price]["zone"] is None:
            levels[price]["zone"] = zone

    # 1. N日高低点
    for n in (20, 60):
        if len(history) >= n:
            seg = history[-n:]
            add(max(h["high"] for h in seg), f"{n}日高点")
            add(min(h["low"] for h in seg), f"{n}日低点")

    # 2. 摆动高低点（左右各3天）
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    k = 3
    for i in range(k, len(history) - k):
        if highs[i] == max(highs[i - k:i + k + 1]):
            add(highs[i], "前高")
        if lows[i] == min(lows[i - k:i + k + 1]):
            add(lows[i], "前低")

    # 3. 成交密集区（密度剖面 v2，top3 区间）
    for z in density_zones(history, days=60, bins=50, n=3):
        add(z["center"], "筹码密集", z["volume"],
            zone={"low": z["low"], "high": z["high"], "strength": z["strength"]})

    # 分类
    supports = []
    resistances = []
    for price, info in levels.items():
        pct = round((price - current) / current * 100, 2)
        entry = {
            "price": price,
            "strength": _strength(len(info["sources"])),
            "sources": info["sources"],
            "volume": round(info["volume"]),
            "distance_pct": pct,
        }
        if info.get("zone"):
            entry["zone"] = info["zone"]
        if price < current:
            supports.append(entry)
        elif price > current:
            resistances.append(entry)

    # 合并距离1.2%内的价位，按距离排序
    supports = _merge(supports, current, reverse=True)
    resistances = _merge(resistances, current, reverse=False)
    return {"supports": supports[:3], "resistances": resistances[:3]}


def _merge(entries, current, tol=0.012, reverse=False):
    """合并相近价位（<1.2%），保留更近的一个并合并来源"""
    entries.sort(key=lambda x: -x["price"] if reverse else x["price"])
    out = []
    for e in entries:
        if out and abs(e["price"] - out[-1]["price"]) / out[-1]["price"] < tol:
            merged = out[-1]
            merged["sources"] = list(dict.fromkeys(merged["sources"] + e["sources"]))
            merged["strength"] = _strength(len(merged["sources"]))
            merged["volume"] = round(merged["volume"] + e["volume"])
            # 区间信息：任意一方有则保留
            if e.get("zone") and not merged.get("zone"):
                merged["zone"] = e["zone"]
            # 保留离当前价更近的
            if abs(e["price"] - current) < abs(merged["price"] - current):
                merged["price"] = e["price"]
            merged["distance_pct"] = round((merged["price"] - current) / current * 100, 2)
        else:
            out.append(e)
    return out


if __name__ == "__main__":
    import json
    import sys
    hist = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "data/history/600036.json", "r", encoding="utf-8"))
    r = compute_levels(hist)
    print(f"当前价: {hist[-1]['close']}")
    print(f"压力位(上方):")
    for l in r["resistances"]:
        print(f"  {l['price']} ({l['strength']}) [+{l['distance_pct']}%] {l['sources']} zone={l.get('zone')}")
    print(f"支撑位(下方):")
    for l in r["supports"]:
        print(f"  {l['price']} ({l['strength']}) [{l['distance_pct']}%] {l['sources']} zone={l.get('zone')}")
