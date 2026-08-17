# -*- coding: utf-8 -*-
"""
打分层（scoring）—— 纯函数，输入标准记录，输出 0-100 分。

当前：热度分（排名/排名变化/涨跌幅/新闻数 加权）
预留：情感分 sentiment_score(text) —— 未来接入 NLP（snownlp/大模型）后
      只需实现该函数并在 heat_score 里加权，上层零改动。
"""
from . import config


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def sentiment_score(text):
    """情感分插槽（-100~+100）。当前未实现，返回 None（不参与加权）。"""
    return None


def heat_score(stock):
    """
    热度分 0-100。分量：
      rank:         排名越靠前分越高（1→100, 100→20）
      rank_change:  排名较历史上升越多分越高（0-100）
      change_pct:   当日涨幅贡献（-5%→0, 0→50, +5%→100）
      news:         新闻条数（0→0, ≥3→100）
    """
    w = config.HEAT_WEIGHTS

    rank = stock.get("rank")
    rank_part = _clamp(100 - (rank or 100) * 0.8) if rank else 40

    rc = stock.get("rank_change")
    rank_change_part = _clamp(50 + (rc or 0) * 4) if rc is not None else 50

    cp = stock.get("change_pct")
    change_part = _clamp(50 + (cp or 0) * 10) if cp is not None else 50

    n_news = len(stock.get("news") or [])
    news_part = _clamp(n_news / config.NEWS_PER_STOCK_MAX * 100)

    score = (rank_part * w["rank"] + rank_change_part * w["rank_change"]
             + change_part * w["change_pct"] + news_part * w["news"])
    return round(score, 1)


def score_all(payload):
    """为所有股票打热度分，按分降序返回 payload（含排序后的 stocks）"""
    for s in payload["stocks"]:
        s["heat_score"] = heat_score(s)
    payload["stocks"].sort(key=lambda x: x["heat_score"], reverse=True)
    return payload
