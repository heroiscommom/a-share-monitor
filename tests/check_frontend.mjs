// ═══════════════════════════════════════════════════════════════
// 前端模块冒烟测试（无浏览器环境）
// 阶段1：真实数据加载 —— fetch 直接读仓库 data/*.json，init() 全路径渲染
// 阶段2：工具函数单测 —— gradeScore / esc / derivePositions / fmt
// 运行：node tests/check_frontend.mjs
// ═══════════════════════════════════════════════════════════════

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

// ── 通用元素 stub：任何属性读取返回可链式调用的 stub ──
function makeEl() {
  return new Proxy({}, {
    get(t, prop) {
      if (prop === 'classList') return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
      if (prop === 'dataset') return {};
      if (prop === 'style') return { display: '', setProperty() {} };
      if (typeof prop === 'symbol') return undefined;
      if (prop === 'value' || prop === 'placeholder' || prop === 'textContent' || prop === 'innerHTML' || prop === 'length') return '';
      if (prop === 'querySelectorAll') return () => [];
      if (prop === 'querySelector') return () => makeEl();
      return () => makeEl();
    },
    set() { return true; },
  });
}

// ── 全局环境 ──
globalThis.window = globalThis;
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.dispatchEvent = () => {};
globalThis.document = new Proxy({}, {
  get(t, prop) {
    if (prop === 'querySelector' || prop === 'getElementById' || prop === 'createElement') return () => makeEl();
    if (prop === 'querySelectorAll') return () => [];
    if (typeof prop === 'symbol') return undefined;
    return () => makeEl();
  },
  set() { return true; },
});
globalThis.location = { hostname: 'localhost', pathname: '/a-share-monitor/', hash: '' };
globalThis.history = { replaceState() {} };
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
globalThis.alert = () => {};
globalThis.scrollTo = () => {};
globalThis.open = () => {};
globalThis.echarts = {
  init: () => ({ setOption() {}, clear() {}, resize() {}, dispose() {}, on() {} }),
};

// fetch 直接读仓库文件（相对 URL 视为仓库根下路径）
globalThis.fetch = async (url) => {
  const clean = String(url).replace(/^\//, '');
  const p = path.join(ROOT, clean);
  try {
    const txt = fs.readFileSync(p, 'utf-8');
    return { ok: true, status: 200, json: async () => JSON.parse(txt) };
  } catch (e) {
    return { ok: false, status: 404, json: async () => ({}) };
  }
};

const errors = [];
const fail = (msg) => { errors.push(msg); };
process.on('unhandledRejection', (e) => { fail('unhandledRejection: ' + (e && e.stack || e)); });
process.on('uncaughtException', (e) => { fail('uncaughtException: ' + (e && e.stack || e)); });

const js = (name) => pathToFileURL(path.join(ROOT, 'js', name)).href;

// ═══════════ 阶段 1：真实数据 init ═══════════
try {
  await import(js('main.js'));
  await new Promise((r) => setTimeout(r, 1000));  // 等异步 init/加载链完成
} catch (e) {
  fail('main.js 加载/init: ' + (e && e.stack || e));
}

// ═══════════ 阶段 2：工具函数单测 ═══════════
try {
  const util = await import(js('util.js'));
  const { state } = await import(js('state.js'));

  // gradeScore 阈值（与后端一致）
  if (util.gradeScore('momentum', 70) !== 'strong') fail('gradeScore momentum 70 → strong');
  if (util.gradeScore('momentum', 55) !== 'bullish') fail('gradeScore momentum 55 → bullish');
  if (util.gradeScore('momentum', 40) !== 'neutral') fail('gradeScore momentum 40 → neutral');
  if (util.gradeScore('momentum', 30) !== 'weak') fail('gradeScore momentum 30 → weak');
  if (util.gradeScore('ma_golden_cross', 55) !== 'bullish') fail('gradeScore ma 55 → bullish');
  if (util.gradeScore('ma_golden_cross', 40) !== 'neutral') fail('gradeScore ma 40 → neutral(原行为)');

  // esc
  if (util.esc('<b>&"\'</b>') !== '&lt;b&gt;&amp;&quot;&#39;&lt;/b&gt;') fail('esc 转义不正确: ' + util.esc('<b>&"\'</b>'));
  if (util.esc(null) !== '') fail('esc(null) 应为空串');

  // normalizeStrategy
  if (util.normalizeStrategy('dragon_head') !== 'dragon') fail('normalizeStrategy dragon_head → dragon');

  // derivePositions：买入→卖出 的加权平均与已实现盈亏
  const trades = [
    { date: '2026-01-05', id: 1, code: '600036', side: 'buy', shares: 100, price: 10, fee: 0 },
    { date: '2026-01-08', id: 2, code: '600036', side: 'buy', shares: 100, price: 12, fee: 0 },
    { date: '2026-01-10', id: 3, code: '600036', side: 'sell', shares: 100, price: 11, fee: 0 },
  ];
  const pos = util.derivePositions(trades);
  const p = pos['600036'];
  if (p.shares !== 100) fail('derivePositions shares 应为 100，实际 ' + p.shares);
  if (Math.abs(p.avg - 11) > 1e-9) fail('derivePositions avg 应为 11（加权），实际 ' + p.avg);
  if (Math.abs(p.realized - 0) > 1e-9) fail('derivePositions realized 应为 0（(11-11)*100），实际 ' + p.realized);
  // deriveCash：首笔建仓日当天不计入现金流（种子现金），其后买入扣款/卖出回款
  if (util.deriveCash(trades, 1000) !== 1000 - 1200 + 1100) fail('deriveCash 计算错误');

  // fmtMoney / fmt
  if (util.fmtMoney(12345) !== '12,345') fail('fmtMoney 千分位错误: ' + util.fmtMoney(12345));
  if (util.fmt(null) !== '-') fail('fmt(null) 应为 -');

  // 状态默认值
  if (state.chartMode !== 'intraday') fail('state.chartMode 默认 intraday');
} catch (e) {
  fail('工具函数单测异常: ' + (e && e.stack || e));
}

// ═══════════ 阶段 3：真实数据渲染关键路径 ═══════════
try {
  const board = await import(js('board.js'));
  const charts = await import(js('charts.js'));
  const trade = await import(js('trade.js'));
  const review = await import(js('review.js'));
  const { state } = await import(js('state.js'));

  // 自选股表格（真实 snapshot 行情）
  const snap = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'snapshot.json'), 'utf-8'));
  window.__snapQuotes = snap.quotes || [];
  if (snap.quotes && snap.quotes.length) {
    await board.renderWatchlist(snap.quotes);
    // 主图：分时 + 日K 各画一遍（echarts stub）
    await charts.loadChart(snap.quotes[0]);
    state.chartMode = 'daily';
    await charts.loadChart(snap.quotes[0]);
    state.chartMode = 'intraday';
  } else {
    fail('snapshot.json 无行情数据，跳过图表路径');
  }

  // 交易弹窗（真实持仓推导）
  trade.openTradeModal(snap.quotes[0]);
  trade.loadTrade();

  // 复盘：全部流水 + 决策闭环（真实 trades）
  await review.loadReviewBasics();
  const tradesData = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'trades.json'), 'utf-8'));
  review.renderAllTrades(tradesData.trades || []);
  review.renderStockSummary(tradesData.trades || []);

  // 龙头梯队 + 情绪（真实 dragon_head.json）
  await board.loadDragon();
  if (state.dragonData) {
    board.renderDragon();
    board.renderSentiment();
  }

  // 策略切换：dragon / momentum 模式下的评分与因子雷达分支
  const quantData = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'quant.json'), 'utf-8'));
  const q0 = Object.assign({}, snap.quotes[0], (quantData.stocks || [])[0] || {});
  state.strategyMode = 'momentum';
  board.scoreSignalOf(q0);
  charts.drawFactor(q0);
  state.strategyMode = 'dragon';
  board.scoreSignalOf(q0);
  charts.drawFactor(q0);
  state.strategyMode = 'ma_golden_cross';
  charts.drawFactor(q0);
  state.strategyMode = 'shrink_pullback';
  charts.drawFactor(q0);
  state.strategyMode = 'mean_reversion';
  charts.drawFactor(q0);
} catch (e) {
  fail('真实数据渲染路径异常: ' + (e && e.stack || e));
}

if (errors.length) {
  console.error('❌ 前端冒烟测试失败：');
  for (const e of errors) console.error('  -', e);
  process.exit(1);
}
console.log('✅ 前端模块加载 + 真实数据渲染路径通过');
process.exit(0);
