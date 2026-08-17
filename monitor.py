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
  python monitor.py                # 完整模式：检测 + 微信实时推送 + 日报累积（本机用）
  python monitor.py --fast         # 快扫：跳过回测/资金流缓存，供本机每分钟盯盘
  python monitor.py --data-only    # 纯数据：只更新看板数据，不推送不落 state（GitHub Actions 用）
  python monitor.py --force        # 本地调试用，忽略当天去重

推送机制（v2）：
  - S/A 级信号 → 逐条 Server酱微信推送（实时，不攒批、不发邮件）
  - 所有级别（S/A/B/C）→ 累积到 data/digest.json，收盘后由 digest.py 一封邮件汇总
  - 邮件只在收盘后发一封，盘中不再发邮件
"""

import os
import sys
import json
import datetime
import time
import urllib.request

import quant
import backtest
import sector
import support_resistance
import signals

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

SNAPSHOT_PATH = os.path.join(DATA_DIR, "snapshot.json")
ALERTS_PATH = os.path.join(DATA_DIR, "alerts.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
DIGEST_PATH = os.path.join(DATA_DIR, "digest.json")
INTRADAY_DIR = os.path.join(DATA_DIR, "intraday")
INTRADAY_COOLDOWN_MINUTES = 30

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
            "turnover_rate": _to_float(parts[38]) if len(parts) > 38 else None,  # 换手率%
            "pe": _to_float(parts[39]) if len(parts) > 39 else None,            # 市盈率
            "float_mktcap": _to_float(parts[44]) if len(parts) > 44 else None,  # 流通市值(亿)
            "total_mktcap": _to_float(parts[45]) if len(parts) > 45 else None,  # 总市值(亿)
            "pb": _to_float(parts[46]) if len(parts) > 46 else None,            # 市净率
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


def fetch_moneyflow(code, market):
    """新浪主力资金流，返回 {date, netamount(元), r0_net(元), turnover, change_pct} 或 None"""
    symbol = f"{market}{code}"
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"MoneyFlow.ssl_qsfx_zjlrqs?page=1&num=1&sort=opendate&asc=0&daima={symbol}")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", errors="replace"))
    if not data or not isinstance(data, list):
        return None
    d = data[0]
    cr = d.get("changeratio")
    return {
        "date": d.get("opendate"),
        "netamount": _to_float(d.get("netamount")),       # 主力净流入(元)
        "r0_net": _to_float(d.get("r0_net")),             # 特大单净流入(元)
        "change_pct": round(cr * 100, 2) if isinstance(cr, (int, float)) else None,
    }


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


def detect_intraday_alerts(intraday, rules):
    """检测分时（盘中）异动，返回 [(规则key, 描述), ...]"""
    if not intraday:
        return []
    minutes = intraday.get("minutes") or []
    if len(minutes) < 3:
        return []
    alerts = []
    last = minutes[-1]
    last_price = last.get("p")

    # 1. 盘中急拉/急跌：最近 N 分钟涨跌幅
    n = int(rules.get("intraday_spike_minutes", 5))
    pct = rules.get("intraday_spike_pct")
    if pct and last_price is not None and len(minutes) > n:
        base_price = minutes[-1 - n].get("p")
        if base_price:
            change = (last_price - base_price) / base_price * 100
            if change >= pct:
                alerts.append(("intraday_spike_up", f"⚡ 盘中急拉：{n}分钟涨 {change:.2f}%（阈值 {pct}%）"))
            elif change <= -pct:
                alerts.append(("intraday_spike_down", f"🌊 盘中急跌：{n}分钟跌 {abs(change):.2f}%（阈值 {pct}%）"))

    # 2. 盘中放量
    vr = rules.get("intraday_volume_ratio")
    vm = int(rules.get("intraday_volume_minutes", 5))
    if vr and len(minutes) > vm:
        cur_vol = last.get("v") or 0
        prev_vols = [m.get("v") or 0 for m in minutes[-1 - vm:-1]]
        avg = sum(prev_vols) / len(prev_vols) if prev_vols else 0
        if avg > 0 and cur_vol >= vr * avg:
            alerts.append(("intraday_volume", f"📊 盘中放量：单分钟量 {cur_vol:.0f} 手，为前{vm}分钟均量 {avg:.0f} 的 {cur_vol / avg:.1f} 倍（阈值 {vr}）"))

    # 3. 突破日内新高/新低
    if rules.get("intraday_break_high_low") and last_price is not None:
        prev_prices = [m.get("p") for m in minutes[:-1] if m.get("p") is not None]
        if prev_prices:
            day_high = max(prev_prices)
            day_low = min(prev_prices)
            if last_price > day_high:
                alerts.append(("intraday_break_high", f"🚀 突破日内新高 {day_high:.2f}"))
            elif last_price < day_low:
                alerts.append(("intraday_break_low", f"⚠️ 跌破日内新低 {day_low:.2f}"))

    return alerts


def is_duplicate(state, dedup_key, rule_type, now, force):
    """日线规则按天去重；分时规则按冷却时间（30分钟）去重"""
    if force:
        return False
    last = state.get(dedup_key)
    if not last:
        return False
    try:
        last_dt = datetime.datetime.fromisoformat(str(last))
    except (ValueError, TypeError):
        try:
            last_dt = datetime.datetime.strptime(str(last), "%Y-%m-%d")
        except (ValueError, TypeError):
            return False
    if rule_type == "intraday":
        return (now - last_dt).total_seconds() < INTRADAY_COOLDOWN_MINUTES * 60
    return last_dt.date() == now.date()


# 提醒重要性分级：S=核心(回测验证) A=重要 B=预警 C=参考
ALERT_TIERS = {
    "quant_strong": "S", "quant_weak": "S",
    "break_support": "A", "break_resistance": "A",
    "break_high": "A", "break_low": "A",
    "moneyflow_in": "A", "moneyflow_out": "A",
    "sector_anomaly": "B",
    "near_支撑": "B", "near_压力": "B",
    "rsi_overbought": "B", "rsi_oversold": "B",
    "intraday_spike_up": "B", "intraday_spike_down": "B",
}


def tier_for(rule_key):
    return ALERT_TIERS.get(rule_key, "C")


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


def send_wechat(title, desp):
    """Server酱 微信推送，凭据从环境变量 SERVERCHAN_KEY 读取"""
    key = os.environ.get("SERVERCHAN_KEY")
    if not key:
        print("[wechat] 未配置 SERVERCHAN_KEY，跳过")
        return False
    import urllib.parse
    url = f"https://sctapi.ftqq.com/{key}.send"
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8"))
        if resp.get("code") == 0:
            print("[wechat] 微信已推送")
            return True
        print(f"[wechat] 推送失败: {resp}")
        return False
    except Exception as e:
        print(f"[wechat] 推送异常: {e}")
        return False


def is_trading_time(now):
    """A股交易时段（含盘前盘后缓冲）：周一至周五 9:20-11:40 / 12:50-15:10"""
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 20 <= t <= 11 * 60 + 40) or (12 * 60 + 50 <= t <= 15 * 60 + 10)


def main():
    args = sys.argv[1:]
    force = "--force" in args
    fast = "--fast" in args
    data_only = "--data-only" in args
    config = load_json(CONFIG_PATH, {})
    watchlist = config.get("watchlist", [])
    rules = config.get("rules", {})
    history_days = config.get("history_days", 60)

    if not watchlist:
        print("config.json 的 watchlist 为空，请先添加自选股")
        return

    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")

    # 非交易时段 + 非 force：仍然更新看板数据，但跳过推送/日报（避免盘后噪音）
    push_enabled = (not data_only) and (is_trading_time(now) or force)
    if data_only:
        print("[mode] data-only：仅更新看板数据，不推送")
    elif fast:
        print("[mode] fast：快扫模式（跳过回测/资金流缓存）")

    print(f"[fetch] 拉取 {len(watchlist)} 只自选股行情 ...")
    quotes = fetch_quotes(watchlist)

    state = load_json(STATE_PATH, {})
    alerts_log = load_json(ALERTS_PATH, {"updated_at": "", "items": []})
    triggered = []
    quant_results = []
    money_results = []
    sr_results = []
    sig_results = []

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

        sr = support_resistance.compute_levels(history)
        sr_results.append({"code": code, "name": stock["name"], "price": quote.get("price"), **sr})

        intraday = None
        try:
            intraday = fetch_intraday(code, stock["market"])
            save_json(os.path.join(INTRADAY_DIR, f"{code}.json"), intraday)
        except Exception as e:
            print(f"[warn] {code} 拉取分时失败: {e}")
            intraday = load_json(os.path.join(INTRADAY_DIR, f"{code}.json"), None)

        sig = signals.compute_signals(history, intraday)
        sig_results.append({"code": code, "name": stock["name"], "price": quote.get("price"), **sig})

        alerts = detect_alerts(quote, history, rules)
        alerts += detect_intraday_alerts(intraday, rules)

        mf = None
        try:
            # fast 模式：资金流 30 分钟缓存（新浪接口，避免每分钟拉爆）
            mf_cached = None
            if fast:
                mf_file = load_json(os.path.join(DATA_DIR, "moneyflow.json"), None)
                if mf_file and str(mf_file.get("updated_at", "")).startswith(today):
                    try:
                        ts = datetime.datetime.strptime(mf_file["updated_at"], "%Y-%m-%d %H:%M:%S")
                        if (now - ts).total_seconds() < 1800:
                            mf_cached = mf_file.get("stocks", [])
                    except (ValueError, TypeError):
                        pass
            if mf_cached is not None:
                hit = next((x for x in mf_cached if x.get("code") == code), None)
                if hit:
                    mf = {
                        "date": hit.get("date"), "netamount": hit.get("netamount"),
                        "r0_net": hit.get("r0_net"), "change_pct": hit.get("change_pct"),
                    }
            else:
                mf = fetch_moneyflow(code, stock["market"])
                if mf:
                    money_results.append({"code": code, "name": stock["name"], **mf})
        except Exception as e:
            print(f"[warn] {code} 资金流失败: {e}")
        mf_th = rules.get("moneyflow_threshold", 50000000)
        if mf and mf.get("netamount") is not None:
            if mf["netamount"] >= mf_th:
                alerts.append(("moneyflow_in", f"💰 主力净流入 {mf['netamount'] / 10000:.0f} 万元"))
            elif mf["netamount"] <= -mf_th:
                alerts.append(("moneyflow_out", f"💸 主力净流出 {abs(mf['netamount']) / 10000:.0f} 万元"))

        # 支撑压力位提醒
        if len(history) >= 2:
            prev_close = history[-2]["close"]
            close = history[-1]["close"]
            for lvl in sr["resistances"]:
                if prev_close < lvl["price"] <= close:
                    alerts.append(("break_resistance", f"🚀 突破压力位 {lvl['price']}（{lvl['strength']}）"))
            for lvl in sr["supports"]:
                if prev_close > lvl["price"] >= close:
                    alerts.append(("break_support", f"⚠️ 跌破支撑位 {lvl['price']}（{lvl['strength']}）"))
            for lvl in sr["supports"] + sr["resistances"]:
                if lvl["strength"] == "强" and 0.1 < abs(lvl["distance_pct"]) <= 1.5:
                    kind = "支撑" if lvl["distance_pct"] < 0 else "压力"
                    alerts.append((f"near_{kind}", f"👀 逼近{kind}位 {lvl['price']}（强）"))

        # 分时买点/卖点提醒（2026-08-17 已移除："接近日内高低点"过于粗糙、几乎每天触发，纯噪音）
        # 日线买卖点仍由 signals.py 计算并展示在看板（支撑=买点、压力=卖点）

        ind, fac = quant.compute_factors(history)
        score = quant.compute_score(fac)
        signal, sig_key = quant.signal_from_score(score)
        quant_results.append({
            "code": code,
            "name": stock["name"],
            "score": score,
            "signal": signal,
            "signal_key": sig_key,
            "factors": fac,
            "indicators": ind,
        })
        if sig_key == "strong":
            alerts.append(("quant_strong", f"🟢 超跌反弹机会（评分 {score:.0f} 分）"))
        elif sig_key == "weak":
            alerts.append(("quant_weak", f"🔴 高位回调风险（评分 {score:.0f} 分）"))

        for rule_key, msg in alerts:
            rule_type = "intraday" if rule_key.startswith("intraday_") else "daily"
            dedup_key = f"{code}:{rule_key}"
            if is_duplicate(state, dedup_key, rule_type, now, force):
                continue
            state[dedup_key] = now.isoformat()
            triggered.append({
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "code": code,
                "name": stock["name"],
                "rule": rule_key,
                "message": msg,
                "price": quote.get("price"),
                "change_pct": quote.get("change_pct"),
            })

    save_json(os.path.join(DATA_DIR, "quant.json"), {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "stocks": quant_results})

    save_json(os.path.join(DATA_DIR, "support_resistance.json"), {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "stocks": sr_results})
    save_json(os.path.join(DATA_DIR, "signals.json"), {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "stocks": sig_results})

    if money_results:
        save_json(os.path.join(DATA_DIR, "moneyflow.json"), {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "stocks": money_results})

    # 板块分析（30分钟缓存，避免频繁拉取新浪）
    stock_sector = {}
    sector_anomalies = []
    sectors = []
    sector_data = load_json(os.path.join(DATA_DIR, "sectors.json"), None)
    need_sector_fetch = True
    if sector_data and sector_data.get("fetched_ts"):
        try:
            if now.timestamp() - float(sector_data["fetched_ts"]) < 1800:
                sectors = sector_data.get("sectors", [])
                sector_anomalies = sector_data.get("anomalies", [])
                stock_sector = sector_data.get("stock_sector", {})
                need_sector_fetch = False
        except (ValueError, TypeError):
            pass
    if need_sector_fetch:
        try:
            sectors, sector_anomalies, stock_sector = sector.analyze_sectors(threshold=rules.get("sector_threshold", 2.0))
            save_json(os.path.join(DATA_DIR, "sectors.json"), {
                "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "fetched_ts": now.timestamp(),
                "sectors": sectors,
                "anomalies": sector_anomalies,
                "stock_sector": stock_sector,
            })
        except Exception as e:
            print(f"[warn] 板块分析失败: {e}")

    for sa in sector_anomalies:
        dedup_key = f"sector:{sa['name']}"
        if not is_duplicate(state, dedup_key, "daily", now, force):
            state[dedup_key] = now.isoformat()
            emoji = "📈" if sa["avg_change"] > 0 else "📉"
            related = [s["name"] for s in watchlist if stock_sector.get(s["code"]) == sa["name"]]
            msg = f"{emoji} 板块异动：{sa['name']} {sa['avg_change']:+.2f}%"
            if related:
                msg += f"（自选：{'、'.join(related)}）"
            triggered.append({
                "time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "code": "板块",
                "name": sa["name"],
                "rule": "sector_anomaly",
                "message": msg,
                "price": None,
                "change_pct": sa["avg_change"],
            })

    # 回测（fast 模式跳过，避免每分钟跑；data-only 保留给看板）
    if not fast:
        try:
            bt = backtest.run()
            print(f"[backtest] 样本 {bt['total_samples']} 个，IC {bt.get('ic')}")
        except Exception as e:
            print(f"[warn] 回测失败: {e}")

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
            "turnover_rate": q.get("turnover_rate"),
            "pe": q.get("pe"),
            "pb": q.get("pb"),
            "float_mktcap": q.get("float_mktcap"),
            "total_mktcap": q.get("total_mktcap"),
        })
    save_json(SNAPSHOT_PATH, snapshot)

    # 更新告警日志（保留最近 200 条）
    if triggered:
        items = triggered + alerts_log.get("items", [])
        alerts_log["items"] = items[:200]
        alerts_log["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        save_json(ALERTS_PATH, alerts_log)

    # data-only 模式：不写 state、不推送、不累积日报（状态归属推送方）
    if data_only:
        print(f"[done] data-only 更新 {len(snapshot['quotes'])} 只，触发 {len(triggered)} 条（仅看板）")
        return

    save_json(STATE_PATH, state)

    # 分级推送 v2（2026-08-17 降噪）：
    #   S/A 级 → 逐条微信实时推送（不攒批、不发邮件）
    #   S/A/B 级 → 累积到 digest.json，收盘后 digest.py 一封邮件汇总
    #   C 级（涨跌幅/量比等）→ 只进看板 alerts.json，不推送不进日报
    immediate = [t for t in triggered if tier_for(t["rule"]) in ("S", "A")]
    digest_items = [dict(t, tier=tier_for(t["rule"])) for t in triggered if tier_for(t["rule"]) in ("S", "A", "B")]
    c_count = sum(1 for t in triggered if tier_for(t["rule"]) == "C")

    if digest_items:
        digest = load_json(DIGEST_PATH, {"items": []})
        merged = digest.get("items", []) + digest_items
        seen = {}
        for it in merged:
            seen[f"{it.get('code')}:{it.get('rule')}"] = it
        digest["items"] = list(seen.values())[-500:]
        digest["c_count"] = digest.get("c_count", 0) + c_count
        save_json(DIGEST_PATH, digest)

    print(f"[done] 更新 {len(snapshot['quotes'])} 只，触发 {len(triggered)} 条（S/A {len(immediate)} 实时推送，日报 {len(digest_items)}，C级参考 {c_count}）")

    if immediate and push_enabled:
        # 每个信号单独发一条短微信，保证手表/手环能完整显示（每条间隔3秒）
        for i, t in enumerate(immediate):
            tier = tier_for(t["rule"])
            emoji = "🔴" if tier == "S" else "🟠"
            title = f"{emoji} {t['name']} {t['message']}"
            desp = f"{t['time']} {t['name']}({t['code']}) {t['message']}"
            send_wechat(title, desp)
            if i < len(immediate) - 1:
                time.sleep(3)
        print(f"[push] 已实时推送 {len(immediate)} 条微信（邮件由收盘日报统一汇总）")
    elif immediate:
        print(f"[push] 非交易时段，S/A {len(immediate)} 条未推微信（已进日报，收盘统一汇总）")
    else:
        print(f"本次无 S/A 级信号（B级进日报，C级仅看板）")


if __name__ == "__main__":
    main()
