#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通知推送模块（2026-08 重构）
================================================
统一 Server酱微信推送 / QQ 邮箱 SMTP 发信 / 交易时段判断，
替代散落在 monitor.py / digest.py / picks.py / ai_report.py / auto_report.py /
morning_report.py / weekly_review.py / sentiment/notifier.py 里的重复实现。

凭据一律从环境变量读取（不落盘）：
  SERVERCHAN_KEY   Server酱 SendKey（微信推送）
  SMTP_USER        QQ 邮箱账号
  SMTP_PASS        QQ 邮箱 SMTP 授权码
  SMTP_TO          收件邮箱
  SMTP_HOST        可选，默认 smtp.qq.com
  SMTP_PORT        可选，默认 465
"""

import os
import json
import smtplib
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.header import Header


def send_wechat(title, desp):
    """Server酱 微信推送，凭据从环境变量 SERVERCHAN_KEY 读取"""
    key = os.environ.get("SERVERCHAN_KEY")
    if not key:
        print("[wechat] 未配置 SERVERCHAN_KEY，跳过")
        return False
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


def send_email(subject, body):
    """QQ 邮箱 SMTP 发信，凭据从环境变量读取"""
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    to = os.environ.get("SMTP_TO")
    if not (user and pw and to):
        print("[notify] 未配置 SMTP_USER/SMTP_PASS/SMTP_TO，跳过发信")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to
    try:
        host = os.environ.get("SMTP_HOST") or "smtp.qq.com"  # Actions 未配置时为空串，需兜底
        port = int(os.environ.get("SMTP_PORT", 465))
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, pw)
            server.sendmail(user, [to], msg.as_string())
        print(f"[notify] 邮件已发送（{host}）")
        return True
    except Exception as e:
        print(f"[notify] 发信失败: {e}")
        return False


def is_trading_time(now):
    """A股交易时段（含盘前盘后缓冲）：周一至周五 9:20-11:40 / 12:50-15:10"""
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 20 <= t <= 11 * 60 + 40) or (12 * 60 + 50 <= t <= 15 * 60 + 10)
