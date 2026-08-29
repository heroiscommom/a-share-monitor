#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收盘日报（推送机制 v2）—— 收盘后把当天所有级别信号（S/A/B/C）汇总成一封邮件，
另发一条微信摘要。发完清空 digest.json。

`python3 digest.py --demo` 发送演示邮件（展示四级格式）。

环境变量：
  SMTP_USER / SMTP_PASS / SMTP_TO / SMTP_HOST(默认 smtp.qq.com)
  SERVERCHAN_KEY  （可选，配置则同时推一条微信摘要）
"""

import os
import sys
import json
import smtplib
import datetime
from email.mime.text import MIMEText
from email.header import Header

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIGEST_PATH = os.path.join(BASE_DIR, "data", "digest.json")

TIER_META = {
    "S": ("🔴 核心信号（S级）—— 回测验证，重点关注", "S"),
    "A": ("🟠 重要信号（A级）—— 趋势/资金/关键位", "A"),
    "B": ("🟡 预警（B级）", "B"),
    "C": ("⚪ 参考（C级）", "C"),
}


def send_email(subject, body):
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    to = os.environ.get("SMTP_TO")
    host = os.environ.get("SMTP_HOST") or "smtp.qq.com"  # Actions 未配置时为空串，需兜底
    port = int(os.environ.get("SMTP_PORT", 465))
    if not (user and pw and to):
        print("[notify] 未配置 SMTP_USER/SMTP_PASS/SMTP_TO，跳过发信")
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, pw)
            server.sendmail(user, [to], msg.as_string())
        print(f"[notify] 邮件已发送（{host}）")
        return True
    except Exception as e:
        print(f"[notify] 发信失败: {e}")
        return False


def send_wechat(title, desp):
    key = os.environ.get("SERVERCHAN_KEY")
    if not key:
        print("[wechat] 未配置 SERVERCHAN_KEY，跳过")
        return False
    import urllib.parse
    import urllib.request
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


def demo_email():
    body = (
        "这是分级提醒的【演示邮件】，展示四级格式：\n\n"
        "🔴 核心信号（S级）—— 回测验证，立即关注\n"
        "  [示例] 贵州茅台(600519)  🟢 超跌反弹机会（评分 83 分）\n\n"
        "🟠 重要信号（A级）—— 趋势/资金/关键位\n"
        "  [示例] 招商银行(600036)  ⚠️ 跌破支撑位 37.88（强）\n"
        "  [示例] 宁德时代(300750)  💸 主力净流出 75181 万元\n\n"
        "🟡 预警（B级）\n"
        "  [示例] 银行板块异动 +2.5%（自选：招商银行、平安银行）\n\n"
        "⚪ 参考（C级）\n"
        "  [示例] 平安银行  日涨幅 +3.2%\n\n"
        "—— 盘中 S/A 级微信实时推送，收盘后邮件统一汇总 ——"
    )
    send_wechat("【日报】 A股盯盘汇总（演示）", body)
    send_email("【日报】 A股盯盘异动汇总（演示）", body)


def main():
    if "--demo" in sys.argv:
        demo_email()
        return

    items = []
    c_count = 0
    if os.path.exists(DIGEST_PATH):
        try:
            with open(DIGEST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                items = data.get("items", [])
                c_count = int(data.get("c_count", 0) or 0)
        except (json.JSONDecodeError, OSError):
            pass

    # 去重标记：GitHub 定时 + 本机兜底双触发时防重复邮件；发送成功才写标记
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    if items:
        try:
            date_str = items[0].get("time", date_str)[:10]  # 以信号日期为准（兼容延迟执行）
        except Exception:
            pass
    marker = os.path.join(BASE_DIR, "data", f"digest_sent_{date_str}.txt")
    if os.path.exists(marker):
        print(f"[skip] {date_str} 日报已发送过（{marker} 存在），跳过")
        return

    ok_email = False
    ok_wechat = False
    if not items:
        body = "今日无任何级别异动，一切平静。\n\n（盘中 S/A 级信号会微信实时推送，此邮件仅收盘汇总）"
        ok_wechat = send_wechat("【日报】今日无预警", "今日无任何级别异动，一切平静。")
        ok_email = send_email("【日报】 A股盯盘异动汇总（今日无预警）", body)
    else:
        # 按级别分组
        groups = {}
        for t in items:
            groups.setdefault(t.get("tier", "C"), []).append(t)

        # 邮件：全量分级汇总（C 级不逐条列出，只给计数）
        lines = [f"今日共 {len(items)} 条 S/A/B 级异动（收盘汇总）：\n"]
        for tier in ("S", "A", "B"):
            if tier not in groups:
                continue
            title, _ = TIER_META[tier]
            lines.append(title + "：")
            for t in groups[tier]:
                lines.append(f"  {t.get('time', '')}  {t.get('name', '')}({t.get('code', '')})  {t.get('message', '')}")
            lines.append("")
        # 选股清单（D策略，picks.py 写入）
        picks = data.get("picks") or {}
        if picks and picks.get("top"):
            lines.append("📋 今日选股清单（D策略）：")
            lines.append(f"  市场：{picks.get('regime', '')}市 · {picks.get('strategy', '')}")
            for i, c in enumerate(picks["top"], 1):
                flag = " ⚠️涨停" if c.get("limit_up") else ""
                lines.append(f"  {i}. {c['name']}({c['code']}) 现价{c['price']}{flag} 支撑{c.get('support')}")
            lines.append("")
        if c_count:
            lines.append(f"⚪ 另有 {c_count} 条 C 级参考（涨跌幅/量比等，仅看板展示，不逐条列出）\n")
        body = "\n".join(lines)
        ok_email = send_email(f"【日报】 A股盯盘异动汇总（{len(items)} 条" + (f" + C级{c_count}" if c_count else "") + "）", body)

        # 微信：只发一条摘要（数量 + 各级别 TOP 几条），避免刷屏
        summary = [f"今日异动 {len(items)} 条："]
        for tier in ("S", "A", "B", "C"):
            if tier not in groups:
                continue
            _, label = TIER_META[tier]
            summary.append(f"{label} {len(groups[tier])} 条")
        top = items[:8]
        summary.append("")
        for t in top:
            emoji = {"S": "🔴", "A": "🟠", "B": "🟡", "C": "⚪"}.get(t.get("tier", "C"), "⚪")
            summary.append(f"{emoji} {t.get('name', '')} {t.get('message', '')}")
        if len(items) > 8:
            summary.append(f"...等共 {len(items)} 条，详见邮件")
        ok_wechat = send_wechat("【日报】 A股盯盘异动汇总", "\n".join(summary))

    # 发送成功（邮件或微信任一）才写标记 + 清空；全失败保留数据下次重试
    if ok_email or ok_wechat:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now().isoformat())
        print(f"[sent] 已发送，写入去重标记 {marker}")
        with open(DIGEST_PATH, "w", encoding="utf-8") as f:
            json.dump({"items": [], "c_count": 0}, f, ensure_ascii=False, indent=2)
        print("[digest] 日报已清空")
    else:
        print("[warn] 发送全部失败，保留数据与标记，下次重试")


if __name__ == "__main__":
    main()
