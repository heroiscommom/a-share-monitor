# -*- coding: utf-8 -*-
"""
推送层（notifier）—— 按热度推送 TOP N。

当前通道：Server酱微信。预留通道：email / webhook（在 PUSH_CHANNELS 配置）。
推送格式与通道解耦：format_top5() 只负责排版，send_xxx() 只负责发送。
"""
import os

from notify import send_wechat  # 复用根目录 notify.send_wechat（Server酱）
from . import config


def format_top5(payload, top_n=None):
    """把热度 TOP N 排版成微信文本"""
    top_n = top_n or config.PUSH_TOP_N
    stocks = payload.get("stocks", [])[:top_n]
    date = payload.get("date", "")
    lines = [f"🔥 舆情热榜（{date}）", ""]
    for i, s in enumerate(stocks, 1):
        cp = s.get("change_pct")
        cp_s = f"{cp:+.2f}%" if cp is not None else "-"
        rc = s.get("rank_change")
        rc_s = f"排名{'+' if (rc or 0) > 0 else ''}{rc or 0}" if rc is not None else ""
        news_s = f" 新闻{s['news'][0]['title'][:25]}" if s.get("news") else ""
        lines.append(
            f"{i}. {s['name']}({s['code']}) 热度{s['heat_score']:.0f} "
            f"第{s['rank']}名 {rc_s} {cp_s}{news_s}"
        )
    lines.append("")
    lines.append(f"（全量 {len(payload.get('stocks', []))} 条已入库 data/sentiment/）")
    return "\n".join(lines)


def push(payload):
    """按配置通道推送 TOP N"""
    title = f"【舆情热榜】{payload.get('date', '')} TOP{config.PUSH_TOP_N}"
    body = format_top5(payload)
    if "wechat" in config.PUSH_CHANNELS:
        send_wechat(title, body)
    return body
