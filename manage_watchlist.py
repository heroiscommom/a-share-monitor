#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自选股管理脚本 —— 解析 GitHub Issue 里的 /add、/remove 命令，更新 config.json。
由 .github/workflows/manage-watchlist.yml 在 issue 打开时调用。

命令格式（放在 issue 正文任意一行）：
  /add 600036           添加（市场自动识别，名称自动从腾讯接口查询）
  /add 600036 招商银行   添加（可手动指定名称）
  /remove 600036        移除
"""

import os
import re
import json
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HEADERS = {"User-Agent": "Mozilla/5.0"}


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def detect_market(code):
    """根据代码前缀判断市场：沪/深/北"""
    if code.startswith(("6", "5", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"  # 0/1/2/3 开头 → 深市


def fetch_name(code, market):
    """从腾讯接口查询股票名称，失败则返回代码本身"""
    try:
        url = f"https://qt.gtimg.cn/q={market}{code}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="replace")
        for line in text.strip().split(";"):
            if "=" not in line:
                continue
            _, _, payload = line.partition("=")
            parts = payload.strip().strip('"').split("~")
            if len(parts) > 2 and parts[2] == code:
                return parts[1].replace(" ", "").replace("\u3000", "")
    except Exception:
        pass
    return code


def parse_command(body):
    for line in (body or "").splitlines():
        line = line.strip()
        m = re.match(r"^/(add|remove)\s+([0-9]{6})\s*(.*)$", line)
        if m:
            return m.group(1), m.group(2), m.group(3).strip()
    return None, None, None


def get_issue():
    body = os.environ.get("ISSUE_BODY", "")
    number = os.environ.get("ISSUE_NUMBER", "")
    ev_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if (not body or not number) and ev_path and os.path.exists(ev_path):
        try:
            ev = json.load(open(ev_path, "r", encoding="utf-8"))
            issue = ev.get("issue", {})
            body = body or issue.get("body", "")
            number = number or str(issue.get("number", ""))
        except (OSError, json.JSONDecodeError):
            pass
    return number, body


def main():
    number, body = get_issue()
    action, code, name = parse_command(body)

    if not action:
        print("⚠️ 未识别到有效命令。请使用：")
        print("- `/add 600036` 添加自选股")
        print("- `/remove 600036` 移除自选股")
        return

    cfg = load_config()
    watchlist = cfg.get("watchlist", [])

    if action == "add":
        if any(s.get("code") == code for s in watchlist):
            print(f"ℹ️ {code} 已在自选股中，无需重复添加")
        else:
            market = detect_market(code)
            if not name:
                name = fetch_name(code, market)
            watchlist.append({"code": code, "market": market, "name": name})
            cfg["watchlist"] = watchlist
            save_config(cfg)
            print(f"✅ 已添加 {name}({code})，市场 {market}")

    elif action == "remove":
        before = len(watchlist)
        removed = [s for s in watchlist if s.get("code") == code]
        watchlist = [s for s in watchlist if s.get("code") != code]
        if not removed:
            print(f"ℹ️ {code} 不在自选股中")
        else:
            cfg["watchlist"] = watchlist
            save_config(cfg)
            print(f"✅ 已移除 {removed[0].get('name', code)}({code})")

    print(f"（当前自选股 {len(cfg['watchlist'])} 只）")


if __name__ == "__main__":
    main()
