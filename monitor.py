#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股盯盘助手 —— 主脚本
功能：
  1. 从腾讯行情免费接口拉取自选股实时行情 + 前复权日K历史
  2. 按 config.json 里的规则做异动检测（涨跌幅 / 新高新低 / 量比 / RSI）
  3. 触发异动时通过 QQ 邮箱 SMTP 发送提醒（凭据走环境变量，不落盘）
  4. 把快照、告警、历史数据写到 data/ 目录，供 GitHub Pages 前端渲染

依赖：仅 Python 标准库，无需 pip install。

用法：
  python monitor.py            # 正常跑（同一天同规则去重）
  python monitor.py --force    # 本地调试用，忽略当天去重
"""

import os
import sys
import json
import datetime
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

SNAPSHOT_PATH = os.path.join(DATA_DIR, "snapshot.json")
ALERTS_PATH = os.path.join(DATA_DIR, "alerts.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
INTRADAY_DIR = os.path.join(DATA_DIR, "intraday")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _to_float(s):
    """字符串转 float，失败返回 None"""
    if s is None or s == "" or s == "-":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def http_get(url, encoding="utf-8"):
    """GET 请求，返回解码后的文本"""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode(encoding, errors="replace")


def fetch_quotes(watchlist):
    """
    批量拉取实时行情（腾讯，一次请求拿全部）。
    返回 {code: {...}}
    """
    symbols = [f"{s['market']}{s['code']}" for s in watchlist]
    text = http_get("https://qt.gtimg.cn/q=" + ",".join(symbols), encoding="gbk")

    quotes = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        _, _, payload = line.partition("=")
        parts = payload.strip().strip('"').split("~")
        if len(parts) < 50:
            continue
        code = parts[2]
        amount_wan = _to_float(parts[37])  # 成交额，单位：万元
        quotes[code] = {
            "name": parts[1],
            "price": _to_float(parts[3]),
            "prev_close": _to_float(parts[4]),
            "open": _to_float(parts[5]),
            "volume": _to_float(parts[6]),       # 手
            "change": _to_float(parts[31]),
            "change_pct": _to_float(parts[32]),  # %
            "high": _to_float(parts[33]),
            "low": _to_float(parts[34]),
            "amount": amount_wan * 10000 if amount_wan is not None else None,  # 元
        }
    return quotes


def fetch_history(code, market, days=60):
    """
    拉取前复权日K（腾讯），返回 [{date,open,close,high,low,volume}, ...]
    最后一根是“今天”（盘中为当日实时K线）。
    """
    symbol = f"{market}{code}"
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
        f"{symbol},day,,,{days},qfq"
    )
    data = json.loads(http_get(url))
    node = (data.get("data") or {}).get(symbol) or {}
    klines = node.get("qfqday") or node.get("day") or []
    out = []
    for k in klines:
        if len(k) < 6:
            continue
        out.append({
            "date": k[0],
            "open": _to_float(k[1]),
            "close": _to_float(k[2]),
            "high": _to_float(k[3]),
            "low": _to_float(k[4]),
            "volume": _to_float(k[5]),
        })
    return out


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
    prev_close = _to_float(qt[4]) if len(qt) > 4 else None

    minutes = []
    prev_vol = 0.0
    for m in raw:
        parts = m.split()
        if len(parts) < 4:
            continue
        price = _to_float(parts[1])
        cum_vol = _to_float(parts[2]) or 0.0
        cum_amt = _to_float(parts[3])
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


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def calc_rsi(closes, period=14):
    """简单移动平均 RSI"""
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


def detect_alerts(quote, history, rules):
    """检测异动，返回 [(规则key, 描述), ...]"""
    alerts = []

    # 1. 涨跌幅
    cp = quote.get("change_pct")
    th = rules.get("change_pct")
    if cp is not None and th:
        if cp >= th:
            alerts.append(("change_pct_up", f"📈 涨幅达 {cp:.2f}%（阈值 {th}%）"))
        elif cp <= -th:
            alerts.append(("change_pct_down", f"📉 跌幅达 {cp:.2f}%（阈值 {th}%）"))

    if not history:
        return alerts

    # 历史最后一根是“今天”（盘中为当日实时K线），做 N 日比较时排除今天
    prior = history[:-1]
    closes = [h["close"] for h in history]

    # 2. 突破 N 日新高 / 新低
    n = rules.get("break_high_days")
    if n and len(prior) >= n:
        prev_high = max(h["high"] for h in prior[-n:])
        price = quote.get("price")
        if price is not None and prev_high and price > prev_high:
            alerts.append(("break_high", f"🚀 突破 {n} 日新高（前高 {prev_high:.2f}）"))

    n = rules.get("break_low_days")
    if n and len(prior) >= n:
        prev_low = min(h["low"] for h in prior[-n:])
        price = quote.get("price")
        if price is not None and prev_low and price < prev_low:
            alerts.append(("break_low", f"⚠️ 跌破 {n} 日新低（前低 {prev_low:.2f}）"))

    # 3. 量比（当前量 vs 过去 5 日均量）
    vr = rules.get("volume_ratio")
    if vr and len(prior) >= 5:
        avg_vol = sum(h["volume"] for h in prior[-5:]) / 5
        cur_vol = quote.get("volume")
        if avg_vol and cur_vol is not None:
            ratio = cur_vol / avg_vol
            if ratio >= vr:
                alerts.append(("volume_ratio", f"📊 放量，量比 {ratio:.2f}（阈值 {vr}）"))

    # 4. RSI 超买 / 超卖
    rsi = calc_rsi(closes)
    if rsi is not None:
        ob = rules.get("rsi_overbought")
        os_ = rules.get("rsi_oversold")
        if ob and rsi >= ob:
            alerts.append(("rsi_overbought", f"🔴 RSI 超买 {rsi:.1f}（阈值 {ob}）"))
        if os_ and rsi <= os_:
            alerts.append(("rsi_oversold", f"🟢 RSI 超卖 {rsi:.1f}（阈值 {os_}）"))

    return alerts


def send_email(subject, body):
    """QQ 邮箱 SMTP 发信，凭据从环境变量读取"""
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    to = os.environ.get("SMTP_TO")
    if not (user and pw and to):
        print("[notify] 未配置 SMTP_USER/SMTP_PASS/SMTP_TO，跳过发信")
        return False

    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as server:
            server.login(user, pw)
            server.sendmail(user, [to], msg.as_string())
        print("[notify] 邮件已发送")
        return True
    except Exception as e:
        print(f"[notify] 发信失败: {e}")
        return False


def main():
    force = "--force" in sys.argv
    config = load_json(CONFIG_PATH, {})
    watchlist = config.get("watchlist", [])
    rules = config.get("rules", {})
    history_days = config.get("history_days", 60)

    if not watchlist:
        print("config.json 的 watchlist 为空，请先添加自选股")
        return

    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")

    print(f"[fetch] 拉取 {len(watchlist)} 只自选股行情 ...")
    quotes = fetch_quotes(watchlist)

    state = load_json(STATE_PATH, {})
    alerts_log = load_json(ALERTS_PATH, {"updated_at": "", "items": []})
    triggered = []

    for stock in watchlist:
        code = stock["code"]
        quote = quotes.get(code)
        if not quote:
            print(f"[warn] 未取到 {code} 行情，跳过")
            continue

        history = []
        try:
            history = fetch_history(code, stock["market"], history_days)
            save_json(os.path.join(HISTORY_DIR, f"{code}.json"), history)
        except Exception as e:
            print(f"[warn] {code} 拉取历史失败，用缓存: {e}")
            history = load_json(os.path.join(HISTORY_DIR, f"{code}.json"), [])

        try:
            intraday = fetch_intraday(code, stock["market"])
            save_json(os.path.join(INTRADAY_DIR, f"{code}.json"), intraday)
        except Exception as e:
            print(f"[warn] {code} 拉取分时失败: {e}")

        for rule_key, msg in detect_alerts(quote, history, rules):
            dedup_key = f"{code}:{rule_key}"
            if not force and state.get(dedup_key) == today:
                continue  # 今天已提醒过，去重
            state[dedup_key] = today
            triggered.append({
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "code": code,
                "name": stock["name"],
                "rule": rule_key,
                "message": msg,
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
            })

    # 落盘快照
    snapshot = {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "quotes": []}
    for s in watchlist:
        q = quotes.get(s["code"]) or {}
        snapshot["quotes"].append({
            "code": s["code"],
            "market": s["market"],
            "name": s["name"],
            "price": q.get("price"),
            "change_pct": q.get("change_pct"),
            "change": q.get("change"),
            "volume": q.get("volume"),
            "amount": q.get("amount"),
            "high": q.get("high"),
            "low": q.get("low"),
            "open": q.get("open"),
            "prev_close": q.get("prev_close"),
        })
    save_json(SNAPSHOT_PATH, snapshot)

    # 更新告警日志（保留最近 200 条）
    if triggered:
        items = triggered + alerts_log.get("items", [])
        alerts_log["items"] = items[:200]
        alerts_log["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        save_json(ALERTS_PATH, alerts_log)

    save_json(STATE_PATH, state)

    # 打印 + 发信
    print(f"[done] 更新 {len(snapshot['quotes'])} 只，触发 {len(triggered)} 条异动")
    if triggered:
        lines = [f"{t['time']}  {t['name']}({t['code']})  {t['message']}" for t in triggered]
        body = "你的自选股出现异动：\n\n" + "\n".join(lines)
        print(body)
        send_email(f"[盯盘提醒] {len(triggered)} 只自选股异动", body)
    else:
        print("本次无触发")


if __name__ == "__main__":
    main()
