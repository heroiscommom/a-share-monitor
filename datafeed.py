#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情数据源模块（2026-08 重构）
================================================
统一腾讯/新浪免费接口的行情拉取函数，替代散落在 monitor.py / pool_backtest.py /
scanner.py / auto_report.py / morning_report.py / advice_history.py /
weekly_review.py 里的重复实现。

数据源（全部免费，无需 token）：
  - 实时行情/分时/日K：腾讯 qt.gtimg.cn / web.ifzq.gtimg.cn
  - 主力资金流：新浪 MoneyFlow
"""

import os
import json
import datetime

from common import http_get, load_json, save_json, to_float, DATA_DIR

INDEX_CACHE = os.path.join(DATA_DIR, "index_cache.json")


def parse_quote_payload(payload):
    """
    解析单只股票的腾讯行情负载（~ 分隔，≥50 字段），返回完整 quote dict 或 None。
    纯函数：字段索引脆弱（接口改版会变），用 tests/test_core.py 的 fixture 守护。
    """
    parts = payload.strip().strip('"').split("~")
    if len(parts) < 50:
        return None
    code = parts[2]
    amount_wan = to_float(parts[37])  # 成交额，单位：万元
    return {
        "code": code,
        "name": parts[1],
        "price": to_float(parts[3]),
        "prev_close": to_float(parts[4]),
        "open": to_float(parts[5]),
        "volume": to_float(parts[6]),       # 手
        "change": to_float(parts[31]),
        "change_pct": to_float(parts[32]),  # %
        "high": to_float(parts[33]),
        "low": to_float(parts[34]),
        "amount": amount_wan * 10000 if amount_wan is not None else None,  # 元
        "turnover_rate": to_float(parts[38]) if len(parts) > 38 else None,  # 换手率%
        "pe": to_float(parts[39]) if len(parts) > 39 else None,            # 市盈率
        "float_mktcap": to_float(parts[44]) if len(parts) > 44 else None,  # 流通市值(亿)
        "total_mktcap": to_float(parts[45]) if len(parts) > 45 else None,  # 总市值(亿)
        "pb": to_float(parts[46]) if len(parts) > 46 else None,            # 市净率
    }


def parse_fqkline(node):
    """解析腾讯日K节点（qfqday/day）→ [{date, open, close, high, low, volume}, ...]"""
    klines = node.get("qfqday") or node.get("day") or []
    out = []
    for k in klines:
        if len(k) < 6:
            continue
        out.append({
            "date": k[0],
            "open": to_float(k[1]),
            "close": to_float(k[2]),
            "high": to_float(k[3]),
            "low": to_float(k[4]),
            "volume": to_float(k[5]),
        })
    return out


def fetch_quote(code, market):
    """腾讯实时行情（单只，快），返回 {"price", "change_pct"} 或 None"""
    try:
        raw = http_get(f"https://qt.gtimg.cn/q={market}{code}", encoding="gbk")
        parts = raw.split("~")
        if len(parts) > 4:
            return {
                "price": float(parts[3]),
                "change_pct": float(parts[32]) if len(parts) > 32 else None,
            }
    except Exception:
        pass
    return None


def fetch_quotes(watchlist):
    """
    批量拉取实时行情（腾讯，一次请求拿全部），返回 {code: {...}}。
    watchlist: [{code, market, ...}, ...]
    """
    symbols = [f"{s['market']}{s['code']}" for s in watchlist]
    text = http_get("https://qt.gtimg.cn/q=" + ",".join(symbols), encoding="gbk")

    quotes = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        _, _, payload = line.partition("=")
        q = parse_quote_payload(payload)
        if q:
            quotes[q["code"]] = q
    return quotes


def fetch_history(code, market, days=60):
    """
    拉取前复权日K（腾讯），返回 [{date, open, close, high, low, volume}, ...]
    最后一根是"今天"（盘中为当日实时K线）。
    """
    symbol = f"{market}{code}"
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
        f"{symbol},day,,,{days},qfq"
    )
    data = json.loads(http_get(url))
    node = (data.get("data") or {}).get(symbol) or {}
    return parse_fqkline(node)


def fetch_intraday(code, market):
    """拉取当日分时数据（腾讯），返回 {date, prev_close, minutes:[{t,p,avg,v}]}"""
    symbol = f"{market}{code}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    data = json.loads(http_get(url))
    node = (data.get("data") or {}).get(symbol) or {}
    inner = node.get("data") or {}
    date = inner.get("date", "")
    raw = inner.get("data") or []
    qt = (node.get("qt") or {}).get(symbol) or []
    prev_close = to_float(qt[4]) if len(qt) > 4 else None

    minutes = []
    prev_vol = 0.0
    for m in raw:
        parts = m.split()
        if len(parts) < 4:
            continue
        price = to_float(parts[1])
        cum_vol = to_float(parts[2]) or 0.0
        cum_amt = to_float(parts[3])
        avg = None
        if cum_vol > 0 and cum_amt is not None:
            avg = cum_amt / (cum_vol * 100)
        minutes.append({
            "t": f"{parts[0][:2]}:{parts[0][2:]}",
            "p": price,
            "avg": round(avg, 3) if avg is not None else None,
            "v": round(cum_vol - prev_vol),
        })
        prev_vol = cum_vol
    return {"date": date, "prev_close": prev_close, "minutes": minutes}


def fetch_index(days=250):
    """沪深300 日K（腾讯），当日缓存到 data/index_cache.json（与 pool_backtest 共用）"""
    cache = load_json(INDEX_CACHE, None)
    today = datetime.date.today().isoformat()
    if cache and cache.get("date") == today and cache.get("closes") and len(cache["closes"]) >= days * 0.9:
        return cache
    symbol = "sh000300"
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
        f"{symbol},day,,,{days},qfq"
    )
    data = json.loads(http_get(url))
    node = (data.get("data") or {}).get(symbol) or {}
    klines = node.get("qfqday") or node.get("day") or []
    closes = [float(k[2]) for k in klines if len(k) >= 3]
    dates = [k[0] for k in klines if len(k) >= 3]
    obj = {"date": today, "closes": closes, "dates": dates}
    save_json(INDEX_CACHE, obj)
    return obj


def fetch_moneyflow(code, market):
    """新浪主力资金流，返回 {date, netamount(元), r0_net(元), turnover, change_pct} 或 None"""
    symbol = f"{market}{code}"
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"MoneyFlow.ssl_qsfx_zjlrqs?page=1&num=1&sort=opendate&asc=0&daima={symbol}")
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
    try:
        data = json.loads(http_get(url, headers=headers, timeout=15))
    except Exception:
        return None
    if not data or not isinstance(data, list):
        return None
    d = data[0]
    cr = d.get("changeratio")
    return {
        "date": d.get("opendate"),
        "netamount": to_float(d.get("netamount")),       # 主力净流入(元)
        "r0_net": to_float(d.get("r0_net")),             # 特大单净流入(元)
        "change_pct": round(cr * 100, 2) if isinstance(cr, (int, float)) else None,
    }
