// ═══════════════════════════════════════════════════════════════
// 入口模块（2026-08 重构：从 app.js 抽出）
// 初始化、视图切换、数据装配（enrichQuotes）、自动刷新、UI 绑定。
// index.html 通过 <script type="module" src="js/main.js"> 加载。
// ═══════════════════════════════════════════════════════════════

import { $, fmtTime, loadJSON, normalizeStrategy, ensureEcharts } from './util.js';
import { state } from './state.js';
import { loadChart, drawFactor } from './charts.js';
import {
  renderWatchlist, renderAlerts, applyStrategyMode,
  loadBacktest, loadSectors, loadScanner, loadPicks, loadStrategies, loadDragon,
} from './board.js';
import { initTradeForm, openTradeModal, loadTrade } from './trade.js';
import { loadReview, loadReviewBasics, renderAllTrades } from './review.js';
import { initManage } from './manage.js';

// ═══════════ 视图切换 ═══════════
export function showView(v) {
  ['board', 'trade', 'review'].forEach((k) => {
    const el = document.getElementById('view-' + k);
    if (el) el.classList.toggle('hidden', k !== v);
  });
  document.querySelectorAll('#main-nav button').forEach((b) => {
    b.classList.toggle('active', b.dataset.view === v);
  });
  try { if (location.hash !== '#' + v) history.replaceState(null, '', '#' + v); } catch (e) {}
  if (v === 'trade') loadTrade();
  if (v === 'review') loadReview();
  window.scrollTo(0, 0);
}

// 把 quant/moneyflow/sector/support_resistance/signals 合并进快照行情
async function enrichQuotes(snap) {
  const [quantData, mfData, sectorData, srData, sigData] = await Promise.all([
    loadJSON('data/quant.json').catch(() => null),
    loadJSON('data/moneyflow.json').catch(() => null),
    loadJSON('data/sectors.json').catch(() => null),
    loadJSON('data/support_resistance.json').catch(() => null),
    loadJSON('data/signals.json').catch(() => null),
  ]);
  const quantMap = {};
  ((quantData && quantData.stocks) || []).forEach((s) => { quantMap[s.code] = s; });
  const mfMap = {};
  ((mfData && mfData.stocks) || []).forEach((s) => { mfMap[s.code] = s; });
  const sectorMap = (sectorData && sectorData.stock_sector) || {};
  const srMap = {};
  ((srData && srData.stocks) || []).forEach((s) => { srMap[s.code] = s; });
  const sigMap = {};
  ((sigData && sigData.stocks) || []).forEach((s) => { sigMap[s.code] = s; });
  (snap.quotes || []).forEach((q) => {
    const s = quantMap[q.code];
    if (s) {
      // 全部策略字段（默认/动量/金叉/缩量）一并合并，切打法不再丢评分
      ['score', 'signal', 'signal_key', 'factors', 'momentum_score', 'momentum_signal', 'momentum_indicators',
        'ma_score', 'ma_signal', 'ma_factors', 'shrink_score', 'shrink_signal', 'shrink_factors', 'resonance'].forEach((k) => {
        if (s[k] !== undefined) q[k] = s[k];
      });
    }
    const m = mfMap[q.code];
    if (m) { q.netamount = m.netamount; q.r0_net = m.r0_net; }
    q.sector = sectorMap[q.code] || '';
    const sr = srMap[q.code];
    if (sr) { q.supports = sr.supports; q.resistances = sr.resistances; q.sr_risk = sr.risk; }
    const sg = sigMap[q.code];
    if (sg) { q.daily_buy = sg.daily_buy; q.daily_sell = sg.daily_sell; q.intraday_buy = sg.intraday_buy; q.intraday_sell = sg.intraday_sell; }
  });
  return snap;
}

// 盘中静默自动刷新：只更新表格/评分，不打断图表与操作
async function refreshQuotes() {
  try {
    const snap = await loadJSON('data/snapshot.json');
    if (!snap || !snap.quotes || !snap.quotes.length) return;
    const ts = snap.updated_at || '';
    if (ts && ts === state.lastSnapTs) return;   // 数据没变就跳过重绘
    state.lastSnapTs = ts;
    await enrichQuotes(snap);
    window.__snapQuotes = snap.quotes;
    $('#updated').textContent = '更新于 ' + fmtTime(ts) + '（自动刷新）';
    renderWatchlist(snap.quotes);
    const nq = snap.quotes.find((x) => x.code === (state.activeStock && state.activeStock.code));
    if (nq) { state.activeStock = nq; drawFactor(nq); }
  } catch (e) { /* 静默失败，下轮再试 */ }
}

function startAutoRefresh() {
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  state.refreshTimer = setInterval(refreshQuotes, 60000);
}

async function init() {
  // 等图表库就绪（jsdelivr 失败会走 staticfile 回退），失败则表格仍可用
  await ensureEcharts();
  try {
    const cfg = await loadJSON('config.json').catch(() => null);
    if (cfg && cfg.strategy && cfg.strategy.active && !localStorage.getItem('strategyMode')) {
      state.strategyMode = normalizeStrategy(cfg.strategy.active);
    }
    const snap = await loadJSON('data/snapshot.json').catch(() => null);
    window.__snapQuotes = (snap && snap.quotes) || [];
    if (snap && snap.quotes && snap.quotes.length) {
      await enrichQuotes(snap);
      state.lastSnapTs = snap.updated_at || '';
      $('#updated').textContent = snap.updated_at ? '更新于 ' + fmtTime(snap.updated_at) : '等待首次采集';
      renderWatchlist(snap.quotes);
    } else {
      $('#updated').textContent = '数据未采集（等 GitHub Actions 更新）';
      $('tbody').innerHTML =
        '<tr><td colspan="6" class="empty">暂无数据，等待首次采集（导航等操作仍可用）</td></tr>';
    }
    const al = await loadJSON('data/alerts.json').catch(() => null);
    renderAlerts((al && al.items) || []);
    loadBacktest();
    loadSectors();
    loadScanner();
    loadPicks();
    loadStrategies();
    loadDragon();
  } catch (e) {
    console.warn('数据加载部分失败(不影响操作):', e);
  }
  // UI 绑定独立于数据加载——即使某个数据源失败，按钮也必须可点
  bindUI();
  startAutoRefresh();
  loadReviewBasics();   // 预计算持仓/现金（情绪卡当前仓位用）
  const initView = (location.hash || '').replace('#', '');
  if (['trade', 'review'].includes(initView)) showView(initView);
}

function bindUI() {
  document.querySelectorAll('#main-nav button').forEach((b) => {
    b.addEventListener('click', () => showView(b.dataset.view));
  });
  initTradeForm();
  initManage();
  document.querySelectorAll('.strategy-toggle button').forEach((b) => {
    b.addEventListener('click', () => {
      state.strategyMode = b.dataset.strategy;
      localStorage.setItem('strategyMode', state.strategyMode);
      applyStrategyMode();
      const snap2 = window.__snapQuotes || [];
      if (snap2.length) loadChart(snap2[0]);
    });
  });
  // 表头排序
  document.querySelectorAll('th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const k = th.dataset.sort;
      if (state.sortKey === k) state.sortDir = -state.sortDir;
      else { state.sortKey = k; state.sortDir = 1; }
      document.querySelectorAll('th[data-sort]').forEach((t) => t.classList.remove('sorted-asc', 'sorted-desc'));
      th.classList.add(state.sortDir === 1 ? 'sorted-asc' : 'sorted-desc');
      const snap2 = window.__snapQuotes || [];
      if (snap2.length) renderWatchlist(snap2);
    });
  });
  // 图表区交易按钮
  const tb = $('#chart-trade-btn');
  if (tb) tb.addEventListener('click', () => { if (state.activeStock) openTradeModal(state.activeStock); });
  // 复盘流水筛选
  document.querySelectorAll('#flow-filter button').forEach((b) => {
    b.addEventListener('click', () => {
      state.flowFilter = b.dataset.f;
      document.querySelectorAll('#flow-filter button').forEach((x) => x.classList.toggle('active', x === b));
      loadJSON('data/trades.json').catch(() => null).then((d) => {
        if (d) renderAllTrades(d.trades || []);
      });
    });
  });
  // 分时/日K切换
  $('#btn-intraday').addEventListener('click', () => switchMode('intraday'));
  $('#btn-daily').addEventListener('click', () => switchMode('daily'));
  applyStrategyMode();
}

function switchMode(mode) {
  state.chartMode = mode;
  $('#btn-intraday').classList.toggle('active', mode === 'intraday');
  $('#btn-daily').classList.toggle('active', mode === 'daily');
  if (state.activeStock) loadChart(state.activeStock);
}

window.addEventListener('resize', () => {
  state.chart && state.chart.resize();
  state.equityChart && state.equityChart.resize();
  state.sentimentChart && state.sentimentChart.resize();
  state.radarChart && state.radarChart.resize();
  state.backtestChart && state.backtestChart.resize();
  state.thresholdChart && state.thresholdChart.resize();
  state.sectorHeatChart && state.sectorHeatChart.resize();
});

// 入口：模块脚本在 DOM 解析后执行（type="module" 自带 defer 语义）
init();
