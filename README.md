# 📈 A股量化盯盘平台

![monitor](https://github.com/heroiscommom/a-share-monitor/actions/workflows/monitor.yml/badge.svg)
![close-report](https://github.com/heroiscommom/a-share-monitor/actions/workflows/close-report.yml/badge.svg)
![pool-backtest](https://github.com/heroiscommom/a-share-monitor/actions/workflows/pool-backtest.yml/badge.svg)
![weekly-review](https://github.com/heroiscommom/a-share-monitor/actions/workflows/weekly-review.yml/badge.svg)

一套 **A股量化盯盘 + 辅助决策** 系统:自动盯自选股、量化评分、支撑压力位、买卖点、全A股扫描、板块热力图。

**推送机制 v2**:GitHub Actions 每 5 分钟盯盘(交易时段),S/A 级信号触发时**逐条 Server酱微信推送**(不攒批);收盘后**一封邮件汇总当天全部 S/A/B/C 信号**。零服务器、零成本。

> ⚠️ 仅供学习研究,不构成投资建议。

## 功能总览

| 功能 | 说明 |
|------|------|
| 📊 盯盘看板 | 自选股实时行情、分时/日K图(ECharts) |
| 🔔 异动提醒 | 涨跌幅/量比/RSI/新高新低/分时急拉急跌/资金流/板块异动 |
| 🧮 量化评分 | 均值回归模型(6因子加权 0-100 分)+ 信号(超跌机会/高位风险等) |
| 📐 支撑压力位 | 三种方法(N日高低点+摆动点+筹码密集区)自动识别关键价位 |
| 💰 买卖点 | 日线买点=支撑位、卖点=压力位;分时买点=均价线/低点、卖点=高点 |
| 🔍 全A股扫描 | 每日扫沪深300+中证500(约800只),找「超跌+接近支撑」候选 |
| 🧭 板块分析 | 申万一级31行业,热力图 + 异动自动关联自选股 |
| 🔬 回测 | 300股池滚动回测 + 胜率曲线,检验因子有效性 |
| 💹 基本面/资金流 | PE/PB/市值/换手率 + 主力资金净流入 |

## 架构

```
┌─────────────────────────────────────────────────────┐
│                同一个 GitHub 仓库                    │
├───────────────────────┬─────────────────────────────┤
│  GitHub Actions(后端) │   GitHub Pages(前端)      │
│  ├ 定时拉行情/历史      │  ├ 看板(行情+图表+评分)     │
│  ├ 量化因子/评分        │  ├ 支撑压力/买卖点标注        │
│  ├ 支撑压力/买卖点      │  ├ 板块热力图 + 候选股        │
│  ├ 回测/扫描           │  ├ 回测报告 + 胜率曲线        │
│  ├ S/A 信号 → Server酱  │  └ 读 data/*.json 渲染       │
│  │  微信实时推送         │                             │
│  ├ 收盘 → 邮件汇总日报   │                             │
│  └ 写 data/*.json 提交  │                             │
└───────────────────────┴─────────────────────────────┘
```

**核心思路**:GitHub Pages 是纯静态托管(不能跑后端/定时/发邮件),所以后端由 GitHub Actions 完成:定时拉数据 → 量化检测 → 写 `data/*.json` commit 回仓库,前端读这些 JSON 渲染。

**推送机制 v2**:S/A 级信号(超跌机会/高位风险/突破支撑压力/主力资金异动等)在每轮检测后**逐条 Server酱微信推送**,不再攒批、盘中不发邮件;所有级别信号累积到 `data/digest.json`,收盘后 `close-report.yml` 发**一封汇总邮件**(QQ 邮箱 SMTP)。

> ⚠️ GitHub Actions 的 cron 是「尽力而为」调度(最短 5 分钟、高峰可能延迟),适合分钟级盯盘;如需秒级实时需常驻服务器/本机。

## 数据源(全部免费,无需 token)

| 数据 | 来源 |
|------|------|
| 行情/日K/分时 | 腾讯(`qt.gtimg.cn` / `web.ifzq.gtimg.cn`) |
| 基本面(PE/PB/市值/换手) | 腾讯行情字段 |
| 主力资金流 | 新浪(`MoneyFlow`) |
| 股票池/板块分类 | 新浪(`Market_Center` / 申万一级) |

## 目录结构

```
a-share-monitor/
├── .github/
│   ├── actions/commit-data/action.yml   # 统一「提交+并发容错推送」复合动作
│   └── workflows/
│       ├── monitor.yml            # 盯盘+评分+S/A微信推送(每5分钟,交易时段)
│       ├── close-report.yml       # 收盘一站式(15:30):选股清单+日报邮件+AI报告+扫描+舆情
│       ├── manage-watchlist.yml   # 页面自选股管理(Issue 触发)
│       ├── trade-command.yml      # 交易录入(Issue /trade 命令)
│       ├── pool-backtest.yml      # 300股池回测(每周一)
│       ├── morning-report.yml     # 开盘前早报(9:00)
│       └── weekly-review.yml      # 周日周复盘
├── common.py                  # 公共工具(load_json/save_json原子写/http_get重试/to_float/market_of/路径常量)
├── datafeed.py                # 行情数据源(腾讯/新浪 行情/历史/分时/资金流/指数, 纯解析函数可单测)
├── notify.py                  # 推送(Server酱微信 + QQ邮箱SMTP + 交易时段判断)
├── monitor.py                 # 主脚本(线程池并行拉取/检测/评分/推送/落盘)
├── digest.py                  # 收盘日报:一封邮件汇总 S/A/B/C
├── quant.py                   # 量化因子引擎(阈值读 strategies/*.yaml，yaml 单一事实源)
├── support_resistance.py      # 支撑位/压力位计算
├── signals.py                 # 买卖点引擎(日线+分时)
├── sector.py                  # 板块分析(申万一级 + 异动)
├── backtest.py                # 滚动回测引擎(成本可配置 BACKTEST_COST_PCT)
├── pool_backtest.py           # 300股池回测
├── scanner.py                 # 全A股扫描器(8线程并行)
├── manage_watchlist.py        # Issue 命令解析(/add /remove)
├── strategy_index.py          # 策略索引(yaml → strategies.json, 迷你解析器)
├── dragon_head.py             # 龙头战法(涨停池/梯队/断板低吸/情绪)
├── picks.py / portfolio.py / trade.py / trade_command.py ...  # 选股/持仓/交易
├── config.json                # 自选股 + 所有规则
├── index.html / style.css     # 页面骨架与样式(PWA: manifest + 图标 + 数据过期警示)
├── js/                        # 前端 ES Modules（无构建步骤）
│   ├── main.js                # 入口:初始化/视图切换/自动刷新/UI绑定/过期警示
│   ├── util.js                # 工具函数与常量(esc 转义/评分分级读 yaml/持仓推导)
│   ├── state.js               # 共享可变状态
│   ├── charts.js              # ECharts 图表渲染
│   ├── board.js               # 盯盘看板(自选/异动/回测/板块/扫描/龙头/情绪)
│   ├── trade.js               # 交易视图与弹窗
│   ├── review.js              # 复盘视图
│   └── manage.js              # 自选股管理 + GitHub Issue 通道
├── strategies/*.yaml          # 策略定义(含 signal_rules 分级阈值 —— 唯一事实源)
├── tests/                     # 离线测试(31 项: 模块导入/量化/支撑压力/解析/建议规则/前端冒烟)
│   ├── test_imports.py / test_quant.py / test_core.py
│   └── check_frontend.mjs
├── package.json               # 前端类型声明 + 测试脚本
├── manifest.webmanifest       # PWA 清单(移动端"添加到主屏幕")
├── icon.svg                   # 站点图标
└── data/                      # 所有数据(快照/评分/信号/板块/回测...)
```

## 快速部署

### 第 1 步:把代码传到 GitHub

```bash
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

### 第 2 步:开启 GitHub Pages

仓库 → **Settings** → **Pages** → **Source** 选 `Deploy from a branch` → **Branch** 选 `main`、目录 `/ (root)` → **Save**

> ⚠️ 免费版 GitHub Pages **只支持公开仓库**。

等 1-2 分钟,访问 `https://<用户名>.github.io/<仓库名>/`。

### 第 3 步:配置推送通道

**Server酱微信推送**(盘中 S/A 信号实时推):

登录 [sct.ftqq.com](https://sct.ftqq.com) → 微信扫码绑定 → 复制 **SendKey**(形如 `sctp...`)。

**QQ 邮箱**(收盘日报):

登录 [mail.qq.com](https://mail.qq.com) → **设置** → **账户** → 开启 **SMTP 服务**,生成 **16 位授权码**(不是 QQ 密码)。

### 第 4 步:把凭据存进 Secrets

仓库 → **Settings** → **Secrets and variables** → **Actions** → 添加:

| Name | Value |
|------|-------|
| `SERVERCHAN_KEY` | Server酱 SendKey(微信推送) |
| `SMTP_USER` | QQ 邮箱,如 `3242506832@qq.com` |
| `SMTP_PASS` | QQ 邮箱 SMTP 授权码 |
| `SMTP_TO` | 收件邮箱(可同 SMTP_USER) |
| `SMTP_HOST` | 可选,默认 smtp.qq.com |

### 第 5 步:触发首次运行

Actions → **Stock Monitor** → **Run workflow** 手动触发一次。之后定时任务自动跑。

---

---

## 配置说明(config.json)

### 自选股(watchlist)

```json
{ "code": "600036", "market": "sh", "name": "招商银行" }
```

- `market`:沪 `sh`、深 `sz`、北 `bj`
- 也可以直接在**网页上**「⚙️ 管理自选股」添加/移除(会生成 Issue,机器人自动改配置)

### 异动规则(rules)

| 规则 | 默认 | 含义 |
|------|------|------|
| `change_pct` | 3.0 | 单日涨跌幅 ≥ 3% |
| `break_high_days` / `break_low_days` | 20 | 突破20日新高/新低 |
| `volume_ratio` | 2.0 | 量比 ≥ 2 |
| `rsi_overbought` / `rsi_oversold` | 70 / 30 | RSI 超买/超卖 |
| `intraday_spike_pct` / `_minutes` | 1.5 / 5 | 分时急拉急跌(N分钟内涨跌≥X%) |
| `intraday_volume_ratio` | 5 | 分时放量倍数 |
| `moneyflow_threshold` | 50000000 | 主力净流入/流出超5000万提醒 |
| `sector_threshold` | 2.0 | 板块涨跌幅超2%异动提醒 |

### 量化评分阈值(单一事实源 = strategies/*.yaml)

2026-08 重构后，各策略的**评分分级阈值只维护在 `strategies/*.yaml` 的 `signal_rules`**，
Python（`quant.signal_rules()`）与前端（`gradeScore`）都从这里读取，缺省值兜底：

- `mean_reversion`: strong 82 / bullish 62 / neutral 45 / bearish 32
- `momentum`: strong 70 / bullish 55 / neutral 40
- `ma_golden_cross` / `shrink_pullback`: strong 70 / bullish 55
- `dragon_head`: S 70 / A 60 / B 45（龙头分级）

改阈值 = 改一个 yaml 文件，前后端自动生效。

### 回测成本（backtest.py，可配置）

默认双边成本 `0.25%`（佣金万2.5×2 + 印花税0.05%卖出 + 滑点，偏保守）。
敏感性分析可用环境变量覆盖：`BACKTEST_COST_PCT=0.15 python3 pool_backtest.py`。

---

## 量化模型说明(v3 · 2026-08-17 数据验证版)

### 双因子模型:均值回归 + 动量

**A. 均值回归评分**(超跌机会,6 因子加权 0-100):

| 因子 | 权重 | 含义 |
|------|------|------|
| RSI 超卖 | 25% | RSI 越低分越高 |
| 超跌(20日跌幅反向) | 25% | 跌越多分越高 |
| 均线偏离 | 20% | 价格低于均线越多分越高 |
| 低位(60日区间) | 15% | 越接近区间低点分越高 |
| 缩量 | 10% | 抛压衰竭 |
| 低波动 | 5% | 越稳分越高 |

信号分级:≥82 超跌机会 / 62-82 偏多 / 45-62 中性 / 32-45 偏空 / <32 高位风险

**B. 动量评分**(强势突破,6 因子):20/60日动量、RSI强势、距60日高点、放量、活跃度,≥70 = 强势突破信号。

### 市场状态过滤(v3 核心)

用沪深300 20日涨跌幅判断市场状态,**信号按状态分级**(回测验证,3年/1.9万样本):

| 市场状态 | 超跌信号 | 动量信号 | 回测依据 |
|----------|----------|----------|----------|
| 下跌市 | **S(最强)** | B | 超跌组胜率 59.8% |
| 震荡市 | A | B | 超跌收益 +2.08% |
| 上涨市 | B(失效) | **S** | 超跌 46.4% / 动量 52.5% |

> 超跌反弹本质是「跌出来的机会」--下跌市最强、上涨市失效;动量只在上涨市有效。
> 推送文案会附带市场状态语境(如「下跌市,历史胜率最高」)。

### 回测结论(300股池 · 3年 · 19330样本 · 严格方法)

**严格方法**:非重叠抽样(每10日1样本)+ 双边成本 0.25% + 剔除一字涨停买入/跌停卖出 + walk-forward 样本外验证 + 沪深300基准。

- **样本外有效**:评分≥77 样本外胜率 56.2% vs 基准 50.0%(+6.2pp),10日超额 +3.43%;分位数动态阈值(90分位,≥74)胜率 53.8%,超额 +2.41%
- 样本内阈值表:≥80 胜率 56.5%(+4.29%)→ ≥85 胜率 62.1%(+4.43%),单调性弱于旧版(旧版 64%/71% 是重叠样本+事后选阈值虚高)
- 线性 IC ≈ 0(连续评分无线性预测力),但**极端超跌是真实信号**
- 动量因子区分度较弱(样本内 50.6~50.8%),作辅助确认信号

### 组合级回测（2026-08-17 新增）

将信号落地为策略：每10日调仓，Top20 等权，双边成本 0.25%/次，3年/沪深300池，对比买入持有基准。

| 策略 | 总收益 | 年化 | 最大回撤 | Sharpe | 胜率 |
|------|--------|------|----------|--------|------|
| 沪深300基准 | +21.5% | 6.9% | 36.7% | 0.11 | 44.6% |
| A 均值回归 Top20 | +114.6% | 32.2% | 24.1% | 0.93 | 52.2% |
| B 均值回归+行业中性化 | +200.7% | 49.5% | 21.7% | **1.45** | 55.1% |
| C 动量 Top20 | +198.1% | 49.0% | 28.6% | 1.23 | 53.6% |
| D 市场状态组合 | +363.9% | **75.1%** | **21.7%** | **1.84** | 56.5% |

**结论**：
- 行业中性化有效（B 大幅优于 A：Sharpe 1.45 vs 0.93，回撤还更低）——去掉行业 beta 后剩下的是真 alpha
- 市场状态组合最优（D）：上涨市用动量、下跌/震荡用中性化均值回归，Sharpe 1.84
- ⚠️ **幸存者偏差**：股票池为当前市值 Top300（包含期内大涨的赢家），绝对收益偏高；相对比较方向可信，实盘需用点时间股票池验证。详见 [docs/幸存者偏差说明.md](docs/幸存者偏差说明.md)

### 支撑压力位 / 买卖点

- **三种方法交叉验证**:N日高低点 + 摆动点(局部极值)+ 筹码密集区(60天成交量分布)
- 来源越多,价位标注越「强」(强/中/弱)
- **日线买点 = 支撑位,日线卖点 = 压力位**
- **分时买点 = 分时均价线 + 日内低点,分时卖点 = 日内高点 + 均价线上方**

### 支撑压力 v2 + 决策闭环（2026-08-26 新增）

- **筹码密集区升级为密度剖面 v2**：100档 `[low,high]` 区间覆盖 + 成交量加权 + 高斯平滑 + 峰值检测多区（纯标准库），输出价格区间而非单点
- **决策闭环**：每个支撑/压力位回溯历史守住率（触及后5日守住/跌破），推送文案带胜率；前端 K 线标注守住率 + 支撑压力风险评分（0-100）
- **验证结论**（295只/19324样本）：接近支撑是择时辅助而非选股信号（独立信号无效）；但守住率有单调预测力，叠加超跌评分后胜率 50.8%→53.8%

### 🐉 龙头战法（2026-08-26 新增）

- **数据源**：东方财富涨停板池（免费、实测可用），字段含连板数/首板时间/封单/炸板/行业
- **龙头强度分**：连板高度30 + 封板强度25 + 首板时间15 + 炸板10 + 板块共振10 + 换手10 → S/A/B/C 分级
- **断板低吸**：昨日连板≥2 今日断板 → 结合支撑压力 v2 守住率给出低吸参考
- **情绪周期温度计**：涨停家数历史（冰点<30/回暖/活跃/高潮>80）+ 5日/20日均趋势 + 最高连板
- **前端打法选择器**：量化区可切换「超跌反弹 / 强势动量 / 龙头战法」，自选股评分/因子雷达/信号联动
- **断板低吸回测**（Top300池3年/213事件）：断板后5日内回调至支撑 0~3% 组胜率 66.7%/收益+3.43%，vs 全断板 42.7%/+0.48%、等3日无脑买 49.8%/+1.03% —— 支撑择时贡献 +16.9pp 胜率（⚠️ 样本仅18，方向参考）

---

## 定时任务

| Workflow | 频率 | 说明 |
|----------|------|------|
| Stock Monitor | 每5分钟(交易时段 9:30-15:00 周一~五) | 盯盘+双因子评分+市场状态门控+提醒（线程池并行拉取） |
| Close Report | 每天 15:30(收盘后) | 一站式：选股清单 + 日报邮件 + AI 报告 + 全市场扫描 + 舆情热榜 |
| Pool Backtest | 每周一 | 300股池回测(严格方法+市场分层+样本外) |
| Morning Report | 每天 9:00(开盘前) | 早报微信推送 |
| Manage Watchlist | Issue 打开时 | 页面自选股增删 |
| Trade Command | Issue 打开时(标题/正文含 /trade) | 交易流水录入 |
| Weekly Review | 每周日 15:30 | 周复盘邮件 + 净值更新 |

> GitHub Actions cron 用 UTC,配置里已换算。节假日照常跑但无害(休市数据静止+去重防刷屏)。

---

## 本地运行

```bash
python3 monitor.py            # 正常跑（跳过发信若无邮箱凭据）
python3 monitor.py --force    # 忽略去重，方便调试
python3 backtest.py           # 单独跑单股回测
python3 pool_backtest.py      # 股票池回测（严格方法+市场分层+样本外）
python3 portfolio_backtest.py # 组合级回测（Top20调仓+行业中性化+市场状态组合）
python3 scanner.py            # 单独跑扫描（首次约5分钟拉800只）
python3 signals.py            # 单独看买卖点
```

只依赖 Python 标准库,3.x 即可,无需 `pip install`。

**测试（离线，不联网）**：

```bash
python3 -m unittest discover -s tests   # Python：全部模块导入 + 量化/支撑压力单测
node tests/check_frontend.mjs           # 前端：模块加载 + 真实数据渲染冒烟
```

---

## FAQ

**Q:免费版 Pages 必须是公开仓库?**
A:是的。代码和行情数据不含敏感信息,邮箱凭据在 Secrets 里。坚持私有可换 Cloudflare Pages / Vercel。

**Q:换微信/Telegram 推送?**
A:改 `notify.py` 里的 `send_wechat()`(Server酱)或 `send_email()`。Server酱直接推送到微信,QQ 邮箱也可在微信收「QQ邮箱提醒」通知。

**Q:ECharts 加载慢?**
A:`index.html` 里是 jsdelivr CDN,国内可换 `https://cdn.bootcdn.net/ajax/libs/echarts/5.4.3/echarts.min.js`。

**Q:能盯港股/美股?**
A:能。腾讯接口支持港股 `hk`、美股 `us` 前缀,改 config 即可。

**Q:买卖点准吗?**
A:买卖点是技术面参考价位,不是必胜信号。建议结合量化评分(超跌机会/高位风险)一起看--「超跌 + 跌到强支撑位」才是更强的买点。

## 免责声明

本项目仅供学习研究,**不构成投资建议**。数据来自公开免费接口,可能存在延迟或误差。量化模型基于历史回测,不代表未来收益。据此操作,风险自负。
