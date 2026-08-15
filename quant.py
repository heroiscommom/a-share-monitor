#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化因子计算引擎 —— 基于日K历史计算 6 维因子，加权得出 0-100 综合评分与信号。

因子（各维度归一化到 0-100）：
  动量 momentum   20日涨幅
  趋势 trend      均线多头/空头排列
  强弱 rsi        RSI(14)
  量能 volume     5日均量 / 20日均量
  稳定 volatility 20日收益率标准差（越低越稳）
  位置 position   60日区间位置
"""


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def std(values):
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


WEIGHTS = {
    "momentum": 0.20,
    "trend": 0.25,
    "rsi": 0.15,
    "volume": 0.15,
    "volatility": 0.10,
    "position": 0.15,
}


def compute_factors(history):
    """返回 (indicators, factors)，factors 各维度 0-100"""
    closes = [h["close"] for h in history]
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    volumes = [h["volume"] for h in history]
    n = len(closes)
    ind, fac = {}, {}

    # 1. 动量：20日涨幅
    if n >= 21:
        mom20 = (closes[-1] / closes[-21] - 1) * 100
        ind["mom20"] = round(mom20, 2)
        fac["momentum"] = round(clamp((mom20 + 20) / 40 * 100))
    else:
        fac["momentum"] = 50

    # 2. 趋势：均线排列
    ma5, ma10, ma20 = sma(closes, 5), sma(closes, 10), sma(closes, 20)
    if ma5 and ma10 and ma20:
        ind["ma5"] = round(ma5, 2)
        ind["ma20"] = round(ma20, 2)
        c = closes[-1]
        if c > ma5 > ma10 > ma20:
            fac["trend"] = 90
        elif c > ma20:
            fac["trend"] = 70
        elif c < ma5 < ma10 < ma20:
            fac["trend"] = 10
        elif c < ma20:
            fac["trend"] = 30
        else:
            fac["trend"] = 50
    else:
        fac["trend"] = 50

    # 3. RSI 强弱
    rsi = calc_rsi(closes)
    if rsi is not None:
        ind["rsi"] = round(rsi, 1)
        if rsi < 30:
            fac["rsi"] = 40
        elif rsi < 45:
            fac["rsi"] = 55
        elif rsi <= 60:
            fac["rsi"] = 75
        elif rsi <= 70:
            fac["rsi"] = 60
        else:
            fac["rsi"] = 35
    else:
        fac["rsi"] = 50

    # 4. 量能：5日均量 / 20日均量
    vol5, vol20 = sma(volumes, 5), sma(volumes, 20)
    if vol5 and vol20 and vol20 > 0:
        vr = vol5 / vol20
        ind["vol_ratio"] = round(vr, 2)
        fac["volume"] = round(clamp(vr * 50))
    else:
        fac["volume"] = 50

    # 5. 波动率（稳定性）：20日收益率标准差，越低越稳
    if n >= 21:
        rets = [closes[i] / closes[i - 1] - 1 for i in range(n - 20, n)]
        vol = std(rets) * 100
        ind["volatility"] = round(vol, 2)
        fac["volatility"] = round(clamp((3 - vol) / 2.5 * 100))
    else:
        fac["volatility"] = 50

    # 6. 位置：60日区间位置
    if n >= 20:
        window = 60 if n >= 60 else n
        h = max(highs[-window:])
        l = min(lows[-window:])
        pos = (closes[-1] - l) / (h - l) * 100 if h > l else 50
        ind["pos60"] = round(pos, 1)
        fac["position"] = round(clamp(pos))
    else:
        fac["position"] = 50

    return ind, fac


def compute_score(factors):
    return round(sum(factors[k] * w for k, w in WEIGHTS.items()), 1)


def signal_from_score(score):
    if score >= 75:
        return "强势", "strong"
    if score >= 62:
        return "偏多", "bullish"
    if score >= 45:
        return "中性", "neutral"
    if score >= 32:
        return "偏空", "bearish"
    return "弱势", "weak"
