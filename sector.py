#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块分析 —— 基于新浪行业分类，计算各行业板块的趋势与异动。

数据源：新浪（稳定）
  - 行业列表：Market_Center.getHQNodes（"新浪行业"节点）
  - 板块成分股：Market_Center.getHQNodeData?node=xxx（按市值取前 N 只）
"""

import json
import urllib.request

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_sector_list():
    """返回 [{name, node}] 新浪行业列表"""
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "Market_Center.getHQNodes")
    data = json.loads(http_get(url))
    industries = []

    def walk(node):
        if not isinstance(node, list):
            return
        for i, item in enumerate(node):
            if item == "新浪行业" and i + 1 < len(node) and isinstance(node[i + 1], list):
                for ind in node[i + 1]:
                    if isinstance(ind, list) and len(ind) >= 3 and ind[2]:
                        industries.append({"name": ind[0], "node": ind[2]})
            else:
                walk(item)

    walk(data)
    return industries


def fetch_sector_stocks(node, num=25):
    """返回某行业按市值排序的前 num 只股票"""
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"Market_Center.getHQNodeData?page=1&num={num}&sort=mktcap&asc=0&node={node}&symbol=&_s_r_a=page")
    try:
        data = json.loads(http_get(url))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def aggregate(stocks):
    """按流通市值加权平均涨跌幅，统计涨跌家数"""
    if not stocks:
        return None
    total_mc = sum(_f(s.get("mktcap")) for s in stocks)
    if total_mc <= 0:
        return None
    weighted = sum(_f(s.get("changepercent")) * _f(s.get("mktcap")) for s in stocks) / total_mc
    up = sum(1 for s in stocks if _f(s.get("changepercent")) > 0)
    down = sum(1 for s in stocks if _f(s.get("changepercent")) < 0)
    return {
        "avg_change": round(weighted, 2),
        "up": up,
        "down": down,
        "count": len(stocks),
    }


def analyze_sectors(limit=40, threshold=2.0):
    """
    分析各行业板块，返回 (sectors, anomalies)
    sectors: 按涨跌幅排序的板块列表
    anomalies: 涨跌幅超阈值的板块（异动）
    """
    sectors = fetch_sector_list()
    results = []
    for sec in sectors[:limit]:
        stocks = fetch_sector_stocks(sec["node"])
        agg = aggregate(stocks)
        if not agg:
            continue
        results.append({
            "name": sec["name"],
            "node": sec["node"],
            "avg_change": agg["avg_change"],
            "up": agg["up"],
            "down": agg["down"],
            "count": agg["count"],
        })

    results.sort(key=lambda x: x["avg_change"], reverse=True)
    anomalies = [
        r for r in results
        if abs(r["avg_change"]) >= threshold
    ]
    return results, anomalies


if __name__ == "__main__":
    import datetime
    sectors, anomalies = analyze_sectors()
    print(f"板块 {len(sectors)} 个，异动 {len(anomalies)} 个\n")
    print("涨幅前5：")
    for s in sectors[:5]:
        print(f"  {s['name']:>8} {s['avg_change']:>6.2f}%  (涨{s['up']}/跌{s['down']})")
    print("跌幅前5：")
    for s in sectors[-5:]:
        print(f"  {s['name']:>8} {s['avg_change']:>6.2f}%  (涨{s['up']}/跌{s['down']})")
    if anomalies:
        print("\n异动板块：")
        for a in anomalies:
            print(f"  {'📈' if a['avg_change']>0 else '📉'} {a['name']} {a['avg_change']:+.2f}%")
