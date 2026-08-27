#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动报告：回测摘要 + 持仓建议 + 市场/情绪/龙头 → 邮件推送（2026-08-26 新增）
=====================================================================
用法:
  python3 auto_report.py            # 生成报告并发送邮件（无 SMTP 凭据则只打印）
  python3 auto_report.py --dry-run  # 只打印不发送

数据来源（优先仓库内 data/，缺失时现场拉取）：
  - data/quant.json       市场状态/自选股评分
  - data/backtest.json    股票池回测结论（周级更新）
  - data/picks.json       今日选股清单（D策略）
  - data/dragon_head.json 龙头梯队 + 情绪周期
  - data/snapshot.json    自选股行情（持仓不在自选时现场拉）
  - data/history/*.json   日K（持仓不在自选时现场拉 250 天）

GitHub Actions 定时：auto-report.yml 每天 15:35（收盘后）自动执行。
"""

import os
import sys
import json
import time
import datetime
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

import portfolio as pf

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def fetch_quote(code, market):
    """腾讯实时行情（持仓不在自选时用）"""
    try:
        raw = http_get(f"https://qt.gtimg.cn/q={market}{code}")
        parts = raw.split("~")
        if len(parts) > 4:
            return {"price": float(parts[3]), "change_pct": float(parts[32]) if len(parts) > 32 else None}
    except Exception:
        pass
    return None


def fetch_history(code, market, days=250):
    """腾讯前复权日K（持仓不在自选时用）"""
    try:
        url = (
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
            f"{market}{code},day,,,{days},qfq"
        )
        data = json.loads(http_get(url))
        node = (data.get("data") or {}).get(f"{market}{code}") or {}
        klines = node.get("qfqday") or node.get("day") or []
        out = []
        for k in klines:
            if len(k) < 6:
                continue
            try:
                out.append({
                    "date": k[0], "open": float(k[1]), "close": float(k[2]),
                    "high": float(k[3]), "low": float(k[4]), "volume": float(k[5]),
                })
            except (ValueError, IndexError):
                continue
        return out
    except Exception:
        return []


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
        host = os.environ.get("SMTP_HOST") or "smtp.qq.com"
        port = int(os.environ.get("SMTP_PORT") or 465)
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, pw)
            server.sendmail(user, [to], msg.as_string())
        print("[notify] 邮件已发送")
        return True
    except Exception as e:
        print(f"[notify] 发信失败: {e}")
        return False


def market_section():
    """市场状态 + 情绪周期 + 龙头摘要"""
    lines = []
    q = load_json(os.path.join(DATA_DIR, "quant.json"), {})
    regime = q.get("market_regime") or {}
    if regime.get("desc"):
        lines.append(f"市场状态: {regime['desc']}")

    d = load_json(os.path.join(DATA_DIR, "dragon_head.json"), {})
    s = d.get("sentiment") or {}
    if s:
        t = s.get("today") or {}
        lines.append(f"情绪周期: {t.get('state', '-')}（涨停{t.get('zt_count', '-')}只/最高{t.get('max_lbc', '-')}板）｜ {s.get('trend', {}).get('desc', '')}")
    tiers = d.get("tiers") or {}
    if tiers.get("S") or tiers.get("A"):
        sa = (tiers.get("S") or [])[:2] + (tiers.get("A") or [])[:3]
        names = "、".join(f"{it['name']}{it['lbc']}板" for it in sa)
        lines.append(f"龙头观察: {names}")
    return "\n".join(lines) if lines else "暂无市场数据"


def backtest_section():
    """回测摘要（读 data/backtest.json 股票池回测结论）"""
    lines = []
    bt = load_json(os.path.join(DATA_DIR, "backtest.json"), {})
    if bt.get("conclusion"):
        lines.append(bt["conclusion"])
    picks = load_json(os.path.join(DATA_DIR, "picks.json"), {})
    stocks = picks.get("stocks") or []
    if stocks:
        top = stocks[:5]
        lines.append("今日选股清单(D策略): " + "、".join(f"{s.get('name', s.get('code'))}({s.get('score', '-')}分)" for s in top))
    return "\n".join(lines) if lines else "回测数据待更新（周一自动跑）"


def portfolio_section():
    """持仓建议（config portfolio，无则演示 watchlist）"""
    portfolio, capital = pf.load_portfolio()
    demo = False
    if not portfolio:
        demo = True
        cfg = pf.load_json(pf.CONFIG_PATH, {})
        for w in cfg.get("watchlist", [])[:5]:
            portfolio.append({"code": w["code"], "market": w["market"], "name": w["name"],
                              "shares": 1000, "cost": 0})
        if not capital:
            capital = {"total": 200000, "cash": 60000}

    snap = load_json(os.path.join(DATA_DIR, "snapshot.json"), {})
    quote_map = {q["code"]: q for q in (snap.get("quotes") or [])}

    advices = []
    for p in portfolio:
        code, market = p["code"], p["market"]
        hist = load_json(os.path.join(DATA_DIR, "history", f"{code}.json"), [])
        quote = quote_map.get(code)
        if not hist or not quote:
            # 现场拉取
            if not quote:
                quote = fetch_quote(code, market)
            if not hist:
                hist = fetch_history(code, market)
        if p.get("cost", 0) <= 0 and quote and quote.get("price"):
            p["cost"] = round(quote["price"] * 0.95, 2)  # 演示成本
        a = pf.advice_one(p, quote, hist)
        if a.get("price") is None:
            a["advice"], a["reason"] = "数据缺失", f"{code} 行情获取失败"
        advices.append(a)
        time.sleep(0.1)

    regime_desc = (load_json(os.path.join(DATA_DIR, "quant.json"), {}).get("market_regime") or {}).get("desc", "")
    overall = pf.overall_advice(advices, capital, regime_desc)
    return advices, overall, demo


def build_report():
    advices, overall, demo = portfolio_section()
    # 信号落库（P1b：真实建议→10日后自动回填判定）
    if not demo:
        try:
            import signal_history
            signal_history.record_signals(advices)
        except Exception as e:
            print(f"[warn] 信号落库失败: {e}")
    extra = [("市场与情绪", market_section()), ("回测摘要", backtest_section())]
    body = pf.format_report_text(advices, overall, extra)
    # AI 决策报告（有 DEEPSEEK_API_KEY 时追加，失败不影响主报告）
    try:
        import ai_report
        payload = ai_report.build_data_payload()
        ai_text = ai_report.call_deepseek(payload)
        if ai_text:
            body += "\n\n" + "=" * 40 + "\n🤖 AI 决策报告\n" + "=" * 40 + "\n" + ai_text
    except Exception as e:
        print(f"[warn] AI 报告失败: {e}")
    if demo:
        body += "\n\n⚠️ 当前为演示持仓（watchlist 前5只，成本=现价×0.95）。请在 config.json 配置真实 portfolio 与 capital。"
    return body, overall


def main():
    dry = "--dry-run" in sys.argv
    body, overall = build_report()
    print(body)
    if dry:
        print("\n[dry-run] 不发送")
        return
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    marker = os.path.join(DATA_DIR, f"report_sent_{date_str}.txt")
    # 当日已发过则跳过（GitHub 定时 + 本机兜底双触发时防重复邮件）
    if os.path.exists(marker):
        print(f"[skip] 今日({date_str})报告已发送过（{marker} 存在），跳过")
        return
    subject = f"【A股量化报告】{date_str} 仓位{overall['pos_ratio']}%｜{overall['summary'][:40]}"
    ok = send_email(subject, body)
    if ok:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now().isoformat())
        print(f"[sent] 邮件已发送，写入去重标记 {marker}")
    else:
        print("[warn] 邮件发送失败/未配置，不写标记（下次重试）")


if __name__ == "__main__":
    main()
