# 📈 A股盯盘助手

自动盯住你的自选股，出现异动时发 **QQ 邮箱**提醒，并提供一个 **可视化看板**。整套跑在 GitHub 上，**零服务器、零成本**。

## 架构

```
┌─────────────────────────────────────────────────────┐
│                同一个 GitHub 仓库                    │
├───────────────────────┬─────────────────────────────┤
│  GitHub Actions（后端） │   GitHub Pages（前端）      │
│  ├ 定时拉行情(腾讯接口)  │  ├ 自选股列表 + 涨跌幅       │
│  ├ 异动检测            │  ├ K线走势图(ECharts)        │
│  ├ QQ邮箱发提醒         │  └ 异动记录                  │
│  └ 写 data/*.json 提交  │  └ 读 data/*.json 渲染       │
└───────────────────────┴─────────────────────────────┘

cron定时 → 拉数据 → 检测异动 → 发邮件 → 数据commit回仓库 → Pages读取渲染
```

- **后端**：GitHub Actions 定时任务（cron），纯 Python 标准库，无需 pip 装包
- **数据源**：腾讯免费行情接口（实时 + 前复权日K），无需注册、无需 token
- **提醒**：QQ 邮箱 SMTP（授权码），凭据存在 GitHub Secrets，不落盘
- **前端**：原生 HTML + ECharts，读仓库里的 `data/*.json`

## 目录结构

```
a-share-monitor/
├── .github/workflows/monitor.yml   # 定时任务配置
├── monitor.py                      # 核心脚本（抓数据/检测/发信/落盘）
├── config.json                     # 自选股 + 异动规则
├── index.html / app.js / style.css # 可视化前端
├── data/
│   ├── snapshot.json               # 最新行情快照（前端读）
│   ├── alerts.json                 # 异动记录（前端读）
│   ├── state.json                  # 去重状态
│   └── history/600000.json ...     # 每只股票的日K历史
└── README.md
```

## 快速部署（5 步）

### 第 1 步：把代码传到 GitHub

```bash
# 在项目目录下
git init
git add .
git commit -m "init: A股盯盘助手"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

> 也可以在 GitHub 网页「New repository」后，把本地目录 push 上去。

### 第 2 步：开启 GitHub Pages

1. 仓库 → **Settings** → **Pages**
2. **Source** 选 `Deploy from a branch`
3. **Branch** 选 `main`、目录选 `/ (root)` → **Save**

> ⚠️ 免费版 GitHub Pages **只支持公开仓库**。若仓库是 private，要么设为 public，要么改用其他托管（见文末 FAQ）。

等 1-2 分钟后，访问 `https://<你的用户名>.github.io/<仓库名>/` 就能看到看板。

### 第 3 步：开启 QQ 邮箱 SMTP 并拿到授权码

1. 登录 [mail.qq.com](https://mail.qq.com) → **设置** → **账户**
2. 找到「POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务」，开启 **SMTP 服务**
3. 按提示验证后，会生成一个 **16 位授权码**（**不是 QQ 密码**），复制保存

### 第 4 步：把邮箱凭据存进 Secrets

仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，添加三个：

| Name | Value |
|------|-------|
| `SMTP_USER` | 你的 QQ 邮箱，如 `123456@qq.com` |
| `SMTP_PASS` | 第 3 步拿到的 **授权码** |
| `SMTP_TO` | 收件邮箱（可以和上面相同） |

### 第 5 步：触发第一次运行

仓库 → **Actions** → 左侧 **Stock Monitor** → **Run workflow** → **Run workflow**（手动触发一次）。

跑完后回到 `data/` 目录，能看到 `snapshot.json` 已更新、`history/` 下生成了历史文件，看板也就有数据了。

---

## 配置说明

### 自选股（config.json → watchlist）

```json
{ "code": "600000", "market": "sh", "name": "浦发银行" }
```

- `market`：沪市 `sh`、深市 `sz`、北交所 `bj`
- `code`：6 位股票代码
- `name`：显示名称（随便起，纯展示用）

### 异动规则（config.json → rules）

| 规则 | 默认值 | 含义 |
|------|--------|------|
| `change_pct` | 3.0 | 单日涨跌幅 ≥ 3%（涨/跌都触发） |
| `break_high_days` | 20 | 突破 20 日新高 |
| `break_low_days` | 20 | 跌破 20 日新低 |
| `volume_ratio` | 2.0 | 量比 ≥ 2（放量） |
| `rsi_overbought` | 70 | RSI(14) ≥ 70 超买 |
| `rsi_oversold` | 30 | RSI(14) ≤ 30 超卖 |
| `history_days` | 60 | 拉取多少天日K用于计算 |

> 改完 `config.json` 直接 push，下次定时任务自动生效。

### 去重机制

同一只股票的**同一条规则**，**每天只提醒一次**（避免 10 分钟一轮刷爆邮箱）。跨天自动重置。

---

## 定时说明

- 工作流 cron 设为**北京时间交易时段**：9:30–11:30、13:00–15:00，周一至周五，每 10 分钟一次
- GitHub Actions 的 cron 用 **UTC 时区**，配置里已换算好（减 8 小时）
- **节假日照常跑但无害**：休市时数据静止、且去重机制防止重复提醒

### ⚠️ 两个要注意的点

1. **60 天休眠**：GitHub 会停止「60 天无任何提交」的仓库的定时任务。长时间不碰的话，随便手动触发一次（Run workflow）就能恢复。
2. **定时可能延迟**：GitHub 不保证 cron 精确到分，繁忙时可能晚 5-15 分钟，属正常现象。

---

## 本地运行测试

```bash
# 直接跑（无邮箱凭据时会自动跳过发信）
python3 monitor.py

# 忽略当天去重，方便反复调试（会重复触发告警）
python3 monitor.py --force
```

只依赖 Python 标准库，3.x 即可，无需 `pip install`。

---

## FAQ

**Q：GitHub Pages 必须是公开仓库，不想公开怎么办？**
A：代码和行情数据本身不含敏感信息，公开也无妨；邮箱凭据在 Secrets 里，不会泄露。若坚持私有，可改用 Cloudflare Pages / Vercel 托管前端（静态，同样免费），Actions 照常跑。

**Q：想换成微信/Telegram 推送？**
A：把 `send_email()` 函数替换成对应推送接口即可。QQ 邮箱也可以在微信里收「QQ邮箱提醒」公众号通知。

**Q：ECharts 加载慢？**
A：`index.html` 里用的是 jsdelivr CDN，国内可换成 `https://cdn.bootcdn.net/ajax/libs/echarts/5.4.3/echarts.min.js`。

**Q：能盯港股/美股吗？**
A：能。腾讯接口同样支持港股（`hk` 前缀，如 `hk00700`）和美股（`us` 前缀，如 `usAAPL`）。只需改 `config.json` 的 `market` 和 `code`，检测逻辑通用。

## 免责声明

本项目仅供学习参考，**不构成投资建议**。数据来自公开接口，可能存在延迟或误差。据此操作，风险自负。
