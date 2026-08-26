#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
龙头战法模块（2026-08-26 新增，纯标准库）
================================================
数据源：东方财富涨停板池（免费、无需 token，接口已实测）
  https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=100&sort=fbt:asc&date=YYYYMMDD

功能：
  1. 拉取任意交易日涨停池（含 lbc连板数 / fbt首板时间 / fund封单 / zbc炸板 / hybk行业）
  2. 龙头强度分（0-100）：连板高度30 + 封板强度25 + 首板时间15 + 炸板10 + 板块共振10 + 换手10
  3. S/A/B/C 分级（对齐现有推送体系）
  4. 断板低吸候选：昨日连板≥2 今日断板 → 结合支撑压力 v2 守住率给出低吸参考

⚠️ 仅供学习研究，不构成投资建议。打板/接力高风险，断板低吸相对稳健。
"""

import json
import math
import os
import time
import datetime
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else "."
DATA_DIR = os.path.join(BASE_DIR, "data")
ZT_CACHE = os.path.join(DATA_DIR, "ztpool_cache.json")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
API = (
    "https://push2ex.eastmoney.com/getTopicZTPool"
    "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
    "&Pageindex={page}&pagesize=100&sort=fbt%3Aasc&date={date}"
)


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def _trade_date_before(date_str, days=1):
    """往前找最近交易日（东财返回空池视为非交易日，最多回退7天）"""
    d = datetime.datetime.strptime(date_str, "%Y%m%d")
    for _ in range(7):
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5:  # 周一~周五（粗略，节假日返回空池会自动再退）
            return d.strftime("%Y%m%d")
    return d.strftime("%Y%m%d")


def fetch_zt_pool(date=None, max_pages=3):
    """
    拉取指定交易日的涨停池（可翻页），返回 [{code,name,price,zdp,amount,ltsz,hs,
    lbc,fbt,lbt,fund,zbc,hybk,zttj}, ...]

    date: 'YYYYMMDD'，None = 今天
    失败/无数据返回 []
    """
    if date is None:
        date = datetime.date.today().strftime("%Y%m%d")
    out = []
    for page in range(max_pages):
        try:
            raw = http_get(API.format(page=page, date=date))
            data = json.loads(raw)
            pool = ((data.get("data") or {}).get("pool")) or []
        except Exception:
            break
        if not pool:
            break
        for it in pool:
            out.append({
                "code": str(it.get("c", "")),
                "name": it.get("n", ""),
                "price": it.get("p"),
                "zdp": it.get("zdp"),
                "amount": it.get("amount"),
                "ltsz": it.get("ltsz"),            # 流通市值
                "hs": it.get("hs"),                # 换手率%
                "lbc": it.get("lbc", 0),           # 连板数
                "fbt": it.get("fbt"),              # 首板时间 HHMMSS
                "lbt": it.get("lbt"),
                "fund": it.get("fund"),            # 封单资金
                "zbc": it.get("zbc", 0),           # 炸板次数
                "hybk": it.get("hybk", ""),        # 行业板块
                "days": (it.get("zttj") or {}).get("days", 0),
            })
        if len(pool) < 100:
            break
        time.sleep(0.3)
    return out


def _fbt_score(fbt):
    """首板时间分：09:25=1.0 线性衰减到 15:00≈0"""
    if not fbt:
        return 0.5
    t = int(fbt)
    hh, mm = t // 10000, (t // 100) % 100
    mins = hh * 60 + mm
    if mins <= 9 * 60 + 25:
        return 1.0
    if mins <= 10 * 60:
        return 0.8
    if mins <= 11 * 60 + 30:
        return 0.55
    if mins <= 14 * 60:
        return 0.3
    return 0.15


def dragon_score(it, sector_count_map=None):
    """
    龙头强度分 0-100：
      连板高度30 + 封板强度25 + 首板时间15 + 炸板10 + 板块共振10 + 换手10
    """
    # 连板高度：log2 归一化，6板及以上满分
    lbc = it.get("lbc") or 1
    h_score = min(1.0, math.log2(max(lbc, 1)) / math.log2(6)) * 30

    # 封板强度：封单/流通市值，≥5% 满分
    fund, ltsz = it.get("fund") or 0, it.get("ltsz") or 0
    f_ratio = (fund / ltsz * 100) if ltsz > 0 else 0
    f_score = min(1.0, f_ratio / 5.0) * 25

    # 首板时间
    t_score = _fbt_score(it.get("fbt")) * 15

    # 炸板：0炸满分，每炸 -30%
    zbc = it.get("zbc") or 0
    z_score = max(0.0, 1.0 - zbc * 0.3) * 10

    # 板块共振：同行业涨停家数（由外部传入的统计表）
    if sector_count_map:
        cnt = sector_count_map.get(it.get("hybk", ""), 1)
    else:
        cnt = it.get("days") or 1
    s_score = min(1.0, cnt / 3.0) * 10

    # 换手：5~20% 最佳，过低缩量板/过高出货板打折
    hs = it.get("hs") or 5
    if 5 <= hs <= 20:
        hx = 1.0
    elif hs < 5:
        hx = 0.6 + hs / 5 * 0.4
    else:
        hx = max(0.2, 1.0 - (hs - 20) / 30)
    x_score = hx * 10

    return round(h_score + f_score + t_score + z_score + s_score + x_score, 1)


def tier_of(score, lbc):
    """S/A/B/C 分级（对齐现有推送体系）"""
    if lbc >= 3 and score >= 70:
        return "S"
    if lbc >= 2 and score >= 60:
        return "A"
    if lbc >= 1 and score >= 45:
        return "B"
    return "C"


def build_tiers(pool):
    """按强度分分级，返回 {S:[...], A:[...], B:[...], C:[...]}（各自内按分数降序）"""
    # 板块共振统计
    sec_cnt = {}
    for it in pool:
        sec_cnt[it.get("hybk", "")] = sec_cnt.get(it.get("hybk", ""), 0) + 1
    tiers = {"S": [], "A": [], "B": [], "C": []}
    for it in pool:
        it["dragon_score"] = dragon_score(it, sec_cnt)
        it["tier"] = tier_of(it["dragon_score"], it.get("lbc") or 1)
        tiers[it["tier"]].append(it)
    for k in tiers:
        tiers[k].sort(key=lambda x: -x["dragon_score"])
    return tiers


def load_zt_cache():
    try:
        with open(ZT_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"date": "", "pool": []}


def save_zt_cache(obj):
    import os
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ZT_CACHE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def find_break_low(today_pool, yest_pool, min_lbc=2, max_rank=10):
    """
    断板低吸候选：昨日涨停池中连板≥2 的股票，今日不在涨停池（断板）。

    返回 [{code,name,prev_lbc,prev_score,hybk}, ...]
    注意：断板≠必跌，这里只做候选池，结合支撑/守住率再过滤（见 monitor 集成）。
    """
    today_codes = {it["code"] for it in today_pool}
    # 昨日池也按强度分排序（断板候选按昨日强度排序）
    yest_tiers = build_tiers(yest_pool)
    score_map = {it["code"]: it["dragon_score"] for it in yest_tiers["S"] + yest_tiers["A"] + yest_tiers["B"] + yest_tiers["C"]}
    cands = []
    for it in yest_pool:
        if (it.get("lbc") or 1) >= min_lbc and it["code"] not in today_codes:
            cands.append({
                "code": it["code"],
                "name": it["name"],
                "prev_lbc": it.get("lbc"),
                "prev_score": score_map.get(it["code"], 0),
                "hybk": it.get("hybk", ""),
                "yest_price": it.get("price"),
            })
    cands.sort(key=lambda x: -x["prev_score"])
    return cands[:max_rank]


if __name__ == "__main__":
    import sys
    date = sys.argv[1] if len(sys.argv) > 1 else None
    pool = fetch_zt_pool(date)
    print(f"涨停池: {len(pool)} 只")
    tiers = build_tiers(pool)
    for k in ("S", "A", "B"):
        print(f"\n[{k}级 {len(tiers[k])}只]")
        for it in tiers[k][:8]:
            print(f"  {it['name']}({it['code']}) {it['lbc']}板 分{it['dragon_score']} 封单{it['fund']/1e8:.1f}亿 首板{it['fbt']} 炸{it['zbc']} {it['hybk']}")
    if len(sys.argv) > 2:
        y = fetch_zt_pool(sys.argv[2])
        print(f"\n断板低吸候选(昨日涨停{len(y)}只):")
        for c in find_break_low(pool, y):
            print(f"  {c['name']}({c['code']}) 昨{c['prev_lbc']}板 分{c['prev_score']} {c['hybk']}")


# ═══════════════════════════════════════════
# 情绪周期温度计（2026-08-26 新增）
# ═══════════════════════════════════════════

def sentiment_state(zt_count):
    """按当日涨停家数划分情绪状态"""
    if zt_count < 30:
        return "冰点"
    if zt_count < 50:
        return "回暖"
    if zt_count < 80:
        return "活跃"
    return "高潮"


def max_lbc_of(pool):
    """涨停池最高连板数（0=无涨停）"""
    return max((it.get("lbc") or 1) for it in pool) if pool else 0


def fetch_zt_history(trading_days=40, gap=1):
    """
    拉最近 N 个交易日的涨停家数历史（东财接口逐日请求，带缓存）。

    返回 [{date:'YYYYMMDD', zt_count, max_lbc}, ...]（时间升序）
    gap: 每次回退天数（节假日时自动跳过空池）
    """
    cache = load_zt_cache()
    hist = cache.get("history", [])
    have = {h["date"] for h in hist}

    today = datetime.date.today()
    d = today
    fetched = 0
    while fetched < trading_days and d > today - datetime.timedelta(days=gap * trading_days * 2):
        ds = d.strftime("%Y%m%d")
        if ds not in have:
            pool = fetch_zt_pool(ds)
            if pool:
                hist.append({"date": ds, "zt_count": len(pool), "max_lbc": max_lbc_of(pool)})
                have.add(ds)
                fetched += 1
                time.sleep(0.25)
            # 空池 = 非交易日/休市，继续往前退
        elif any(h["date"] == ds for h in hist):
            fetched += 1
        d -= datetime.timedelta(days=gap)
        if fetched >= trading_days:
            break

    hist.sort(key=lambda h: h["date"])
    # 只保留最近 trading_days 条
    hist = hist[-trading_days:]
    cache["history"] = hist
    save_zt_cache(cache)
    return hist


def sentiment_report(today_pool=None, trading_days=40):
    """
    生成情绪周期报告：
      {today: {date, zt_count, max_lbc, state},
       history: [...],
       trend: {zt5, zt20, rising, desc}}
    """
    hist = fetch_zt_history(trading_days=trading_days)
    if today_pool is None:
        today_pool = fetch_zt_pool(datetime.date.today().strftime("%Y%m%d"))
    today = datetime.date.today().strftime("%Y%m%d")
    tc = len(today_pool)
    ml = max_lbc_of(today_pool)

    # 5日 vs 20日 平均涨停家数 → 升温/降温
    counts = [h["zt_count"] for h in hist]
    zt5 = round(sum(counts[-5:]) / min(5, len(counts)), 1) if counts else 0
    zt20 = round(sum(counts[-20:]) / min(20, len(counts)), 1) if len(counts) >= 5 else zt5
    rising = zt5 > zt20 * 1.1
    if rising:
        trend_desc = f"情绪升温（5日均{zt5} vs 20日均{zt20}）"
    elif zt5 < zt20 * 0.9:
        trend_desc = f"情绪降温（5日均{zt5} vs 20日均{zt20}）"
    else:
        trend_desc = f"情绪平稳（5日均{zt5} vs 20日均{zt20}）"

    return {
        "today": {"date": today, "zt_count": tc, "max_lbc": ml, "state": sentiment_state(tc)},
        "history": hist,
        "trend": {"zt5": zt5, "zt20": zt20, "rising": rising, "desc": trend_desc},
    }
