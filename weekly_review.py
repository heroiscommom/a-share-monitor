#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周复盘报告（2026-08-26 新增，P3）
================================================
每周日自动生成：本周交易明细 + 已实现盈亏 + 持仓浮动 + 净值变化 + 下周建议。
数据闭环：建议 → 执行(记录) → 复盘 → 修正。

用法：
  python3 weekly_review.py            # 生成复盘并邮件发送
  python3 weekly_review.py --dry-run  # 只打印

Workflow：weekly-review.yml 每周日 15:30（北京时间）
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
import trade as tr

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def http_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_quote(code, market):
    try:
        raw = http_get(f"https://qt.gtimg.cn/q={market}{code}")
        parts = raw.split("~")
        if len(parts) > 4:
            return {"price": float(parts[3]), "change_pct": float(parts[32]) if len(parts) > 32 else None}
    except Exception:
        pass
    return None


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


def send_email(subject, body):
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


def main():
    dry = "--dry-run" in sys.argv
    today = datetime.date.today()
    week_start = (today - datetime.timedelta(days=today.weekday())).isoformat()

    # ── 1. 交易流水 ──
    obj = tr.load_trades()
    trades = obj["trades"]
    week_trades = [t for t in trades if t["date"] >= week_start]
    realized_week = sum(t["shares"] for t in week_trades if t["side"] == "pnl")
    buys = [t for t in week_trades if t["side"] == "buy"]
    sells = [t for t in week_trades if t["side"] == "sell"]
    # 卖出已实现盈亏（本周）
    sell_pnl = 0.0
    pos_snapshot = tr.positions_from_trades([t for t in trades if t["date"] < week_start])
    # 简化：用全量流水的已实现盈亏 - 上周的已实现盈亏
    pos_now = tr.positions_from_trades(trades)
    realized_total = sum(p["realized_pnl"] for p in pos_now.values())
    realized_before = sum(p["realized_pnl"] for p in
                          tr.positions_from_trades([t for t in trades if t["date"] < week_start]).values())
    realized_week = realized_total - realized_before

    # ── 2. 当前持仓 + 行情 ──
    portfolio, capital = pf.load_portfolio()
    quotes = {}
    for p in portfolio:
        q = fetch_quote(p["code"], p["market"])
        if q:
            quotes[p["code"]] = q["price"]
        time.sleep(0.1)
    pos = tr.positions_from_trades(trades)
    mv = 0.0
    float_pnl = 0.0
    pos_lines = []
    for code, p in sorted(pos.items()):
        if p["shares"] > 0:
            price = quotes.get(code, 0)
            m = p["shares"] * price
            fp = m - p["avg_cost"] * p["shares"]
            mv += m
            float_pnl += fp
            pos_lines.append(f"• {p['name']}({code}) {p['shares']:.0f}股 均价{p['avg_cost']:.3f} 现价{price:.2f} 浮动{fp:+,.0f}")
        else:
            pos_lines.append(f"• {p['name']}({code}) 已清仓 累计已实现盈亏 {p['realized_pnl']:+,.0f}")

    cash = capital.get("cash") or 0
    total_assets = cash + mv

    # ── 3. 净值历史 ──
    eq = load_json(os.path.join(DATA_DIR, "equity_history.json"), {"entries": []})
    last_entry = eq["entries"][-1] if eq["entries"] else None
    last_total = last_entry["total"] if last_entry else None
    week_change = (total_assets - last_total) if last_total else None
    eq["entries"].append({
        "date": today.isoformat(),
        "total": round(total_assets, 2),
        "cash": round(cash, 2),
        "market_value": round(mv, 2),
        "realized_pnl": round(realized_total, 2),
    })
    eq["entries"] = eq["entries"][-52:]  # 保留一年
    save_json(os.path.join(DATA_DIR, "equity_history.json"), eq)

    # ── 4. 报告文本 ──
    lines = []
    lines.append("=" * 40)
    lines.append(f"📈 A股周度复盘报告")
    lines.append(f"生成时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 40)
    lines.append(f"\n【本周交易】买入 {len(buys)} 笔 / 卖出 {len(sells)} 笔")
    for t in week_trades:
        side = {"buy": "买入", "sell": "卖出", "pnl": "盈亏记录"}[t["side"]]
        if t["side"] == "pnl":
            lines.append(f"  {t['date']} {side} {t['name']}({t['code']}) {t['shares']:+,.0f} ｜ {t.get('reason', '')}")
        else:
            lines.append(f"  {t['date']} {side} {t['name']}({t['code']}) {t['shares']:.0f}股 @{t['price']}")
    lines.append(f"本周已实现盈亏: {realized_week:+,.0f}")

    lines.append(f"\n【当前持仓】")
    lines.extend(pos_lines)
    lines.append(f"持仓市值 {mv:,.0f} ｜ 浮动盈亏 {float_pnl:+,.0f}")

    lines.append(f"\n【净值】总资产 {total_assets:,.2f}（现金 {cash:,.0f} + 持仓 {mv:,.0f}）")
    if week_change is not None:
        lines.append(f"较上周({last_entry['date']} {last_entry['total']:,.2f})变动: {week_change:+,.2f} ({(week_change / last_entry['total'] * 100) if last_entry['total'] else 0:+.2f}%)")
    if len(eq["entries"]) >= 2:
        first = eq["entries"][0]
        lines.append(f"近{len(eq['entries'])}周累计: {first['total']:,.2f} → {total_assets:,.2f} ({(total_assets / first['total'] - 1) * 100 if first['total'] else 0:+.2f}%)")

    # ── 5. 下周建议（复用 portfolio 引擎） ──
    advices = []
    snap = load_json(os.path.join(DATA_DIR, "snapshot.json"), {})
    quote_map = {q["code"]: q for q in (snap.get("quotes") or [])}
    for p in portfolio:
        hist = load_json(os.path.join(DATA_DIR, "history", f"{p['code']}.json"), [])
        a = pf.advice_one(p, quote_map.get(p["code"]) or {"price": quotes.get(p["code"])}, hist)
        advices.append(a)
    ov = pf.overall_advice(advices, capital)
    lines.append(f"\n【下周建议】{ov['summary']}")
    for a in advices:
        pnl = a.get("pnl_pct")
        pnl_txt = f"{pnl:+.1f}%" if pnl is not None else "-"
        lines.append(f"  {a['name']}({a['code']}) 盈亏{pnl_txt} 评分{a.get('score', '-')} → 【{a['advice']}】{a['reason']}")

    # ── 6. 建议准确率（P1b 信号追踪） ──
    try:
        import signal_history
        n_fill = signal_history.check_and_fill()
        st = signal_history.accuracy_stats()
        lines.append(f"\n【建议准确率】{signal_history.stats_text(st)}")
        if n_fill:
            lines.append(f"（本次回填 {n_fill} 条）")
    except Exception as e:
        print(f"[warn] 信号追踪失败: {e}")

    lines.append("\n" + "=" * 40)
    lines.append("⚠️ 自动生成仅供参考。交易记录请用 trade.py 及时录入，复盘才有意义。")
    body = "\n".join(lines)
    print(body)

    if dry:
        print("\n[dry-run] 不发送")
        return
    subject = f"【A股周复盘】{today.isoformat()} 总资产{total_assets:,.0f} 周变动{week_change:+,.0f}" if week_change is not None else f"【A股周复盘】{today.isoformat()}"
    send_email(subject, body)


if __name__ == "__main__":
    main()
