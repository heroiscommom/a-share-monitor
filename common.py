#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
公共工具模块（2026-08 重构）
================================================
把散落在 13+ 个脚本里的重复工具函数（load_json / save_json / http_get /
to_float / 市场推断 / 路径常量）统一收拢到这里，避免每份脚本各自复制一份。

依赖：仅 Python 标准库，无需 pip install。
"""

import os
import json
import time
import urllib.request

# ═══════════════ 路径常量（以仓库根目录为基准） ═══════════════
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
INTRADAY_DIR = os.path.join(DATA_DIR, "intraday")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# 统一 UA，避免部分接口拒收默认 Python UA
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def to_float(s):
    """字符串转 float，失败返回 None"""
    if s is None or s == "" or s == "-":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def http_get(url, encoding="utf-8", timeout=20, headers=None, retries=2):
    """
    GET 请求，返回解码后的文本。headers 可覆盖默认 UA。
    网络失败自动重试 retries 次（指数退避 0.5s/1s），避免免费接口偶发超时丢数据。
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode(encoding, errors="replace")
        except Exception as e:  # noqa: BLE001  (URLError/TimeoutError/HTTPError 等)
            last_exc = e
            if attempt < retries:
                time.sleep(0.5 * (2 ** attempt))
    raise last_exc


def load_json(path, default=None):
    """读取 JSON 文件；不存在或解析失败返回 default。"""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def save_json(path, obj, indent=2):
    """
    写 JSON 文件（自动建目录，UTF-8 不转义中文）。
    原子写：先写 *.tmp 再 os.replace，避免进程中断留下半截 JSON。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def market_of(code):
    """
    按代码推断市场前缀：
      - 6/5/9 开头 → sh（沪市A股 / 沪市基金ETF / 沪市B股）
      - 4/8 开头   → bj（北交所）
      - 其余       → sz（深市A股 / 深市基金ETF / 创业板 300 / 科创板 688 归 sh 由 6 覆盖）
    """
    code = str(code)
    if code.startswith(("6", "5", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"
