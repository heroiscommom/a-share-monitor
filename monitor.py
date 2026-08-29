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
from concurrent.futures import ThreadPoolExecutor

import quant
import backtest
import sector
import support_resistance
import signals
import zone_history
import strategy_index

from common import DATA_DIR, HISTORY_DIR, INTRADAY_DIR, CONFIG_PATH, load_json, save_json, market_of
import datafeed
from notify import send_wechat, is_trading_time

SNAPSHOT_PATH = os.path.join(DATA_DIR, "snapshot.json")
ALERTS_PATH = os.path.join(DATA_DIR, "alerts.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
DIGEST_PATH = os.path.join(DATA_DIR, "digest.json")
INTRADAY_COOLDOWN_MINUTES = 30


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
    rsi = quant.calc_rsi(closes)
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
    "momentum_strong": "S",
    "break_support": "A", "break_resistance": "A",
    "break_high": "A", "break_low": "A",
    "moneyflow_in": "A", "moneyflow_out": "A",
    "sector_anomaly": "B",
    "strategy_resonance": "A",
    "near_支撑": "B", "near_压力": "B",
    "rsi_overbought": "B", "rsi_oversold": "B",
    "intraday_spike_up": "B", "intraday_spike_down": "B",
}


def tier_for(rule_key):
    return ALERT_TIERS.get(rule_key, "C")


def quant_tier_by_regime(regime):
    """超跌机会（均值回归）：下跌市最强(回测胜率59.8%)，震荡市可用，上涨市失效(46.4%)"""
    return {"下跌": "S", "震荡": "A", "上涨": "B"}.get(regime, "S")


def momentum_tier_by_regime(regime):
    """强势突破（动量）：上涨市有效(52.5%)，震荡/下跌失效(下跌市仅47.4%)"""
    return {"上涨": "S", "震荡": "B", "下跌": "B"}.get(regime, "B")


def effective_tier(rule_key, regime):
    if rule_key == "quant_strong":
        return quant_tier_by_regime(regime)
    if rule_key == "momentum_strong":
        return momentum_tier_by_regime(regime)
    return tier_for(rule_key)


def get_market_regime(history_days):
    """市场状态（沪深300 20日趋势+波动率）"""
    regime = {"regime": "未知", "mom20": None, "vol20": None, "desc": "指数数据不可用"}
    try:
        idx = datafeed.fetch_index(max(history_days, 250))
        regime = quant.market_regime(idx.get("closes") or [])
    except Exception as e:
        print(f"[warn] 市场状态获取失败: {e}")
    print(f"[regime] {regime['desc']}")
    return regime


def process_stocks(watchlist, quotes, rules, history_days, fast, now, regime, force,
                   state, triggered, quant_results, sr_results, sig_results, money_results):
    """
    处理全部自选股：历史/支撑压力/分时/资金流/评分 → 更新各结果列表并触发告警。
    线程池并行（IO 密集）：单轮 90+ 个 HTTP 请求从 1~2 分钟降到 ~30 秒，
    让 5 分钟周期的数据更新更接近实时。共享列表的 append 为原子操作；
    quant_results 改为「就地构造 item 再一次性 append」，避免跨线程竞争 [-1]。
    """
    today = now.strftime("%Y-%m-%d")

    def one(stock):
        code = stock["code"]
        quote = quotes.get(code)
        if not quote:
            print(f"[warn] 未取到 {code} 行情，跳过")
            return

        history = []
        try:
            history = datafeed.fetch_history(code, stock["market"], history_days)
            save_json(os.path.join(HISTORY_DIR, f"{code}.json"), history)
        except Exception as e:
            print(f"[warn] {code} 拉取历史失败，用缓存: {e}")
            history = load_json(os.path.join(HISTORY_DIR, f"{code}.json"), [])

        sr = support_resistance.compute_levels(history)
        # 决策闭环 v2：每个支撑/压力位的历史守住率 + 触及预警 + 风险评分
        try:
            ctx = zone_history.build_zone_context(history, sr)
            hr_map = {}
            for side in ("supports", "resistances"):
                for h in ctx["zone_history"].get(side, []):
                    hr_map[h["price"]] = h
            for side in ("supports", "resistances"):
                for lvl in sr[side]:
                    h = hr_map.get(round(lvl["price"], 2))
                    if h:
                        lvl["held_rate"] = h["held_rate"]
                        lvl["touch"] = h["touch"]
                        lvl["confidence"] = h["confidence"]
            sr["risk"] = ctx["risk"]
            sr["alerts"] = ctx["alerts"]
        except Exception as e:
            print(f"[warn] 决策闭环计算失败: {e}")
        sr_results.append({"code": code, "name": stock["name"], "price": quote.get("price"), **sr})

        intraday = None
        try:
            intraday = datafeed.fetch_intraday(code, stock["market"])
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
                mf = datafeed.fetch_moneyflow(code, stock["market"])
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

        # 支撑压力位提醒（带历史守住率）
        if len(history) >= 2:
            prev_close = history[-2]["close"]
            close = history[-1]["close"]
            for lvl in sr["resistances"]:
                if prev_close < lvl["price"] <= close:
                    hr = lvl.get("held_rate")
                    hr_txt = f"（历史守住率{hr}%）" if hr is not None else ""
                    alerts.append(("break_resistance", f"🚀 突破压力位 {lvl['price']}（{lvl['strength']}{hr_txt}）"))
            for lvl in sr["supports"]:
                if prev_close > lvl["price"] >= close:
                    hr = lvl.get("held_rate")
                    hr_txt = f"（历史守住率{hr}%）" if hr is not None else ""
                    alerts.append(("break_support", f"⚠️ 跌破支撑位 {lvl['price']}（{lvl['strength']}{hr_txt}）"))
            for lvl in sr["supports"] + sr["resistances"]:
                if lvl["strength"] == "强" and 0.1 < abs(lvl["distance_pct"]) <= 1.5:
                    kind = "支撑" if lvl["distance_pct"] < 0 else "压力"
                    hr = lvl.get("held_rate")
                    hr_txt = f"（历史守住率{hr}%）" if hr is not None else ""
                    alerts.append((f"near_{kind}", f"👀 逼近{kind}位 {lvl['price']}（强{hr_txt}）"))

        # 分时买点/卖点提醒（2026-08-17 已移除："接近日内高低点"过于粗糙、几乎每天触发，纯噪音）
        # 日线买卖点仍由 signals.py 计算并展示在看板（支撑=买点、压力=卖点）

        ind, fac = quant.compute_factors(history)
        score = quant.compute_score(fac)
        signal, sig_key = quant.signal_from_score(score)
        # 动量维度（U型另一端）
        mind, mfac = quant.momentum_factors(history)
        mscore = quant.momentum_score(mfac)
        m_signal = "强势突破" if mscore >= quant.MOM_STRONG_THRESHOLD else ("偏强" if mscore >= 55 else ("中性" if mscore >= 40 else "偏弱"))
        item = {
            "code": code,
            "name": stock["name"],
            "score": score,
            "signal": signal,
            "signal_key": sig_key,
            "momentum_score": mscore,
            "momentum_signal": m_signal,
            "factors": fac,
            "indicators": ind,
            "momentum_indicators": mind,
        }
        # 新策略（P1a yaml 化配套）——就地填充 item，最后一次性 append（线程安全）
        try:
            maf = quant.ma_golden_cross_factors(history)
            item["ma_score"] = quant.ma_golden_cross_score(maf)
            item["ma_factors"] = maf
            item["ma_signal"] = ("强势金叉" if (item["ma_score"] or 0) >= 70 else
                                 ("金叉" if (item["ma_score"] or 0) >= 55 else "未金叉"))
            sf = quant.shrink_pullback_factors(history)
            item["shrink_score"] = quant.shrink_pullback_score(sf)
            item["shrink_factors"] = sf
            item["shrink_signal"] = ("缩量低吸" if (item["shrink_score"] or 0) >= 70 else
                                     ("接近" if (item["shrink_score"] or 0) >= 55 else "无"))
            # 策略共振（2026-08-27 新增）：多个策略同时看多 → 多因子确认
            res = []
            if score is not None and score >= 70:
                res.append("超跌")
            if mscore is not None and mscore >= 55:
                res.append("动量")
            if (item["ma_score"] or 0) >= 55:
                res.append("金叉")
            if (item["shrink_score"] or 0) >= 55:
                res.append("缩量")
            item["resonance"] = {"count": len(res), "list": res}
            if len(res) >= 3:
                alerts.append(("strategy_resonance", f"🔥 {len(res)}策略共振（{'+'.join(res)}），多因子确认，信号更可靠"))
        except Exception:
            pass
        quant_results.append(item)
        if sig_key == "strong":
            note = ""
            if regime["regime"] == "下跌":
                note = "（下跌市，历史胜率最高，超跌反弹窗口）"
            elif regime["regime"] == "上涨":
                note = "（⚠️ 上涨市超跌信号失效，谨慎）"
            else:
                note = "（震荡市，信号可用）"
            alerts.append(("quant_strong", f"🟢 超跌反弹机会（评分 {score:.0f} 分）{note}"))
        elif sig_key == "weak":
            alerts.append(("quant_weak", f"🔴 高位回调风险（评分 {score:.0f} 分）"))
        if mscore >= quant.MOM_STRONG_THRESHOLD:
            note = ""
            if regime["regime"] == "上涨":
                note = "（上涨市，动量有效）"
            elif regime["regime"] == "下跌":
                note = "（⚠️ 下跌市追高有风险）"
            alerts.append(("momentum_strong", f"🔥 强势突破机会（动量评分 {mscore:.0f} 分）{note}"))

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
                "tier": effective_tier(rule_key, regime["regime"]),
            })

    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="stock") as pool:
        list(pool.map(one, watchlist))

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

    # 非交易时段 + 非 force：仍然更新看板数据，但跳过推送/日报（避免盘后噪音）
    push_enabled = (not data_only) and (is_trading_time(now) or force)
    if data_only:
        print("[mode] data-only：仅更新看板数据，不推送")
    elif fast:
        print("[mode] fast：快扫模式（跳过回测/资金流缓存）")

    print(f"[fetch] 拉取 {len(watchlist)} 只自选股行情 ...")
    quotes = datafeed.fetch_quotes(watchlist)

    regime = get_market_regime(history_days)

    state = load_json(STATE_PATH, {})
    alerts_log = load_json(ALERTS_PATH, {"updated_at": "", "items": []})
    triggered = []
    quant_results = []
    money_results = []
    sr_results = []
    sig_results = []

    process_stocks(watchlist, quotes, rules, history_days, fast, now, regime, force,
                   state, triggered, quant_results, sr_results, sig_results, money_results)

    save_json(os.path.join(DATA_DIR, "quant.json"), {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "market_regime": regime,
        "stocks": quant_results,
    })

    save_json(os.path.join(DATA_DIR, "support_resistance.json"), {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "stocks": sr_results})
    save_json(os.path.join(DATA_DIR, "signals.json"), {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "stocks": sig_results})

    # 策略索引（P1a yaml 化，供前端动态渲染打法选择器）
    try:
        strategy_index.main()
    except Exception as e:
        print(f"[warn] 策略索引失败: {e}")

    run_dragon(now)

    if money_results:
        save_json(os.path.join(DATA_DIR, "moneyflow.json"), {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S"), "stocks": money_results})

    run_sector(rules, now, state, watchlist, triggered, force)

    # 回测（fast 模式跳过，避免每分钟跑；data-only 保留给看板）
    if not fast:
        try:
            bt = backtest.run()
            print(f"[backtest] 样本 {bt['total_samples']} 个，IC {bt.get('ic')}")
        except Exception as e:
            print(f"[warn] 回测失败: {e}")

    snapshot = build_snapshot(watchlist, quotes, now)
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
    push_signals(triggered, now, push_enabled)


def run_dragon(now):
    """龙头战法：涨停池 → 梯队 + 断板低吸 + 情绪温度计 → data/dragon_head.json"""
    try:
        import dragon_head as dh
        today_pool = dh.fetch_zt_pool(now.strftime("%Y%m%d"))
        yest_str = (now - datetime.timedelta(days=1)).strftime("%Y%m%d")
        yest_pool = dh.fetch_zt_pool(yest_str)
        tiers = dh.build_tiers(today_pool) if today_pool else {"S": [], "A": [], "B": [], "C": []}

        # 断板低吸：昨日连板≥2 今日断板 → 拉历史算支撑/守住率（最多10只，防慢）
        break_low = []
        for c in dh.find_break_low(today_pool, yest_pool):
            code = c["code"]
            market = market_of(code)
            try:
                chist = datafeed.fetch_history(code, market, 250)
                if len(chist) >= 60:
                    lv = support_resistance.compute_levels(chist)
                    ctx = zone_history.build_zone_context(chist, lv)
                    sup = lv["supports"][0] if lv["supports"] else None
                    c["support"] = sup["price"] if sup else None
                    # 守住率：取最近支撑对应的预警
                    sup_alerts = [a for a in ctx["alerts"] if a["side"] == "supports"]
                    if sup_alerts:
                        nearest = min(sup_alerts, key=lambda a: a["distance_pct"])
                        c["support_held"] = nearest["held_rate"]
                    c["risk_score"] = ctx["risk"]["score"]
                    c["risk_level"] = ctx["risk"]["level"]
                    c["now_price"] = chist[-1]["close"]
            except Exception:
                pass
            break_low.append(c)

        dragon = {
            "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y%m%d"),
            "zt_count": len(today_pool),
            "tiers": {k: [{"code": it["code"], "name": it["name"], "lbc": it.get("lbc"),
                             "dragon_score": it.get("dragon_score"), "fund": it.get("fund"),
                             "fbt": it.get("fbt"), "zbc": it.get("zbc"), "hybk": it.get("hybk"),
                             "hs": it.get("hs"), "price": it.get("price")} for it in v]
                       for k, v in tiers.items()},
            "break_low": break_low,
        }
        # 情绪周期温度计（涨停家数历史 + 趋势 + P2状态机：连板高度/炸板率/3日确认）
        try:
            zbc_rate = None
            if today_pool:
                zbc_rate = round(sum(1 for it in today_pool if (it.get("zbc") or 0) > 0) / len(today_pool), 3)
            dragon["sentiment"] = dh.sentiment_report(today_pool=today_pool, trading_days=40, zbc_rate=zbc_rate)
        except Exception as e:
            print(f"[warn] 情绪周期获取失败: {e}")
        save_json(os.path.join(DATA_DIR, "dragon_head.json"), dragon)
        print(f"[dragon] 涨停{len(today_pool)}只 S{len(tiers['S'])} A{len(tiers['A'])} B{len(tiers['B'])} 断板低吸{len(break_low)}只")
        if dragon.get("sentiment"):
            s = dragon["sentiment"]
            sm = s.get("state_machine") or {}
            print(f"[sentiment] {s['today']['state']}（{sm.get('direction', '')}）涨停{s['today']['zt_count']}只 最高{s['today']['max_lbc']}板 炸板率{sm.get('zbc_rate', '-')} | {s['trend']['desc']} | 仓位建议: {sm.get('position_advice', '-')}")
    except Exception as e:
        print(f"[warn] 龙头战法数据失败: {e}")


def run_sector(rules, now, state, watchlist, triggered, force):
    """板块分析（30分钟缓存）→ data/sectors.json，异动进告警列表"""
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
                "tier": "B",
            })


def build_snapshot(watchlist, quotes, now):
    """落盘快照 data/snapshot.json"""
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
    return snapshot


def push_signals(triggered, now, push_enabled):
    """分级推送 v2：S/A 微信实时 / S/A/B 进 digest 收盘邮件汇总 / C 仅看板"""
    immediate = [t for t in triggered if t.get("tier", tier_for(t["rule"])) in ("S", "A")]
    digest_items = [dict(t, tier=t.get("tier", tier_for(t["rule"]))) for t in triggered if t.get("tier", tier_for(t["rule"])) in ("S", "A", "B")]
    c_count = sum(1 for t in triggered if t.get("tier", tier_for(t["rule"])) == "C")

    if digest_items:
        digest = load_json(DIGEST_PATH, {"items": []})
        merged = digest.get("items", []) + digest_items
        seen = {}
        for it in merged:
            seen[f"{it.get('code')}:{it.get('rule')}"] = it
        digest["items"] = list(seen.values())[-500:]
        digest["c_count"] = digest.get("c_count", 0) + c_count
        save_json(DIGEST_PATH, digest)

    print(f"[done] 信号分级：S/A {len(immediate)} 实时推送，日报 {len(digest_items)}，C级参考 {c_count}")

    if immediate and push_enabled:
        # 每个信号单独发一条短微信，保证手表/手环能完整显示（每条间隔3秒）
        for i, t in enumerate(immediate):
            tier = t.get("tier", tier_for(t["rule"]))
            emoji = "🔴" if tier == "S" else "🟠"
            title = f"{emoji} {t['name']} {t['message']}"
            desp = f"{t['time']} {t['name']}({t['code']}) {t['message']}"
            send_wechat(title, desp)
            if i < len(immediate) - 1:
                time.sleep(3)
        print(f"[push] 已实时推送 {len(immediate)} 条微信（邮件由收盘日报统一汇总）")
    elif immediate:
        # 非交易时段（收盘后的补扫/迟到 run）：不逐条推，攒成单条批量补推，避免轰炸
        lines = [f"🔸 {t['name']}({t['code']}) {t['message']}" for t in immediate[:20]]
        if len(immediate) > 20:
            lines.append(f"… 共 {len(immediate)} 条")
        send_wechat(f"📋 盘后补推 {len(immediate)} 条 S/A 信号", "\n".join(lines))
        print(f"[push] 非交易时段，S/A {len(immediate)} 条已批量补推微信（单条汇总，避免轰炸）")
    else:
        print(f"本次无 S/A 级信号（B级进日报，C级仅看板）")


if __name__ == "__main__":
    main()
