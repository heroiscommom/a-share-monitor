# -*- coding: utf-8 -*-
"""
采集层（fetchers）—— 所有数据源统一返回「标准记录」：
    {code, name, price, change_pct, rank, rank_change, news: [...]}

新数据源（雪球/微博/研报等）只需在此追加一个 fetch_xxx() 并保持返回结构一致，
上层打分/存储/推送零改动 —— 这是模块化的核心约定。
"""
import json
import urllib.request

from . import config

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def http_json(url, data=None, headers=None):
    """GET/POST 请求返回 JSON"""
    req_headers = dict(HEADERS)
    if headers:
        req_headers.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=req_headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def http_text(url, encoding="gbk"):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode(encoding, errors="replace")


def fetch_hot_rank():
    """东财人气榜：返回 [{code, market, rank, rank_change}]（全市场按热度排序）"""
    data = http_json(config.HOT_RANK_URL, config.HOT_RANK_BODY,
                     headers={"Referer": "https://quote.eastmoney.com/"})
    items = data.get("data") or []
    out = []
    for it in items:
        sc = it.get("sc", "")          # 如 SH600584
        if len(sc) < 6:
            continue
        market = "sh" if sc[:2].upper() == "SH" else "sz"
        code = sc[2:]
        out.append({
            "code": code,
            "market": market,
            "rank": it.get("rk"),
            "rank_change": it.get("hisRc"),   # 历史排名变化（正=上升）
        })
    return out


def fetch_quotes(codes):
    """腾讯批量行情：返回 {code: {name, price, change_pct}}"""
    if not codes:
        return {}
    # 腾讯一次最多约 60 个，分批
    out = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i + 50]
        symbols = ",".join(f"{m}{c}" for c, m in batch)
        text = http_text(config.QUOTE_URL + symbols)
        for line in text.strip().split(";"):
            if "=" not in line:
                continue
            parts = line.partition("=")[2].strip().strip('"').split("~")
            if len(parts) < 33:
                continue
            out[parts[2]] = {
                "name": parts[1],
                "price": float(parts[3]) if parts[3] else None,
                "change_pct": float(parts[32]) if parts[32] else None,
            }
    return out


def fetch_news():
    """东财财经新闻列表：返回 [{title, summary, time}]"""
    try:
        data = http_json(config.NEWS_URL, headers={"Referer": "https://finance.eastmoney.com/"})
        lst = (data.get("data") or {}).get("list") or []
        out = []
        for n in lst:
            out.append({
                "title": n.get("title") or "",
                "summary": (n.get("summary") or "")[:120],
                "time": n.get("showTime") or n.get("createTime") or "",
            })
        return out
    except Exception as e:
        print(f"[fetcher] 新闻获取失败: {e}")
        return []


def scan():
    """
    主采集入口：热度榜 + 行情补充 + 新闻
    返回：{stocks: [...标准记录...], news: [...]}
    """
    hot = fetch_hot_rank()[: config.HOT_POOL_SIZE]
    print(f"[fetcher] 热度榜 {len(hot)} 只")

    quotes = fetch_quotes([(h["code"], h["market"]) for h in hot])
    news = fetch_news()
    print(f"[fetcher] 新闻 {len(news)} 条")

    stocks = []
    for h in hot:
        q = quotes.get(h["code"]) or {}
        stocks.append({
            "code": h["code"],
            "market": h["market"],
            "name": q.get("name") or h["code"],
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "rank": h["rank"],
            "rank_change": h["rank_change"],
            # 新闻匹配（标题/摘要含股票名或代码 → 计入该股）
            "news": [n for n in news
                     if (q.get("name") and q["name"] in (n["title"] + n["summary"]))
                     or h["code"] in (n["title"] + n["summary"])][: config.NEWS_PER_STOCK_MAX],
        })
    return {"stocks": stocks, "news": news}
