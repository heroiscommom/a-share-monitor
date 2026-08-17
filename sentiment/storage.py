# -*- coding: utf-8 -*-
"""
存储层（storage）—— 历史"数据库"（JSON 按日归档，GitHub 仓库即数据库）。

约定：
  data/sentiment/YYYY-MM-DD.json  每日全量快照（全部股票 + 新闻，不止 TOP5）
  data/sentiment/latest.json      最新一期（页面展示用）

未来如需换 SQLite/数据库，只需重写本文件，上层零改动。
"""
import os
import json
import datetime

from . import config


def _path(date_str):
    return os.path.join(config.DATA_DIR, date_str + ".json")


def save_daily(date_str, payload):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    payload = dict(payload)
    payload["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(_path(date_str), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    # 最新一期
    with open(os.path.join(config.DATA_DIR, config.LATEST_FILE), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return _path(date_str)


def load_daily(date_str):
    path = _path(date_str)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_dates():
    """已入库日期（升序），供未来分析用"""
    if not os.path.isdir(config.DATA_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(config.DATA_DIR)
                  if f.endswith(".json") and f != config.LATEST_FILE)
