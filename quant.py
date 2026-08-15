#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化因子计算引擎（均值回归版 v2）—— 6 维因子，加权得出 0-100 评分。

回测验证（300只/5.2万样本）：
  - 追涨型因子（v1）IC 负，弃用
  - 均值回归型（v2）：「超跌」信号有效，胜率随阈值单调上升
  - v3 加布林带/连跌因子反而稀释信号，已回退 v2

因子（各维度 0-100，越高越超跌/越有机会）：
  rsi         超卖    RSI 越低分越高
  drawdown    超跌    20日涨幅反向（跌越多分越高）
  deviation   偏离    价格相对20日均线负偏离越大分越高
  position    低位    距60日区间低点越近分越高
  volume      量能    缩量（抛压衰竭）分越高
  volatility  稳定    波动率越低分越高
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
    "rsi": 0.25,
    "drawdown": 0.25,
    "deviation": 0.20,
    "position": 0.15,
    "volume": 0.10,
    "volatility": 0.05,
}

# 超跌机会阈值（回测：≥82 胜率约 64%，≥85 约 71%）
BUY_THRESHOLD = 82
RISK_THRESHOLD = 32


def compute_factors(history):
    """返回 (indicators, factors)，factors 各维度 0-100"""
    closes = [h["close"] for h in history]
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    volumes = [h["volume"] for h in history]
    n = len(closes)
    ind, fac = {}, {}

    # 1. RSI 超卖
    rsi = calc_rsi(closes)
    if rsi is not None:
        ind["rsi"] = round(rsi, 1)
        if rsi < 30:
            fac["rsi"] = 90
        elif rsi < 40:
            fac["rsi"] = 75
        elif rsi < 50:
            fac["rsi"] = 60
        elif rsi < 60:
            fac["rsi"] = 45
        elif rsi < 70:
            fac["rsi"] = 30
        else:
            fac["rsi"] = 15
    else:
        fac["rsi"] = 50

    # 2. 超跌：20日涨幅反向
    if n >= 21:
        mom20 = (closes[-1] / closes[-21] - 1) * 100
        ind["mom20"] = round(mom20, 2)
        fac["drawdown"] = round(clamp((20 - mom20) / 40 * 100))
    else:
        fac["drawdown"] = 50

    # 3. 均线偏离：价格相对20日均线，负偏离分高
    ma20 = sma(closes, 20)
    if ma20:
        dev = (closes[-1] / ma20 - 1) * 100
        ind["deviation"] = round(dev, 2)
        fac["deviation"] = round(clamp(50 - dev * 5))
    else:
        fac["deviation"] = 50

    # 4. 低位：距60日区间低点越近分越高
    if n >= 20:
        window = 60 if n >= 60 else n
        h = max(highs[-window:])
        l = min(lows[-window:])
        pos = (closes[-1] - l) / (h - l) if h > l else 0.5
        ind["pos60"] = round(pos * 100, 1)
        fac["position"] = round(clamp((1 - pos) * 100))
    else:
        fac["position"] = 50

    # 5. 量能：缩量（抛压衰竭）分高
    vol5, vol20 = sma(volumes, 5), sma(volumes, 20)
    if vol5 and vol20 and vol20 > 0:
        vr = vol5 / vol20
        ind["vol_ratio"] = round(vr, 2)
        fac["volume"] = round(clamp((1.2 - vr) * 100))
    else:
        fac["volume"] = 50

    # 6. 稳定：波动率越低分越高
    if n >= 21:
        rets = [closes[i] / closes[i - 1] - 1 for i in range(n - 20, n)]
        vol = std(rets) * 100
        ind["volatility"] = round(vol, 2)
        fac["volatility"] = round(clamp((3 - vol) / 2.5 * 100))
    else:
        fac["volatility"] = 50

    return ind, fac


def compute_score(factors):
    return round(sum(factors[k] * w for k, w in WEIGHTS.items()), 1)


def signal_from_score(score, buy_threshold=BUY_THRESHOLD, risk_threshold=RISK_THRESHOLD):
    if score >= buy_threshold:
        return "超跌机会", "strong"
    if score >= 62:
        return "偏多", "bullish"
    if score >= 45:
        return "中性", "neutral"
    if score >= risk_threshold:
        return "偏空", "bearish"
    return "高位风险", "weak"
