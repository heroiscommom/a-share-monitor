# 与 ZhuLinsen/daily_stock_analysis (DSA) 对比分析

> 2026-08-26 · 对标开源项目 [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)（AI 大模型驱动、15+ 模型供应商、6 市场、WebUI/桌面端/Agent 问股、GitHub 高星项目）
> 目的：找出 a-share-monitor (ASM) 可以借鉴的改进点

---

## 一、两个项目定位差异

| 维度 | DSA（开源对标） | ASM（本项目） |
|------|---------------|-------------|
| 核心思路 | **AI 大模型**把数据转成自然语言决策报告 | **纯规则量化**：因子评分+支撑压力+龙头情绪 |
| 市场 | A股/港股/美股/日股/韩股/台股 | A股（腾讯接口可扩展港美股） |
| 依赖 | pandas/akshare/LLM SDK/FastAPI/Electron 等 | **纯 Python 标准库，零依赖** |
| 部署 | Actions/Docker/本地/桌面端 | Actions + Pages 静态 |
| 报告形式 | LLM 生成的决策仪表盘（风险/催化/检查清单） | 规则生成的量化建议（评分/支撑/守住率） |
| 策略 | **15 个 yaml 配置化策略**（缠论/波浪/龙头/情绪周期…） | 3 个硬编码打法（超跌/动量/龙头） |
| 持仓 | 多账户/现金账本/公司行为/防超卖/去重 | 简单流水+加权成本 |
| 信号闭环 | 决策信号→结构化提取→**结果追踪回测** | 有回测，无"信号落地准确率"追踪 |
| 推送 | 企微/飞书/Telegram/Discord/Slack/邮件 | Server酱微信 + QQ邮件 |

**一句话：DSA 赢在「AI 表达 + 策略生态 + 持仓工程化」，ASM 赢在「回测严谨性 + 零依赖 + 规则可验证」。**

## 二、ASM 可借鉴的改进点（按优先级）

### 🔴 P0：AI 决策报告（差距最大，收益最高）

DSA 的最大卖点是 LLM 把量化数据变成**人话报告**：
```
🚨 风险警报: 2月5日主力资金大幅净卖出3.63亿元…
✨ 利好催化: 被市场定位为AI服务器HDI核心供应商…
📢 操作检查清单: …
```

ASM 已具备全部量化原料（评分/信号/支撑/守住率/龙头/情绪/资金流），只差一层 LLM 总结。

**方案**：`ai_report.py` 把现有 `auto_report.py` 的结构化数据（持仓建议+支撑压力+市场情绪+龙头）打包成 prompt → 调 DeepSeek API（便宜，~1元/百万token）→ 生成 400 字决策报告 → 邮件+微信推送。
- 成本：每天一份报告约 0.01 元
- 风险控制：LLM 输出只做"表述润色"，**建议结论仍以规则引擎为准**（防止 LLM 幻觉改变决策）

### 🟡 P1：策略 YAML 配置化（学 DSA 的 strategies/ 目录）

DSA 每个策略是独立 yaml：
```yaml
name: dragon_head
display_name: 龙头策略
core_rules: [2, 7]
required_tools: [get_realtime_quote, get_sector_rankings, search_stock_news]
market_regimes: [sector_hot]
instructions: | ...评分调整规则...
```

ASM 的打法选择器是硬编码 3 种。改成 `strategies/*.yaml` 配置化后：
- 新增策略（均线金叉/箱体震荡/缩量回调/事件驱动…）只需加一个 yaml + 一个因子函数
- 前端选择器、报告、推送自动识别新策略
- 每个策略声明适用市场状态（上涨市用动量、情绪回暖用龙头）→ 与现有 regime 门控打通

### 🟡 P1：决策信号结果追踪（学 DSA 的 decision_signal_outcome）

DSA 把每次报告的买卖建议结构化落库（buy/sell/观望 + 狙击点位），之后自动追踪这些信号**实际对不对**，统计建议准确率。

ASM 改进：`data/signal_history.json` 记录每次 auto_report 的建议（日期/代码/方向/目标价/支撑价）→ 10 个交易日后自动回填结果 → 周复盘报告统计"建议准确率"。**这比回测更接近实盘真实表现**（回测是历史模拟，这是你自己执行后的真实反馈）。

### 🟡 P1：狙击点位结构化（ideal_buy / secondary_buy / stop_loss / take_profit）

DSA 从报告提取 4 个关键价位。ASM 的支撑/压力 v2 数据更精确，输出同样结构：
```
理想买点 = 最近强支撑（守住率≥60%）
次级买点 = 次近支撑
止损     = 强支撑下沿 × 0.98
止盈     = 最近压力
```
前端持仓卡片和推送里直接展示"狙击点位"卡片，比现在"支撑 6.35(守0%)"更直观。

### 🟢 P2：大盘复盘段落（学 DSA 的 market review + guardrail）

DSA 每天推送大盘复盘：指数涨跌/涨跌家数/涨停跌停/板块领涨领跌，且带合规软化。

ASM 日报加一段（数据全有）：
```
📊 今日大盘: 上证 +0.85% | 涨停52 跌停3 | 领涨: 家居用品/化学制药 | 领跌: …
```

### 🟢 P2：持仓服务增强（学 DSA 的 portfolio_service）

DSA 持仓支持：多账户、**现金账本**（入金/出金/分红）、**公司行为**（送股/配股/除权）、防超卖检查、交易去重（uid）。

ASM 的 trade.py 增加：
- `trade.py cash in 50000 入金` / `out` / `dividend 600664 300 分红` → 现金自动变化
- 卖出时校验持仓不足（防超卖）
- 除权除息时成本自动调整（可选，较复杂）

### 🟢 P2：多市场支持（低成本扩展）

腾讯接口支持港股 `hk`/美股 `us` 前缀（README FAQ 已说明）。ASM 的代码里 market 已作为参数传递，扩展 `config.json` watchlist 加 `hk00700` 即可。DSA 支持的日韩台股需要专门数据源，优先级低。

### 🟢 P3：CI 冒烟测试（防 GitHub 上跑挂）

ASM 之前 workflow 踩过 `SMTP_PORT` 空值等 bug。加一个简单 CI：
```yaml
- run: python3 -m py_compile *.py        # 语法检查
- run: python3 trade.py pos && python3 verify_sr_v2.py --quick  # 冒烟
```
防止代码推到 GitHub 后 Actions 才发现坏了。

### 🟢 P3：多推送渠道

Server酱→微信 和 QQ邮件已有。加企业微信/飞书 webhook 各约 20 行（POST JSON），把 S/A 信号和 AI 报告同步推送。

## 三、反向：DSA 可借鉴 ASM 的（可选输出）

1. **回测严谨性**：ASM 的非重叠抽样/双边成本/一字板剔除/组合级行业中性化/walk-forward——DSA 的信号回测较简单
2. **密度剖面支撑压力**：ASM 的 v2 是纯规则、可解释、不依赖 LLM，可给 LLM 提供精确价位作为 ground truth
3. **零依赖可部署**：ASM 单文件跑通，DSA 依赖重（各有取舍）

## 四、落地路线（ASM 改造顺序）

| 阶段 | 内容 | 工作量 | 依赖 |
|------|------|--------|------|
| **P0** | AI 决策报告（DeepSeek API → 每日报告） | 中 | 需配 DeepSeek API key Secret |
| **P1a** | 策略 yaml 化（先迁现有3个，再加2个新策略） | 中 | — |
| **P1b** | 决策信号结果追踪（signal_history + 回填） | 小 | P0 报告建议落库 |
| **P1c** | 狙击点位卡片（前端+推送） | 小 | — |
| **P2a** | 大盘复盘段落进日报 | 小 | — |
| **P2b** | 持仓增强（现金账本/分红/防超卖） | 小 | — |
| **P3** | CI 冒烟 + 多推送渠道 | 小 | — |

> 建议先做 P0（AI 报告，效果最直观、用户感知最强），再做 P1a（策略生态，长期扩展性）。

## 五、结论

DSA 是「AI 表达 + 全市场 + 生态化」路线，ASM 是「规则可验证 + 零依赖 + 自闭环」路线，**两者互补而非替代**。ASM 最值得抄的三件事：**① LLM 报告层（把量化结果讲成人话）② 策略 yaml 配置化（可扩展）③ 决策信号结果追踪（真实胜率统计）**——都是低成本高感知的改进，且不破坏 ASM 现有的回测严谨性。
