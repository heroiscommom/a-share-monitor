# -*- coding: utf-8 -*-
"""
舆情模块配置（集中管理，改这里不用改代码）
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "sentiment")

# ---- 采集 ----
HOT_POOL_SIZE = 100          # 热度榜扫描股票数（全市场）
HOT_RANK_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
HOT_RANK_BODY = {
    "appId": "appId01",
    "globalId": "786e4c21-70dc-435a-93bb-38",
    "marketType": "",
    "pageNo": 1,
    "pageSize": 100,
}
NEWS_URL = ("https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
            "?client=web&biz=web_news_col&column=350&order=1&needInteractData=0"
            "&page_index=1&page_size=30&req_trace=openclaw")
QUOTE_URL = "https://qt.gtimg.cn/q="   # 腾讯批量行情（补名称/价格/涨跌幅）

# ---- 打分 ----
# 热度分 = rank*W + rank_change*W + change_pct*W + news*W（各分量先归一到 0-100）
HEAT_WEIGHTS = {"rank": 0.45, "rank_change": 0.20, "change_pct": 0.25, "news": 0.10}
NEWS_PER_STOCK_MAX = 3      # 单只股票新闻计入热度的上限

# ---- 推送 ----
PUSH_TOP_N = 5               # 每天推送 TOP N 条
PUSH_CHANNELS = ["wechat"]   # 预留: ["wechat", "email"]

# ---- 存储 ----
DAILY_FILE = "daily.json"    # 历史库：data/sentiment/YYYY-MM-DD.json
LATEST_FILE = "latest.json"  # 最新一期（页面展示用）
