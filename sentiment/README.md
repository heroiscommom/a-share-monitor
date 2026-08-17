# 舆情模块（sentiment/）

扫描市场舆论 → 按热度打分 → 推送 TOP5（Server酱微信）+ 全量入库（JSON 数据库）。

## 架构（模块化，四层分离）

```
sentiment/
├── config.py     配置（数据源开关/权重/推送条数/存储路径）—— 改这里不用改代码
├── fetchers.py   采集层 —— 加数据源只加 fetch_xxx()，返回统一结构
├── scoring.py    打分层 —— 热度分（排名/排名变化/涨跌幅/新闻数加权）+ 情感分插槽
├── storage.py    存储层 —— data/sentiment/YYYY-MM-DD.json（历史库）+ latest.json（页面）
├── notifier.py   推送层 —— format_top5 排版与 send_wechat 发送解耦，通道可加
└── runner.py     主流程：scan → score → store → push
```

## 数据流约定（模块化的核心）

所有数据源必须返回**标准记录**：
```python
{code, name, price, change_pct, rank, rank_change, news: [{title, summary, time}]}
```
上层（打分/存储/推送）只认这个结构 → 换数据源/加数据源，上层零改动。

## 运行

```bash
python3 -m sentiment.runner            # 采集→打分→入库→推送TOP5
python3 -m sentiment.runner --no-push  # 只扫描入库（本地调试）
```

定时：GitHub Actions `sentiment.yml`，每天 16:10 北京时间（收盘后）。

## 数据文件

| 文件 | 内容 |
|------|------|
| `data/sentiment/YYYY-MM-DD.json` | 每日全量快照（100只热度榜 + 30条新闻，不止 TOP5）|
| `data/sentiment/latest.json` | 最新一期（页面展示用）|

## 未来扩展（无需改框架，只加"零件"）

| 需求 | 改哪里 |
|------|--------|
| 加数据源（雪球/微博/研报） | `fetchers.py` 加 `fetch_xxx()` |
| 情感分析（NLP 打分） | `scoring.py` 实现 `sentiment_score()` |
| 舆情因子进选股策略 | 读 `storage.load_daily()` 历史 → 打分 → 与行情因子合成 |
| 换 SQLite/数据库 | 重写 `storage.py` |
| 加推送通道（邮件/webhook） | `notifier.py` 加 `send_xxx()` + config 开启 |
| 页面展示 | 前端读 `latest.json` |
