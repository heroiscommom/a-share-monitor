# -*- coding: utf-8 -*-
"""
舆情模块主流程（runner）—— 采集 → 打分 → 入库 → 推送 TOP5。

用法：
  python3 -m sentiment.runner            # 正常跑
  python3 -m sentiment.runner --no-push  # 只扫描入库不推送（本地调试）

架构（模块化，后续扩展零改动）：
  fetchers.scan()   采集   （加数据源 → 在 fetchers 加函数）
  scoring.score_all 打分   （加因子/情感 → 改 scoring）
  storage.save_daily 存储  （换数据库 → 重写 storage）
  notifier.push      推送  （加通道 → 在 notifier 加 send_xxx）
"""
import sys
import datetime

from . import fetchers, scoring, storage, notifier, config


def main():
    no_push = "--no-push" in sys.argv
    date_str = datetime.date.today().strftime("%Y-%m-%d")

    print(f"[sentiment] {date_str} 开始扫描舆情 ...")
    payload = fetchers.scan()
    payload["date"] = date_str

    payload = scoring.score_all(payload)

    path = storage.save_daily(date_str, payload)
    print(f"[sentiment] 已入库 {len(payload['stocks'])} 只 / {len(payload['news'])} 条新闻 → {path}")

    body = notifier.push(payload)
    print("\n===== TOP" + str(config.PUSH_TOP_N) + " 预览 =====")
    print(body)

    if no_push:
        print("\n[--no-push] 未推送")
    return payload


if __name__ == "__main__":
    main()
