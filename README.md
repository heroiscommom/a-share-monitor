# 📈 A股量化盯盘平台

一套跑在 GitHub 上的 **A股量化盯盘 + 辅助决策** 系统：自动盯自选股、量化评分、支撑压力位、买卖点、全A股扫描、板块热力图，异动时发 **Server酱微信 + QQ 邮箱**提醒。**零服务器、零成本、纯免费接口。**

> ⚠️ 仅供学习研究，不构成投资建议。

## 功能总览

| 功能 | 说明 |
|------|------|
| 📊 盯盘看板 | 自选股实时行情、分时/日K图（ECharts） |
| 🔔 异动提醒 | 涨跌幅/量比/RSI/新高新低/分时急拉急跌/资金流/板块异动 |
| 🧮 量化评分 | 均值回归模型（6因子加权 0-100 分）+ 信号（超跌机会/高位风险等） |
| 📐 支撑压力位 | 三种方法（N日高低点+摆动点+筹码密集区）自动识别关键价位 |
| 💰 买卖点 | 日线买点=支撑位、卖点=压力位；分时买点=均价线/低点、卖点=高点 |
| 🔍 全A股扫描 | 每日扫沪深300+中证500（约800只），找「超跌+接近支撑」候选 |
| 🧭 板块分析 | 申万一级31行业，热力图 + 异动自动关联自选股 |
| 🔬 回测 | 300股池滚动回测 + 胜率曲线，检验因子有效性 |
| 💹 基本面/资金流 | PE/PB/市值/换手率 + 主力资金净流入 |

## 架构

```
┌─────────────────────────────────────────────────────┐
│                同一个 GitHub 仓库                    │
├───────────────────────┬─────────────────────────────┤
│  GitHub Actions（后端） │   GitHub Pages（前端）      │
│  ├ 定时拉行情/历史      │  ├ 看板（行情+图表+评分）     │
│  ├ 量化因子/评分        │  ├ 支撑压力/买卖点标注        │
│  ├ 支撑压力/买卖点      │  ├ 板块热力图 + 候选股        │
│  ├ 回测/扫描           │  ├ 回测报告 + 胜率曲线        │
│  ├ 微信+邮箱发提醒       │  └ 读 data/*.json 渲染       │
│  └ 写 data/*.json 提交  │                             │
└───────────────────────┴─────────────────────────────┘
```

**核心思路**：GitHub Pages 是纯静态托管（不能跑后端/定时/发邮件），所以后端全交给 GitHub Actions，把结果写成 `data/*.json` commit 回仓库，前端读这些 JSON 渲染。

## 数据源（全部免费，无需 token）

| 数据 | 来源 |
|------|------|
| 行情/日K/分时 | 腾讯（`qt.gtimg.cn` / `web.ifzq.gtimg.cn`） |
| 基本面（PE/PB/市值/换手） | 腾讯行情字段 |
| 主力资金流 | 新浪（`MoneyFlow`） |
| 股票池/板块分类 | 新浪（`Market_Center` / 申万一级） |

## 目录结构

```
a-share-monitor/
├── .github/workflows/
│   ├── monitor.yml            # 盯盘+评分+提醒（每5分钟，交易时段）
│   ├── manage-watchlist.yml   # 页面自选股管理（Issue 触发）
│   ├── pool-backtest.yml      # 300股池回测（每周一）
│   └── scanner.yml            # 全A股扫描（每天收盘后）
├── monitor.py                 # 主脚本（抓数据/检测/评分/发信/落盘）
├── quant.py                   # 量化因子引擎（6因子加权评分）
├── support_resistance.py      # 支撑位/压力位计算
├── signals.py                 # 买卖点引擎（日线+分时）
├── sector.py                  # 板块分析（申万一级 + 异动）
├── backtest.py                # 滚动回测引擎
├── pool_backtest.py           # 300股池回测
├── scanner.py                 # 全A股扫描器
├── manage_watchlist.py        # Issue 命令解析（/add /remove）
├── config.json                # 自选股 + 所有规则
├── index.html / app.js / style.css  # 前端
└── data/                      # 所有数据（快照/评分/信号/板块/回测...）
```

## 快速部署（5 步）

### 第 1 步：把代码传到 GitHub

```bash
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

### 第 2 步：开启 GitHub Pages

仓库 → **Settings** → **Pages** → **Source** 选 `Deploy from a branch` → **Branch** 选 `main`、目录 `/ (root)` → **Save**

> ⚠️ 免费版 GitHub Pages **只支持公开仓库**。

等 1-2 分钟，访问 `https://<用户名>.github.io/<仓库名>/`。

### 第 3 步：配置推送通道

**Server酱微信推送**（推荐，实时到微信）：

登录 [sct.ftqq.com](https://sct.ftqq.com) → 微信扫码绑定 → 复制你的 **SendKey**（形如 `sctp...`）。

**QQ 邮箱**：

登录 [mail.qq.com](https://mail.qq.com) → **设置** → **账户** → 开启 **SMTP 服务**，生成 **16 位授权码**（不是 QQ 密码）。

### 第 4 步：把凭据存进 Secrets

仓库 → **Settings** → **Secrets and variables** → **Actions** → 添加：

| Name | Value |
|------|-------|
| `SERVERCHAN_KEY` | Server酱 SendKey（微信推送） |
| `SMTP_USER` | QQ 邮箱，如 `123456@qq.com` |
| `SMTP_PASS` | 第 3 步的授权码 |
| `SMTP_TO` | 收件邮箱（可同 SMTP_USER） |

### 第 5 步：触发首次运行

Actions → **Stock Monitor** → **Run workflow** 手动触发一次。之后定时任务自动跑。

---

## 配置说明（config.json）

### 自选股（watchlist）

```json
{ "code": "600036", "market": "sh", "name": "招商银行" }
```

- `market`：沪 `sh`、深 `sz`、北 `bj`
- 也可以直接在**网页上**「⚙️ 管理自选股」添加/移除（会生成 Issue，机器人自动改配置）

### 异动规则（rules）

| 规则 | 默认 | 含义 |
|------|------|------|
| `change_pct` | 3.0 | 单日涨跌幅 ≥ 3% |
| `break_high_days` / `break_low_days` | 20 | 突破20日新高/新低 |
| `volume_ratio` | 2.0 | 量比 ≥ 2 |
| `rsi_overbought` / `rsi_oversold` | 70 / 30 | RSI 超买/超卖 |
| `intraday_spike_pct` / `_minutes` | 1.5 / 5 | 分时急拉急跌（N分钟内涨跌≥X%） |
| `intraday_volume_ratio` | 5 | 分时放量倍数 |
| `moneyflow_threshold` | 50000000 | 主力净流入/流出超5000万提醒 |
| `sector_threshold` | 2.0 | 板块涨跌幅超2%异动提醒 |

### 量化评分阈值（quant.py 顶部）

- `BUY_THRESHOLD = 82`：评分 ≥82 =「超跌机会」信号（回测胜率约 64%）
- `RISK_THRESHOLD = 32`：评分 ≤32 =「高位风险」信号

---

## 量化模型说明

### 评分模型（均值回归 v2）

6 个因子（各归一化 0-100，加权求和）：

| 因子 | 权重 | 含义 |
|------|------|------|
| RSI 超卖 | 25% | RSI 越低分越高 |
| 超跌（20日跌幅反向） | 25% | 跌越多分越高 |
| 均线偏离 | 20% | 价格低于均线越多分越高 |
| 低位（60日区间） | 15% | 越接近区间低点分越高 |
| 缩量 | 10% | 抛压衰竭 |
| 低波动 | 5% | 越稳分越高 |

信号分级：≥82 超跌机会 / 62-82 偏多 / 45-62 中性 / 32-45 偏空 / <32 高位风险

### 回测结论（300股池 · 5.2万样本）

- 评分与未来10日收益呈 **U 型关系**：超跌股（≥75）和强势股（<45）都跑赢中间档
- 「超跌」信号胜率随阈值单调上升：≥75 胜率58% → ≥82 胜率64% → ≥85 胜率71%
- 线性 IC ≈ 0（连续评分无线性预测力），但**极端超跌是真实信号**

### 支撑压力位 / 买卖点

- **三种方法交叉验证**：N日高低点 + 摆动点（局部极值）+ 筹码密集区（60天成交量分布）
- 来源越多，价位标注越「强」（强/中/弱）
- **日线买点 = 支撑位，日线卖点 = 压力位**
- **分时买点 = 分时均价线 + 日内低点，分时卖点 = 日内高点 + 均价线上方**

---

## 定时任务

| Workflow | 频率 | 说明 |
|----------|------|------|
| Stock Monitor | 每5分钟（交易时段 9:30-15:00 周一~五） | 盯盘+评分+买卖点+提醒 |
| Stock Scanner | 每天 16:00（收盘后） | 全A股扫描候选股 |
| Pool Backtest | 每周一 | 300股池回测 + 胜率曲线 |
| Manage Watchlist | Issue 打开时 | 页面自选股增删 |

> GitHub Actions cron 用 UTC，配置里已换算。节假日照常跑但无害（休市数据静止+去重防刷屏）。

---

## 本地运行

```bash
python3 monitor.py            # 正常跑（跳过发信若无邮箱凭据）
python3 monitor.py --force    # 忽略去重，方便调试
python3 backtest.py           # 单独跑回测
python3 scanner.py            # 单独跑扫描（首次约5分钟拉800只）
python3 signals.py            # 单独看买卖点
```

只依赖 Python 标准库，3.x 即可，无需 `pip install`。

---

## FAQ

**Q：免费版 Pages 必须是公开仓库？**
A：是的。代码和行情数据不含敏感信息，邮箱凭据在 Secrets 里。坚持私有可换 Cloudflare Pages / Vercel。

**Q：换微信/Telegram 推送？**
A：改 `monitor.py` 里的 `send_wechat()`（Server酱）或 `send_email()`。Server酱直接推送到微信，QQ 邮箱也可在微信收「QQ邮箱提醒」通知。

**Q：ECharts 加载慢？**
A：`index.html` 里是 jsdelivr CDN，国内可换 `https://cdn.bootcdn.net/ajax/libs/echarts/5.4.3/echarts.min.js`。

**Q：能盯港股/美股？**
A：能。腾讯接口支持港股 `hk`、美股 `us` 前缀，改 config 即可。

**Q：买卖点准吗？**
A：买卖点是技术面参考价位，不是必胜信号。建议结合量化评分（超跌机会/高位风险）一起看——「超跌 + 跌到强支撑位」才是更强的买点。

## 免责声明

本项目仅供学习研究，**不构成投资建议**。数据来自公开免费接口，可能存在延迟或误差。量化模型基于历史回测，不代表未来收益。据此操作，风险自负。
