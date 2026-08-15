#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日收盘后发送 B/C 级异动日报，并清空 digest.json。
`python3 digest.py --demo` 发送演示邮件（展示三级格式）。
"""

import os
import sys
import json
import time
import urllib.request
import smtplib
from email.mime.text import MIMEText
from email.header import Header

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIGEST_PATH = os.path.join(BASE_DIR, "data", "digest.json")


def send_email(subject, body):
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    to = os.environ.get("SMTP_TO")
    if not (user and pw and to):
        print("[notify] 未配置 SMTP，跳过发信")
        return False
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
        "🟡 预警（B级）—— 收盘后进日报\n"
        "  [示例] 银行板块异动 +2.5%（自选：招商银行、平安银行）\n\n"
        "⚪ 参考（C级）—— 收盘后进日报\n"
        "  [示例] 平安银行  日涨幅 +3.2%\n\n"
        "—— 今后 S/A 级实时推送，B/C 级每天收盘后一封日报汇总 ——"
    )
    send_wechat("【紧急】 A股盯盘提醒（分级测试）", body)
    send_email("【紧急】 A股盯盘提醒（分级测试）", body)


def main():
    if "--demo" in sys.argv:
        demo_email()
        return

    items = []
    if os.path.exists(DIGEST_PATH):
        try:
            with open(DIGEST_PATH, "r", encoding="utf-8") as f:
                items = json.load(f).get("items", [])
        except (json.JSONDecodeError, OSError):
            pass

    if not items:
        body = "今日无 B/C 级异动，一切平静。\n\n（S/A 级信号会实时推送，此日报仅汇总 B/C 级）"
        send_wechat("【日报】今日无预警", "今日无 B/C 级异动，一切平静。")
        send_email("【日报】 A股盯盘异动汇总（今日无预警）", body)
    else:
        b_items = [t for t in items if t.get("tier") == "B"]
        c_items = [t for t in items if t.get("tier") == "C"]
        # 每条异动单独发一条短微信，保证手表能完整显示（每条间隔3秒）
        for i, t in enumerate(items):
            emoji = "🟡" if t.get("tier") == "B" else "⚪"
            title = f"{emoji} {t['name']} {t['message']}"
            desp = f"{t['time']} {t['name']}({t['code']}) {t['message']}"
            send_wechat(title, desp)
            if i < len(items) - 1:
                time.sleep(3)
        # 邮件保留完整长消息汇总
        lines = []
        if b_items:
            lines.append("🟡 预警（B级）：")
            lines += [f"  {t['time']}  {t['name']}({t['code']})  {t['message']}" for t in b_items]
        if c_items:
            lines.append("⚪ 参考（C级）：")
            lines += [f"  {t['time']}  {t['name']}({t['code']})  {t['message']}" for t in c_items]
        body = f"今日 B/C 级异动汇总（{len(items)} 条）：\n\n" + "\n".join(lines)
        send_email(f"【日报】 A股盯盘异动汇总（{len(items)} 条）", body)

    # 清空日报
    with open(DIGEST_PATH, "w", encoding="utf-8") as f:
        json.dump({"items": []}, f, ensure_ascii=False, indent=2)
    print("[digest] 日报已清空")


if __name__ == "__main__":
    main()
