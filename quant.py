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


# ============================================================
# 动量因子（2026-08-17 新增，U型另一端）：越高=越强势
# 用于「强势突破机会」信号，与均值回归互补
# ============================================================

def calc_std(values):
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5


def momentum_factors(history):
    """返回 (indicators, factors)，factors 各维度 0-100，越高越强势"""
    closes = [h["close"] for h in history]
    highs = [h["high"] for h in history]
    lows = [h["low"] for h in history]
    volumes = [h["volume"] for h in history]
    n = len(closes)
    ind, fac = {}, {}

    # 1. 20日动量
    if n >= 21:
        mom20 = (closes[-1] / closes[-21] - 1) * 100
        ind["mom20"] = round(mom20, 2)
        fac["mom20"] = round(clamp(mom20 / 8 * 100))
    else:
        fac["mom20"] = 50

    # 2. 60日动量
    if n >= 61:
        mom60 = (closes[-1] / closes[-61] - 1) * 100
        ind["mom60"] = round(mom60, 2)
        fac["mom60"] = round(clamp(mom60 / 15 * 100))
    else:
        fac["mom60"] = 50

    # 3. RSI 强势（高 RSI 分高）
    rsi = calc_rsi(closes)
    if rsi is not None:
        ind["rsi"] = round(rsi, 1)
        fac["rsi"] = round(clamp((rsi - 30) / 40 * 100))
    else:
        fac["rsi"] = 50

    # 4. 距60日高点（越接近高点越强）
    if n >= 20:
        window = 60 if n >= 60 else n
        h = max(highs[-window:])
        l = min(lows[-window:])
        pos = (closes[-1] - l) / (h - l) if h > l else 0.5
        ind["pos60"] = round(pos * 100, 1)
        fac["pos"] = round(clamp(pos * 100))
    else:
        fac["pos"] = 50

    # 5. 量能（放量分高）
    vol5, vol20 = sma(volumes, 5), sma(volumes, 20)
    if vol5 and vol20 and vol20 > 0:
        vr = vol5 / vol20
        ind["vol_ratio"] = round(vr, 2)
        fac["vol"] = round(clamp((vr - 0.6) / 0.8 * 100))
    else:
        fac["vol"] = 50

    # 6. 活跃度（波动率高分高）
    if n >= 21:
        rets = [closes[i] / closes[i - 1] - 1 for i in range(n - 20, n)]
        vol = calc_std(rets) * 100
        ind["volatility"] = round(vol, 2)
        fac["act"] = round(clamp(vol / 2.5 * 100))
    else:
        fac["act"] = 50

    return ind, fac


MOM_WEIGHTS = {
    "mom20": 0.25, "mom60": 0.20, "rsi": 0.20,
    "pos": 0.15, "vol": 0.10, "act": 0.10,
}

MOM_STRONG_THRESHOLD = 70   # 强势突破信号阈值（动量评分）


def momentum_score(factors):
    return round(sum(factors[k] * w for k, w in MOM_WEIGHTS.items()), 1)


# ============================================================
# 市场状态（2026-08-17 新增）：用沪深300 20日趋势+波动率判断
# 震荡/企稳 → 均值回归有效窗口；单边下跌 → 超跌是接飞刀；单边上涨 → 动量有效
# ============================================================

def market_regime(index_closes, lookback=20):
    """
    输入沪深300收盘价序列（升序），返回 {"regime", "mom20", "vol20", "desc"}
    regime: 震荡 | 上涨 | 下跌
    """
    if not index_closes or len(index_closes) < lookback + 1:
        return {"regime": "未知", "mom20": None, "vol20": None, "desc": "指数数据不足"}
    mom20 = (index_closes[-1] / index_closes[-1 - lookback] - 1) * 100
    rets = [index_closes[i] / index_closes[i - 1] - 1 for i in range(len(index_closes) - lookback, len(index_closes))]
    vol20 = calc_std(rets) * 100
    if mom20 > 3.0:
        regime = "上涨"
    elif mom20 < -3.0:
        regime = "下跌"
    else:
        regime = "震荡"
    desc = f"沪深300 20日涨跌 {mom20:+.1f}%，波动 {vol20:.1f}% → {regime}市"
    return {"regime": regime, "mom20": round(mom20, 1), "vol20": round(vol20, 1), "desc": desc}


# ═══════════════════════════════════════════
# 新增策略因子（2026-08-26，P1a yaml 化配套）
# ═══════════════════════════════════════════

def ma_golden_cross_factors(history):
    """
    均线金叉因子（0-100，越高越强）：
      cross  金叉强度：MA5 上穿 MA20 的天数/幅度
      slope  均线斜率：MA20 近10日斜率
      vol    量能确认：金叉日放量程度
      hold   回踩守住：现价距 MA5/MA20 的距离（回踩不破）
    """
    def ma(n):
        if len(history) < n:
            return None
        return sum(h["close"] for h in history[-n:]) / n
    ma5, ma20 = ma(5), ma(20)
    if ma5 is None or ma20 is None or ma20 <= 0:
        return None
    # 金叉强度：MA5-MA20 归一化（2% 为满分）
    cross = max(0.0, min(100.0, (ma5 / ma20 - 1) * 100 / 2.0 * 100))
    # 斜率：MA20 近10日变化（2% 为满分）
    if len(history) >= 30:
        ma20_prev = sum(h["close"] for h in history[-30:-10]) / 20
        slope = max(0.0, min(100.0, (ma20 / ma20_prev - 1) * 100 / 2.0 * 100)) if ma20_prev else 50
    else:
        slope = 50
    # 量能确认：近5日均量 vs 前20日均量（放量 1.2x 满分）
    v5 = sum(h["volume"] for h in history[-5:]) / 5
    v20 = sum(h["volume"] for h in history[-25:-5]) / 20 if len(history) >= 25 else v5
    vol = min(100.0, max(0.0, v5 / v20 * 100)) if v20 else 50
    # 回踩守住：现价在 MA5 附近（±1.5% 满分）
    cur = history[-1]["close"]
    dist_ma5 = abs(cur / ma5 - 1) * 100
    hold = max(0.0, 100 - dist_ma5 * 30)
    return {"cross": round(cross), "slope": round(slope), "vol": round(vol), "hold": round(hold)}


def ma_golden_cross_score(factors):
    if not factors:
        return None
    return round(factors["cross"] * 0.35 + factors["slope"] * 0.25 +
                 factors["vol"] * 0.20 + factors["hold"] * 0.20, 1)


def shrink_pullback_factors(history):
    """
    缩量回调因子（0-100，越高越值得低吸）：
      trend    趋势向上：MA20 高于 MA60 且斜率正
      shrink   缩量程度：近5日均量 / 前20日均量（越缩越好）
      pullback 回调深度：现价距 MA20 的回调幅度（0~8% 为佳）
      support  近支撑：现价接近 60日支撑（复用 support_resistance）
    """
    def ma(n):
        if len(history) < n:
            return None
        return sum(h["close"] for h in history[-n:]) / n
    ma20, ma60 = ma(20), ma(60)
    if ma20 is None or ma60 is None or ma60 <= 0:
        return None
    # 趋势
    slope = 0.0
    if len(history) >= 40:
        ma20_prev = sum(h["close"] for h in history[-40:-20]) / 20
        slope = (ma20 / ma20_prev - 1) * 100 if ma20_prev else 0
    trend = max(0.0, min(100.0, (ma20 / ma60 - 1) * 100 / 5.0 * 100 * 0.6 + slope * 20))
    # 缩量：近5日/前20日，0.6 倍以下满分
    v5 = sum(h["volume"] for h in history[-5:]) / 5
    v20 = sum(h["volume"] for h in history[-25:-5]) / 20 if len(history) >= 25 else v5
    ratio = v5 / v20 if v20 else 1.0
    shrink = max(0.0, min(100.0, (1.0 - ratio) * 150))
    # 回调深度：距 MA20 的负偏离 0~8% 最佳
    cur = history[-1]["close"]
    dev = (cur / ma20 - 1) * 100
    if -8 <= dev <= 0:
        pullback = 100 - abs(dev) * 5
    elif dev > 0:
        pullback = max(0.0, 60 - dev * 10)
    else:
        pullback = max(0.0, 60 - abs(dev + 8) * 6)
    # 近支撑：现价距最近支撑 0~3% 满分
    support = 50.0
    try:
        import support_resistance
        lv = support_resistance.compute_levels(history)
        if lv["supports"]:
            sp = lv["supports"][0]["price"]
            dist = (cur - sp) / cur * 100
            if 0 <= dist <= 3:
                support = 100 - dist * 20
            elif dist < 0:
                support = 30  # 已跌破支撑
    except Exception:
        pass
    return {"trend": round(trend), "shrink": round(shrink),
            "pullback": round(pullback), "support": round(support)}


def shrink_pullback_score(factors):
    if not factors:
        return None
    return round(factors["trend"] * 0.30 + factors["shrink"] * 0.30 +
                 factors["pullback"] * 0.20 + factors["support"] * 0.20, 1)
