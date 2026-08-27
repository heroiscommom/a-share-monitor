// A股盯盘助手 - 前端逻辑
const $ = (sel) => document.querySelector(sel);

let chart = null;
let activeCode = null;
let chartMode = 'intraday';
let activeStock = null;
let radarChart = null;
let backtestChart = null;
let thresholdChart = null;
let sectorHeatChart = null;

const FACTOR_LABELS = { rsi: '超卖', drawdown: '超跌', deviation: '偏离', position: '低位', volume: '量能', volatility: '稳定' };
const MOM_LABELS = { mom20: '20日动量', mom60: '60日动量', rsi: 'RSI', pos60: '60日位置', vol_ratio: '量比', volatility: '波动' };
const DRAGON_LABELS = { lbc: '连板高度', seal: '封板强度', fbt: '首板时间', zbc: '炸板', hs: '换手' };
const MA_LABELS = { cross: '金叉强度', slope: '均线斜率', vol: '量能确认', hold: '回踩守住' };
const SHRINK_LABELS = { trend: '趋势向上', shrink: '缩量程度', pullback: '回调深度', support: '近支撑' };
let strategyList = [];   // strategies.json 动态列表
const SIGNAL_CLASS = { strong: 's-strong', bullish: 's-bullish', neutral: 's-neutral', bearish: 's-bearish', weak: 's-weak' };

let strategyMode = localStorage.getItem('strategyMode') || 'mean_reversion';

// 策略名归一化：yaml/strategies.json 里龙头叫 dragon_head，前端统一用 dragon
function normalizeStrategy(name) {
  return name === 'dragon_head' ? 'dragon' : name;
}
strategyMode = normalizeStrategy(strategyMode);
let dragonData = null;
let dragonMap = {};

async function loadJSON(url, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { cache: 'no-store', signal: ctrl.signal });
    if (!r.ok) throw new Error(url + ' → ' + r.status);
    return await r.json();
  } finally { clearTimeout(timer); }
}

function ensureEcharts(timeoutMs = 8000) {
  if (window.echarts) return Promise.resolve(true);
  return new Promise((res) => {
    const done = () => { window.removeEventListener('echarts-ready', done); res(!!window.echarts); };
    window.addEventListener('echarts-ready', done);
    setTimeout(done, timeoutMs);
  });
}

function fmt(n, d = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-';
  return Number(n).toFixed(d);
}

function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d)) return ts;
  const now = new Date();
  const p = (n) => String(n).padStart(2, '0');
  if (d.toDateString() === now.toDateString()) return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 涨停判断：创业板/科创板 20%，主板 10%（ST 5% 未纳入，按 9.8% 阈值）
function isLimitUp(q) {
  const c = q.code || '';
  const thr = /^(300|301|688)/.test(c) ? 19.8 : 9.8;
  return q.change_pct != null && q.change_pct >= thr;
}

// 策略共振徽标（跨策略多因子确认）
function resBadge(q) {
  const r = q && q.resonance;
  if (!r || !r.count || r.count < 2) return '';
  return `<span class="res-badge" title="${(r.list || []).join('+')} 共振">🔥${r.count}</span>`;
}

// 按当前打法取评分
function scoreOf(q) {
  if (strategyMode === 'momentum') return q.momentum_score;
  if (strategyMode === 'ma_golden_cross') return q.ma_score;
  if (strategyMode === 'shrink_pullback') return q.shrink_score;
  if (strategyMode === 'dragon') { const dg = dragonMap[q.code]; return dg ? dg.dragon_score : null; }
  return q.score;
}

function showChartEmpty(msg) {
  const el = $('#chart');
  if (!el) return;
  if (!window.echarts) {
    el.innerHTML = `<div class="empty" style="height:100%;display:flex;align-items:center;justify-content:center;">${msg}</div>`;
    return;
  }
  if (!chart) chart = echarts.init(el);
  chart.clear();
  chart.setOption({
    backgroundColor: 'transparent',
    graphic: { type: 'text', left: 'center', top: 'middle', style: { text: msg, fill: '#8b96ad', fontSize: 14 } },
  }, true);
}

let sortKey = null, sortDir = 1;

function selectRow(q) {
  document.querySelectorAll('tbody tr').forEach((r) => r.classList.remove('active'));
  const tr = document.querySelector(`tbody tr[data-code="${q.code}"]`);
  if (tr) tr.classList.add('active');
  loadChart(q);
}

function scoreSignalOf(q) {
  let score = null, signal = '-', sigKey = 'neutral';
  if (strategyMode === 'momentum') {
    score = q.momentum_score;
    signal = q.momentum_signal || '-';
    sigKey = q.momentum_score >= 70 ? 'strong' : (q.momentum_score >= 55 ? 'bullish' : (q.momentum_score >= 40 ? 'neutral' : 'weak'));
  } else if (strategyMode === 'ma_golden_cross') {
    score = q.ma_score;
    signal = q.ma_signal || '-';
    sigKey = score >= 70 ? 'strong' : (score >= 55 ? 'bullish' : 'neutral');
  } else if (strategyMode === 'shrink_pullback') {
    score = q.shrink_score;
    signal = q.shrink_signal || '-';
    sigKey = score >= 70 ? 'strong' : (score >= 55 ? 'bullish' : 'neutral');
  } else if (strategyMode === 'dragon') {
    const dg = dragonMap[q.code];
    if (dg) {
      score = dg.dragon_score;
      signal = `${dg.lbc}连板`;
      sigKey = dg.tier === 'S' ? 'strong' : (dg.tier === 'A' ? 'bullish' : (dg.tier === 'B' ? 'neutral' : 'weak'));
    } else {
      score = null;
      signal = isLimitUp(q) ? '涨停未入池' : '非涨停';
      sigKey = isLimitUp(q) ? 'bullish' : 'neutral';
    }
  } else {
    score = q.score;
    signal = q.signal || '-';
    sigKey = q.signal_key || 'neutral';
  }
  return { score, signal, sigKey };
}

function renderWatchlist(quotes) {
  const tbody = $('tbody');
  const rows = quotes.slice();
  if (sortKey) {
    rows.sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey];
      if (sortKey === 'score') { va = scoreOf(a); vb = scoreOf(b); }
      if (va === null || va === undefined) va = -Infinity;
      if (vb === null || vb === undefined) vb = -Infinity;
      return (va - vb) * sortDir;
    });
  }
  tbody.innerHTML = '';
  rows.forEach((q) => {
    const cp = q.change_pct;
    const cls = cp >= 0 ? 'up' : 'down';
    const { score, signal, sigKey } = scoreSignalOf(q);
    const sigCls = SIGNAL_CLASS[sigKey] || 's-neutral';
    const tr = document.createElement('tr');
    tr.dataset.code = q.code;
    tr.innerHTML =
      `<td>${q.code}</td>` +
      `<td><span class="stk-name">${q.name || '-'}</span><button class="td-trade" title="买入/卖出 ${q.name || q.code}">💰</button></td>` +
      `<td class="num">${fmt(q.price)}</td>` +
      `<td class="num ${cls}">${cp >= 0 ? '+' : ''}${fmt(cp)}%</td>` +
      `<td class="num score">${score != null ? Number(score).toFixed(0) : '-'}</td>` +
      `<td><span class="sig ${sigCls}">${signal}</span>${resBadge(q)}</td>`;
    tr.addEventListener('click', () => selectRow(q));
    tr.querySelector('.td-trade').addEventListener('click', (e) => {
      e.stopPropagation();
      selectRow(q);
      openTradeModal(q);
    });
    tbody.appendChild(tr);
  });

  if (quotes.length && (!activeStock || !quotes.some((x) => x.code === activeStock.code))) {
    loadChart(quotes[0]);
  }
}

async function loadChart(q) {
  activeStock = q;
  activeCode = q.code;
  drawFactor(q);
  if (!window.echarts) { showChartEmpty('图表库加载失败，请刷新重试'); return; }
  if (chartMode === 'intraday') await loadIntraday(q);
  else await loadDaily(q);
}

function drawFundamental(q) {
  const parts = [];
  if (q.sector) parts.push(`板块 ${q.sector}`);
  if (q.sr_risk && q.sr_risk.score != null) parts.push(`支撑压力评分 ${q.sr_risk.score}（${q.sr_risk.level}）`);
  const buy = (q.daily_buy && q.daily_buy.length) ? q.daily_buy[0].price : ((q.supports && q.supports[0]) ? q.supports[0].price : null);
  const sell = (q.daily_sell && q.daily_sell.length) ? q.daily_sell[0].price : ((q.resistances && q.resistances[0]) ? q.resistances[0].price : null);
  if (buy != null) parts.push(`买点 ${buy}`);
  if (sell != null) parts.push(`卖点 ${sell}`);
  if (q.pe != null) parts.push(`PE ${q.pe}`);
  if (q.pb != null) parts.push(`PB ${q.pb}`);
  if (q.total_mktcap != null) parts.push(`市值 ${q.total_mktcap}亿`);
  if (q.turnover_rate != null) parts.push(`换手 ${q.turnover_rate}%`);
  let mf = '';
  if (q.netamount != null) {
    const w = Math.abs(q.netamount / 10000).toFixed(0);
    const cls = q.netamount >= 0 ? 'up' : 'down';
    mf = `<span class="${cls}">主力净${q.netamount >= 0 ? '流入' : '流出'} ${w}万</span>`;
  }
  $('#fundamental-bar').innerHTML = parts.join(' · ') + (mf ? ' · ' + mf : '');
}

function drawFactor(q) {
  $('#factor-title').textContent = `${q.name || q.code} (${q.code}) 量化因子`;
  if (!window.echarts) {
    $('#factor-score').innerHTML = '';
    $('#factor-radar').innerHTML = '<div class="empty" style="height:100%;display:flex;align-items:center;justify-content:center;">图表库加载失败</div>';
    drawFundamental(q);
    return;
  }
  let factors = null, labels = FACTOR_LABELS, score = null, sigKey = 'neutral', sigText = '';
  if (strategyMode === 'momentum') {
    factors = q.momentum_indicators;
    labels = MOM_LABELS;
    score = q.momentum_score;
    sigText = q.momentum_signal || '';
    sigKey = score >= 70 ? 'strong' : (score >= 55 ? 'bullish' : (score >= 40 ? 'neutral' : 'weak'));
  } else if (strategyMode === 'ma_golden_cross') {
    factors = q.ma_factors;
    labels = MA_LABELS;
    score = q.ma_score;
    sigText = q.ma_signal || '';
    sigKey = score >= 70 ? 'strong' : (score >= 55 ? 'bullish' : 'neutral');
  } else if (strategyMode === 'shrink_pullback') {
    factors = q.shrink_factors;
    labels = SHRINK_LABELS;
    score = q.shrink_score;
    sigText = q.shrink_signal || '';
    sigKey = score >= 70 ? 'strong' : (score >= 55 ? 'bullish' : 'neutral');
  } else if (strategyMode === 'dragon') {
    const dg = dragonMap[q.code];
    score = dg ? dg.dragon_score : null;
    sigText = dg ? `${dg.lbc}连板 · ${dg.tier}级` : (isLimitUp(q) ? '涨停未入池' : '非涨停股');
    sigKey = dg && dg.tier === 'S' ? 'strong' : (dg && dg.tier === 'A' ? 'bullish' : 'neutral');
    if (dg) {
      const f = (v, max) => Math.min(100, Math.round((v || 0) / max * 100));
      factors = {
        lbc: f(Math.log2(Math.max(dg.lbc, 1)) / Math.log2(6) * 100, 1),
        seal: f(dg.fund / (dg.ltsz || 1) * 100, 5),
        fbt: dg.fbt_score != null ? dg.fbt_score : 50,
        zbc: Math.max(0, 100 - (dg.zbc || 0) * 30),
        hs: Math.min(100, Math.max(0, 100 - Math.abs((dg.hs || 5) - 12) * 4)),
      };
      labels = DRAGON_LABELS;
    }
  } else {
    factors = q.factors;
    labels = FACTOR_LABELS;
    score = q.score;
    sigText = q.signal || '';
    sigKey = q.signal_key || 'neutral';
  }
  if (score != null) {
    $('#factor-score').innerHTML =
      `<span class="score-big">${Number(score).toFixed(0)}</span>` +
      `<span class="sig ${SIGNAL_CLASS[sigKey] || 's-neutral'}">${sigText || ''}</span>`;
  } else {
    $('#factor-score').innerHTML = `<span class="empty">${strategyMode === 'dragon' ? '非涨停股无龙头分' : '暂无评分'}</span>`;
  }
  if (!factors || !Object.keys(factors).length) {
    // 无因子时显示占位提示（而非纯空白）
    if (radarChart) { try { radarChart.dispose(); } catch (e) {} radarChart = null; }
    const fel = $('#factor-radar');
    if (fel) fel.innerHTML = `<div class="empty" style="height:100%;display:flex;align-items:center;justify-content:center;">${
      strategyMode === 'dragon' ? (isLimitUp(q) ? '涨停未入池 — 无龙头分，切其他策略看因子' : '非涨停股无龙头分 — 切换到其他策略查看因子') : '暂无因子数据'}</div>`;
    drawFundamental(q);
    return;
  }
  const fel2 = $('#factor-radar');
  if (fel2) fel2.innerHTML = '';
  if (!radarChart) radarChart = echarts.init(fel2);
  const inds = Object.keys(labels).filter((k) => factors[k] !== undefined);
  radarChart.setOption({
    backgroundColor: 'transparent',
    radar: {
      indicator: inds.map((k) => ({ name: `${labels[k]} ${factors[k]}`, max: 100 })),
      axisName: { color: '#8b96ad', fontSize: 11 },
      splitLine: { lineStyle: { color: '#232c42' } },
      splitArea: { areaStyle: { color: ['rgba(58,122,254,0.02)', 'rgba(58,122,254,0.05)'] } },
      axisLine: { lineStyle: { color: '#232c42' } },
    },
    series: [{
      type: 'radar',
      data: [{
        value: inds.map((k) => factors[k]), name: '评分',
        areaStyle: { opacity: 0.3 }, lineStyle: { color: '#3a7afe' }, itemStyle: { color: '#3a7afe' },
      }],
    }],
  }, true);
  drawFundamental(q);
}

async function loadIntraday(q) {
  try {
    const d = await loadJSON(`data/intraday/${q.code}.json`);
    if (d && d.minutes && d.minutes.length) {
      $('#chart-title').textContent = `${q.name || q.code} (${q.code}) · 分时 ${d.date || ''}`;
      drawIntradayChart(q, d);
    } else {
      showChartEmpty('暂无分时数据');
    }
  } catch (e) {
    showChartEmpty('暂无分时数据');
  }
}

async function loadDaily(q) {
  try {
    const hist = await loadJSON(`data/history/${q.code}.json`);
    if (hist && hist.length) {
      $('#chart-title').textContent = `${q.name || q.code} (${q.code}) · 日K`;
      drawDailyChart(q, hist);
    } else {
      showChartEmpty('暂无历史数据');
    }
  } catch (e) {
    showChartEmpty('暂无历史数据');
  }
}

function drawIntradayChart(q, d) {
  if (!chart) chart = echarts.init($('#chart'));
  const mins = d.minutes || [];
  const times = mins.map((m) => m.t);
  const prices = mins.map((m) => m.p);
  const avgs = mins.map((m) => m.avg);
  const vols = mins.map((m) => m.v);
  const pc = d.prev_close;
  const volColors = mins.map((m) => (m.p >= pc ? '#ef232a' : '#14b143'));

  const priceSeries = {
    name: '价格', type: 'line', data: prices, showSymbol: false,
    lineStyle: { width: 1.5, color: '#ffffff' },
  };
  if (pc != null) {
    priceSeries.markLine = {
      symbol: 'none', silent: true, label: { show: false },
      lineStyle: { type: 'dashed', color: '#5a6478' },
      data: [{ yAxis: pc }],
    };
  }

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: [
      { left: 64, right: 24, top: 24, height: '58%' },
      { left: 64, right: 24, top: '76%', height: '14%' },
    ],
    xAxis: [
      { type: 'category', data: times, boundaryGap: false,
        axisLine: { lineStyle: { color: '#3a4155' } },
        axisLabel: { color: '#8b96ad', interval: 29 } },
      { type: 'category', data: times, gridIndex: 1, axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    ],
    yAxis: [
      { type: 'value', scale: true, axisLabel: { color: '#8b96ad' }, splitLine: { lineStyle: { color: '#232c42' } } },
      { type: 'value', gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      priceSeries,
      { name: '均价', type: 'line', data: avgs, showSymbol: false, lineStyle: { width: 1, color: '#f5a623' } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vols, itemStyle: { color: (p) => volColors[p.dataIndex] } },
    ],
  }, true);
}

function drawDailyChart(q, hist) {
  if (!$('#chart')) return;
  if (!chart) chart = echarts.init($('#chart'));

  const dates = hist.map((h) => h.date);
  const closes = hist.map((h) => h.close);
  const volumes = hist.map((h) => h.volume);
  const srLines = [];
  (q.resistances || []).forEach((r) => srLines.push({ yAxis: r.price, lineStyle: { color: '#ef232a', type: 'dashed', width: 1 }, label: { formatter: `卖${r.price}${r.held_rate != null ? ' 守' + r.held_rate + '%' : ''}`, color: '#ef232a', position: 'insideEndTop', fontSize: 10 } }));
  (q.supports || []).forEach((s) => srLines.push({ yAxis: s.price, lineStyle: { color: '#14b143', type: 'dashed', width: 1 }, label: { formatter: `买${s.price}${s.held_rate != null ? ' 守' + s.held_rate + '%' : ''}`, color: '#14b143', position: 'insideEndBottom', fontSize: 10 } }));

  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: [
      { left: 64, right: 24, top: 24, height: '58%' },
      { left: 64, right: 24, top: '76%', height: '14%' },
    ],
    xAxis: [
      { type: 'category', data: dates, boundaryGap: false,
        axisLine: { lineStyle: { color: '#3a4155' } },
        axisLabel: { color: '#8b96ad' } },
      { type: 'category', data: dates, gridIndex: 1,
        axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false } },
    ],
    yAxis: [
      { type: 'value', scale: true,
        axisLabel: { color: '#8b96ad' },
        splitLine: { lineStyle: { color: '#232c42' } } },
      { type: 'value', gridIndex: 1, axisLabel: { show: false },
        splitLine: { show: false } },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1] }],
    series: [
      {
        name: '收盘价', type: 'line', data: closes,
        smooth: true, showSymbol: false,
        lineStyle: { width: 2, color: '#3a7afe' },
        itemStyle: { color: '#3a7afe' },
        areaStyle: { opacity: 0.08 },
        markLine: srLines.length ? { silent: true, symbol: 'none', data: srLines } : undefined,
      },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes,
        itemStyle: { color: '#5a6478' },
      },
    ],
  }, true);
}

function renderAlerts(items) {
  const ul = $('#alerts');
  ul.innerHTML = '';
  if (!items || !items.length) {
    ul.innerHTML = '<li class="empty">暂无异动记录</li>';
    return;
  }
  items.forEach((a) => {
    const li = document.createElement('li');
    const codeLabel = a.code ? `(${a.code})` : '';
    li.innerHTML =
      `<span class="time">${a.time}</span>` +
      `<span class="stock">${a.name}${codeLabel}</span>` +
      `<span class="msg">${a.message}</span>`;
    ul.appendChild(li);
  });
}

function drawBacktestChart(groups) {
  if (!backtestChart) backtestChart = echarts.init($('#backtest-chart'));
  const labels = groups.map((g) => g.label);
  const values = groups.map((g) => g.avg_return);
  const colors = values.map((v) => (v >= 0 ? '#ef232a' : '#14b143'));
  backtestChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', formatter: (p) => {
      const g = groups[p[0].dataIndex];
      return `${g.label}：平均 ${g.avg_return}%（胜率 ${g.win_rate}%，样本 ${g.count}）`;
    } },
    grid: { left: 50, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: labels, axisLabel: { color: '#8b96ad' }, axisLine: { lineStyle: { color: '#3a4155' } } },
    yAxis: { type: 'value', name: '未来10日%', axisLabel: { color: '#8b96ad' }, splitLine: { lineStyle: { color: '#232c42' } } },
    series: [{ type: 'bar', data: values, itemStyle: { color: (p) => colors[p.dataIndex] }, barWidth: '55%' }],
  }, true);
}

function drawThresholdChart(thresholds) {
  if (!thresholdChart) thresholdChart = echarts.init($('#threshold-chart'));
  const labels = thresholds.map((t) => `≥${t.threshold}`);
  const win = thresholds.map((t) => t.win_rate);
  thresholdChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', formatter: (p) => {
      const t = thresholds[p[0].dataIndex];
      return `评分≥${t.threshold}：胜率 ${t.win_rate}%，平均 ${t.avg_return}%，样本 ${t.count}`;
    } },
    grid: { left: 50, right: 20, top: 20, bottom: 32 },
    xAxis: { type: 'category', data: labels, axisLabel: { color: '#8b96ad' }, axisLine: { lineStyle: { color: '#3a4155' } } },
    yAxis: { type: 'value', name: '胜率%', min: 45, max: 75, axisLabel: { color: '#8b96ad' }, splitLine: { lineStyle: { color: '#232c42' } } },
    series: [{ type: 'line', data: win, smooth: true, showSymbol: true, lineStyle: { color: '#ef232a', width: 2 }, itemStyle: { color: '#ef232a' }, areaStyle: { opacity: 0.08 } }],
  }, true);
}

async function loadBacktest() {
  try {
    let b = await loadJSON('data/pool_backtest.json').catch(() => null);
    if (!b || !b.groups || !b.groups.length) {
      b = await loadJSON('data/backtest.json').catch(() => null);
    }
    if (!b || !b.groups || !b.groups.length) {
      $('#backtest-meta').textContent = '';
      $('#backtest-conclusion').textContent = '暂无回测数据';
      return;
    }
    const poolInfo = b.pool_size ? `沪深300池 ${b.pool_size} 只 · ` : '';
    const method = b.methodology ? ` · ${b.methodology}` : '';
    $('#backtest-meta').textContent = `${poolInfo}持有 ${b.forward_days} 日 · 样本 ${b.total_samples} 个 · IC ${b.ic ?? '-'}${method}`;
    $('#backtest-conclusion').textContent = b.conclusion || '';
    drawBacktestChart(b.groups);
    if (b.thresholds && b.thresholds.length) drawThresholdChart(b.thresholds);
  } catch (e) {
    $('#backtest-conclusion').textContent = '回测数据加载失败';
  }
}

function sectorRow(s) {
  const cls = s.avg_change > 0 ? 'up' : 'down';
  return `<div class="sector-row"><span class="s-name">${s.name}</span><span class="s-pct ${cls}">${s.avg_change > 0 ? '+' : ''}${s.avg_change}%</span></div>`;
}

function heatColor(pct, maxAbs) {
  const t = Math.min(Math.abs(pct) / maxAbs, 1);
  const a = 0.2 + t * 0.8;
  return pct >= 0 ? `rgba(239, 35, 42, ${a.toFixed(2)})` : `rgba(20, 177, 67, ${a.toFixed(2)})`;
}

function drawSectorHeatmap(sectors) {
  if (!sectorHeatChart) sectorHeatChart = echarts.init($('#sector-heatmap'));
  const maxAbs = Math.max(...sectors.map((s) => Math.abs(s.avg_change)), 0.5);
  const data = sectors.map((s) => ({
    name: s.name,
    value: 1,
    change: s.avg_change,
    up: s.up,
    down: s.down,
    itemStyle: { color: heatColor(s.avg_change, maxAbs), borderColor: '#0f1420', borderWidth: 1, gapWidth: 1 },
  }));
  sectorHeatChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { formatter: (p) => `${p.name}：${p.data.change > 0 ? '+' : ''}${p.data.change}%（涨${p.data.up} 跌${p.data.down}）` },
    series: [{
      type: 'treemap',
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      label: { show: true, formatter: (p) => `${p.name}\n${p.data.change > 0 ? '+' : ''}${p.data.change}%`, color: '#fff', fontSize: 11, lineHeight: 15 },
      data,
    }],
  }, true);
}

async function loadSectors() {
  try {
    const d = await loadJSON('data/sectors.json');
    const secs = d.sectors || [];
    const anomalies = d.anomalies || [];
    $('#sector-meta').textContent = d.updated_at ? '更新于 ' + d.updated_at : '';
    $('#sector-anomalies').innerHTML = anomalies.length
      ? anomalies.map((a) => `<span class="sector-anom ${a.avg_change > 0 ? 'up' : 'down'}">${a.avg_change > 0 ? '📈' : '📉'} ${a.name} ${a.avg_change > 0 ? '+' : ''}${a.avg_change}%</span>`).join(' ')
      : '<span class="empty">暂无板块异动</span>';
    if (secs.length) drawSectorHeatmap(secs);
  } catch (e) {
    $('#sector-heatmap').innerHTML = '<div class="empty">板块数据加载失败</div>';
  }
}

function tierBadge(t) {
  const cls = t === 'S' ? 's-strong' : (t === 'A' ? 's-bullish' : (t === 'B' ? 's-neutral' : 's-weak'));
  return `<span class="sig ${cls}">${t}级</span>`;
}

let sentimentChart = null;

const SENT_STYLE = { 冰点: 's-weak', 回暖: 's-neutral', 活跃: 's-bullish', 高潮: 's-strong' };

function renderSentiment() {
  const barEl = $('#sentiment-bar');
  const chartEl = $('#sentiment-chart');
  const s = dragonData && dragonData.sentiment;
  if (!s) {
    if (barEl) barEl.innerHTML = '<div class="empty">情绪数据采集中…</div>';
    return;
  }
  const t = s.today;
  const st = t.state;
  const sm = s.state_machine || {};
  const zbcTxt = sm.zbc_rate != null ? Math.round(sm.zbc_rate * 100) + '%' : '-';
  // 当前实际仓位（reviewPos/reviewCash 由 loadReviewBasics 预计算）
  let posRatio = null, posHint = '';
  if (reviewPos) {
    const qm = {};
    (window.__snapQuotes || []).forEach((q) => { qm[q.code] = q.price; });
    let mv = 0;
    Object.values(reviewPos).forEach((p) => { mv += p.shares * (qm[p.code] || 0); });
    const total = (reviewCash || 0) + mv;
    if (total > 0) {
      posRatio = mv / total * 100;
      const cap = { 冰点: 20, 回暖: 50, 活跃: 70, 高潮: 50 }[st];
      if (cap) {
        if (posRatio > cap) posHint = `（当前${posRatio.toFixed(0)}% 超配${(posRatio - cap).toFixed(0)}%）`;
        else if (posRatio < cap * 0.6) posHint = `（当前${posRatio.toFixed(0)}% 可加${(cap - posRatio).toFixed(0)}%）`;
        else posHint = `（当前${posRatio.toFixed(0)}% 合理）`;
      }
    }
  }
  barEl.innerHTML =
    `<div class="sentiment-card">
      <div class="sent-item"><span class="sent-label">今日涨停</span><span class="sent-num">${t.zt_count}</span></div>
      <div class="sent-item"><span class="sent-label">情绪状态</span><span class="sig ${SENT_STYLE[st] || 's-neutral'}">${st}${sm.direction ? '·' + sm.direction : ''}</span></div>
      <div class="sent-item"><span class="sent-label">最高连板</span><span class="sent-num">${t.max_lbc}板</span></div>
      <div class="sent-item"><span class="sent-label">炸板率</span><span class="sent-num">${zbcTxt}</span></div>
      <div class="sent-item"><span class="sent-label">5日/20日均</span><span class="sent-num">${s.trend.zt5}/${s.trend.zt20}</span></div>
      <div class="sent-item"><span class="sent-label">趋势</span><span class="sig ${s.trend.rising ? 's-strong' : 's-weak'}">${s.trend.desc}</span></div>
      ${posRatio != null ? `<div class="sent-item"><span class="sent-label">当前仓位</span><span class="sent-num">${posRatio.toFixed(1)}%</span></div>` : ''}
      <div class="sent-item sent-pos"><span class="sent-label">💡 仓位建议</span><span class="sent-pos-txt">${sm.position_advice || '-'}${posHint}</span></div>
    </div>`;
  // 30 日涨停家数迷你曲线
  const hist = (s.history || []).slice(-30);
  if (hist.length >= 3 && chartEl) {
    if (!sentimentChart) sentimentChart = echarts.init(chartEl);
    const dates = hist.map((h) => h.date.replace(/^(\d{4})(\d{2})(\d{2})$/, '$2-$3'));
    const counts = hist.map((h) => h.zt_count);
    sentimentChart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis' },
      grid: { left: 40, right: 12, top: 12, bottom: 24 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#8b96ad', fontSize: 10 }, axisLine: { lineStyle: { color: '#3a4155' } } },
      yAxis: { type: 'value', axisLabel: { color: '#8b96ad', fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } } },
      series: [{
        type: 'line', data: counts, smooth: true, symbol: 'none',
        lineStyle: { color: '#3a7afe', width: 2 },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(58,122,254,0.35)' }, { offset: 1, color: 'rgba(58,122,254,0.02)' }] } },
        markLine: { silent: true, symbol: 'none', lineStyle: { color: '#f5a623', type: 'dashed' }, data: [{ yAxis: 30, label: { formatter: '冰点线30', color: '#8b96ad', fontSize: 9 } }] },
      }],
    }, true);
  } else if (chartEl && sentimentChart) {
    sentimentChart.clear();
  }
}

function renderDragon() {
  const meta = $('#dragon-meta');
  const tiersEl = $('#dragon-tiers');
  const blEl = $('#dragon-breaklow');
  if (!dragonData) {
    meta.textContent = '暂无数据（收盘后采集）';
    tiersEl.innerHTML = '<div class="empty">等待采集</div>';
    blEl.innerHTML = '';
    return;
  }
  meta.textContent = `${dragonData.date} 涨停 ${dragonData.zt_count} 只`;
  renderSentiment();
  const order = ['S', 'A', 'B', 'C'];
  const tierNames = { S: 'S 龙头确认', A: 'A 龙头候选', B: 'B 观察池', C: 'C 参考' };
  tiersEl.innerHTML = order.map((k) => {
    const list = (dragonData.tiers && dragonData.tiers[k]) || [];
    const rows = list.slice(0, 12).map((it) =>
      `<tr>
        <td>${it.name}</td><td>${it.code}</td>
        <td class="num">${it.lbc}板</td>
        <td class="num score">${it.dragon_score}</td>
        <td class="num">${it.fund != null ? (it.fund / 1e8).toFixed(1) + '亿' : '-'}</td>
        <td class="num">${it.fbt ? String(it.fbt).padStart(6, '0').slice(0, 4).replace(/^(\d{2})(\d{2})/, '$1:$2') : '-'}</td>
        <td class="num">${it.zbc}</td>
        <td>${it.hybk || '-'}</td>
      </tr>`).join('');
    return `<div class="tier-block">
      <h3>${tierNames[k]}（${list.length}）</h3>
      ${rows ? `<div class="dragon-table-wrap"><table class="dragon-table"><thead><tr><th>名称</th><th>代码</th><th>连板</th><th>强度分</th><th>封单</th><th>首板</th><th>炸板</th><th>板块</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty">无</div>'}
    </div>`;
  }).join('');

  const bl = dragonData.break_low || [];
  if (!bl.length) {
    blEl.innerHTML = '<div class="empty">今日无断板低吸候选</div>';
    return;
  }
  blEl.innerHTML = `<div class="dragon-table-wrap"><table class="dragon-table"><thead><tr><th>名称</th><th>代码</th><th>昨连板</th><th>昨强度</th><th>现价</th><th>支撑位</th><th>守住率</th><th>风险评分</th><th>板块</th></tr></thead><tbody>` +
    bl.map((c) =>
      `<tr>
        <td>${c.name}</td><td>${c.code}</td>
        <td class="num">${c.prev_lbc}板</td>
        <td class="num score">${c.prev_score || '-'}</td>
        <td class="num">${fmt(c.now_price)}</td>
        <td class="num">${c.support != null ? c.support : '-'}</td>
        <td class="num ${c.support_held != null && c.support_held >= 60 ? 'up' : ''}">${c.support_held != null ? c.support_held + '%' : '-'}</td>
        <td class="num ${c.risk_score != null && c.risk_score >= 65 ? 'up' : (c.risk_score != null && c.risk_score < 40 ? 'down' : '')}">${c.risk_score != null ? c.risk_score + '(' + (c.risk_level || '') + ')' : '-'}</td>
        <td>${c.hybk || '-'}</td>
      </tr>`).join('') + `</tbody></table></div>`;
}

function applyStrategyMode() {
  document.querySelectorAll('.strategy-toggle button').forEach((b) => {
    b.classList.toggle('active', b.dataset.strategy === strategyMode);
  });
  const snap = window.__snapQuotes || [];
  if (snap.length) renderWatchlist(snap);
}

function renderStrategyButtons() {
  const wrap = document.querySelector('.strategy-toggle');
  if (!wrap || !strategyList.length) return;
  wrap.innerHTML = strategyList.map((s) => {
    const key = normalizeStrategy(s.name);
    return `<button data-strategy="${key}" class="${key === strategyMode ? 'active' : ''}">${s.display_name}</button>`;
  }).join('');
  wrap.querySelectorAll('button').forEach((b) => {
    b.addEventListener('click', () => {
      strategyMode = normalizeStrategy(b.dataset.strategy);
      localStorage.setItem('strategyMode', strategyMode);
      applyStrategyMode();
      const snap2 = window.__snapQuotes || [];
      if (snap2.length) loadChart(snap2[0]);
    });
  });
}

function loadStrategies() {
  return loadJSON('data/strategies.json').then((d) => {
    strategyList = d.strategies || [];
    renderStrategyButtons();
  }).catch(() => { strategyList = []; });
}

function loadDragon() {
  return loadJSON('data/dragon_head.json').then((d) => {
    dragonData = d;
    dragonMap = {};
    (['S', 'A', 'B', 'C']).forEach((k) => {
      ((d.tiers && d.tiers[k]) || []).forEach((it) => {
        it.tier = k;
        dragonMap[it.code] = it;
      });
    });
    renderDragon();
  }).catch(() => {
    dragonData = null;
    renderDragon();
  });
}

let equityChart = null;
let lastQuoteMap = {};

// ═══════════ 视图切换 ═══════════
function showView(v) {
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

// ═══════════ 交易视图 ═══════════
function fmtMoney(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-';
  return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function loadTrade() {
  loadJSON('data/trades.json').then((d) => {
    const trades = d.trades || [];
    // 推导持仓
    const pos = {};
    trades.sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id);
    trades.forEach((t) => {
      if (!pos[t.code]) pos[t.code] = { name: t.name || t.code, shares: 0, avg: 0, cost: 0, realized: 0 };
      const p = pos[t.code];
      if (t.side === 'buy') {
        const c = t.shares * t.price + (t.fee || 0);
        const ns = p.shares + t.shares;
        p.avg = ns ? (p.cost + c) / ns : 0;
        p.cost += c;
        p.shares = ns;
      } else if (t.side === 'sell') {
        const sv = t.shares * t.price - (t.fee || 0);
        p.realized += sv - p.avg * t.shares;
        p.shares = Math.max(0, p.shares - t.shares);
        p.cost = p.avg * p.shares;
      } else if (t.side === 'pnl') {
        p.realized += t.shares;
      }
    });
    // 现价（只拉一次 snapshot，别每只持仓拉一遍）
    const codes = Object.keys(pos);
    loadJSON('data/snapshot.json').catch(() => null).then((s) => {
      const qm = {};
      if (s) (s.quotes || []).forEach((x) => { if (codes.includes(x.code)) qm[x.code] = x.price; });
      renderPositions(pos, qm);
    });
    renderTradeList(trades.slice(-15).reverse());
  }).catch(() => {
    document.getElementById('positions').innerHTML = '<div class="empty">暂无交易记录</div>';
  });
}

function renderPositions(pos, qm) {
  const el = document.getElementById('positions');
  const sumEl = document.getElementById('pos-summary');
  let mv = 0, floatPnl = 0;
  const rows = Object.keys(pos).map((code) => {
    const p = pos[code];
    const price = qm[code] || 0;
    const m = p.shares * price;
    const fp = m - p.avg * p.shares;
    mv += m; floatPnl += fp;
    const pnlCls = fp >= 0 ? 'up' : 'down';
    if (p.shares > 0) {
      return `<div class="pos-card">
        <div class="pos-head"><b>${p.name}</b><span>${code}</span></div>
        <div class="pos-grid">
          <div><label>持仓</label><span>${p.shares}股</span></div>
          <div><label>均价</label><span>${p.avg.toFixed(3)}</span></div>
          <div><label>现价</label><span>${price || '-'}</span></div>
          <div><label>浮动盈亏</label><span class="${pnlCls}">${fp >= 0 ? '+' : ''}${fmtMoney(fp)}</span></div>
        </div>
      </div>`;
    }
    return `<div class="pos-card pos-closed">
      <div class="pos-head"><b>${p.name}</b><span>${code} 已清仓</span></div>
      <div class="pos-grid"><div><label>已实现盈亏</label><span class="${p.realized >= 0 ? 'up' : 'down'}">${p.realized >= 0 ? '+' : ''}${fmtMoney(p.realized)}</span></div></div>
    </div>`;
  }).join('');
  el.innerHTML = rows || '<div class="empty">暂无持仓</div>';
  sumEl.textContent = `持仓市值 ${fmtMoney(mv)} ｜ 浮动 ${floatPnl >= 0 ? '+' : ''}${fmtMoney(floatPnl)}`;
}

function renderTradeList(trades) {
  const el = document.getElementById('trade-list');
  if (!trades.length) { el.innerHTML = '<div class="empty">暂无交易流水</div>'; return; }
  el.innerHTML = trades.map((t) => {
    const sideTxt = { buy: '买入', sell: '卖出', pnl: '盈亏' }[t.side] || t.side;
    const cls = t.side === 'buy' ? 'up' : (t.side === 'sell' ? 'down' : '');
    if (t.side === 'pnl') {
      return `<div class="trade-row"><span class="t-date">${t.date}</span><span class="${t.shares >= 0 ? 'up' : 'down'}">${sideTxt}</span><b>${t.name || t.code}</b><span>${t.shares >= 0 ? '+' : ''}${fmtMoney(t.shares)}</span><span class="t-note">${t.reason || ''}</span></div>`;
    }
    return `<div class="trade-row"><span class="t-date">${t.date}</span><span class="${cls}">${sideTxt}</span><b>${t.name || t.code}</b><span>${t.shares}股 @${t.price}</span><span class="t-note">${t.reason || ''}</span></div>`;
  }).join('');
}

// ═══════════ 交易录入表单 ═══════════
function buildTradeCommand() {
  const side = document.getElementById('tf-side').value;
  const code = document.getElementById('tf-code').value.trim();
  const name = document.getElementById('tf-name').value.trim();
  const qty = document.getElementById('tf-qty').value.trim();
  const price = document.getElementById('tf-price').value.trim();
  const note = document.getElementById('tf-note').value.trim();
  if (!/^\d{6}$/.test(code)) { alert('请输入6位股票代码'); return null; }
  if (!qty || Number(qty) === 0) { alert('请输入股数/金额'); return null; }
  if (side !== 'pnl' && !price) { alert('请输入成交价格'); return null; }
  const parts = ['/trade', side, code, qty];
  if (side !== 'pnl') parts.push(price);
  if (name) parts.push(name);
  if (note) parts.push(note);
  return parts.join(' ');
}

let tmStock = null;   // 当前弹窗股票

function sniperText(q) {
  // 从 sr_risk/alerts 找狙击点位(简化: 用支撑/压力)
  const sup = q.supports && q.supports[0];
  const res = q.resistances && q.resistances[0];
  const parts = [];
  if (sup) parts.push(`理想买 ${sup.price}${sup.held_rate != null ? '(守' + sup.held_rate + '%)' : ''}`);
  if (res) parts.push(`止盈 ${res.price}`);
  return parts.join(' ｜ ') || '暂无价位参考';
}

function openTradeModal(q) {
  tmStock = q;
  $('#tm-title').textContent = `交易 ${q.name || q.code} (${q.code})`;
  $('#tm-info').innerHTML =
    `<div class="tm-price-row">
       <span class="tm-now">现价 <b class="${q.change_pct >= 0 ? 'up' : 'down'}">${fmt(q.price)}</b> (${q.change_pct >= 0 ? '+' : ''}${fmt(q.change_pct)}%)</span>
       <span class="tm-sniper">${sniperText(q)}</span>
     </div>`;
  $('#tm-price').value = q.price != null ? q.price : '';
  $('#tm-qty').value = '';
  $('#tm-qty').placeholder = '股数';
  // 卖出时提示可卖数量
  loadJSON('data/trades.json').catch(() => null).then((d) => {
    const trades = (d && d.trades) || [];
    const pos = {};
    trades.forEach((t) => {
      if (!pos[t.code]) pos[t.code] = { shares: 0 };
      if (t.side === 'buy') pos[t.code].shares += t.shares;
      else if (t.side === 'sell') pos[t.code].shares -= t.shares;
    });
    const hold = pos[q.code] ? pos[q.code].shares : 0;
    $('#tm-qty').placeholder = hold > 0 ? `股数(可卖 ${hold})` : '股数';
    if ($('#tm-side').value === 'sell' && hold > 0) {
      $('#tm-qty').value = hold;
    }
  });
  const m = document.getElementById('trade-modal');
  m.classList.remove('hidden');
}

function closeTradeModal() {
  document.getElementById('trade-modal').classList.add('hidden');
}

function submitTradeModal() {
  if (!tmStock) return;
  const side = document.getElementById('tm-side').value;
  const price = document.getElementById('tm-price').value.trim();
  const qty = document.getElementById('tm-qty').value.trim();
  const note = document.getElementById('tm-note').value.trim();
  if (!qty || Number(qty) <= 0) { alert('请输入股数'); return; }
  if (!price) { alert('请输入成交价位'); return; }
  const cmd = `/trade ${side} ${tmStock.code} ${qty} ${price} ${tmStock.name || ''} ${note ? note : (side === 'buy' ? '页面录入买入' : '页面录入卖出')}`.trim();
  const title = encodeURIComponent('[trade] 交易录入');
  const body = encodeURIComponent(cmd + '\n\n（页面自动生成）');
  window.open(`https://github.com/heroiscommom/a-share-monitor/issues/new?title=${title}&body=${body}`, '_blank');
  closeTradeModal();
}

function initTradeForm() {
  document.getElementById('tf-side').addEventListener('change', (e) => {
    const isPnl = e.target.value === 'pnl';
    document.getElementById('tf-price-row').style.display = isPnl ? 'none' : '';
    document.getElementById('tf-qty').placeholder = isPnl ? '盈亏金额(如 -4961)' : '股数';
  });
  document.getElementById('tm-close').addEventListener('click', closeTradeModal);
  document.getElementById('tm-submit').addEventListener('click', submitTradeModal);
  document.getElementById('tm-side').addEventListener('change', (e) => {
    const isSell = e.target.value === 'sell';
    document.getElementById('tm-qty').value = '';
  });
  // 点击弹窗遮罩关闭
  document.getElementById('trade-modal').addEventListener('click', (e) => {
    if (e.target === document.getElementById('trade-modal')) closeTradeModal();
  });
  document.getElementById('tf-submit').addEventListener('click', () => {
    const cmd = buildTradeCommand();
    if (!cmd) return;
    const title = encodeURIComponent('[trade] 交易录入');
    const body = encodeURIComponent(cmd + '\n\n（自动生成，提交后自动录入并关闭）');
    window.open(`https://github.com/heroiscommom/a-share-monitor/issues/new?title=${title}&body=${body}`, '_blank');
  });
}

// ═══════════ 复盘视图 v2 ═══════════
let flowFilter = 'all';
let reviewPos = {};   // 按股票汇总（含已清仓）
let reviewCash = 0;

// 按流水顺序推导持仓/均价/已实现（与后端 trade.py 一致：加权平均成本）
function derivePositions(trades) {
  const pos = {};
  const sorted = trades.slice().sort((a, b) => a.date.localeCompare(b.date) || (a.id || 0) - (b.id || 0));
  sorted.forEach((t) => {
    if (!pos[t.code]) pos[t.code] = { code: t.code, name: t.name || t.code, shares: 0, avg: 0, cost: 0, realized: 0, buys: 0, sells: 0, pnls: 0 };
    const p = pos[t.code];
    if (t.side === 'buy') {
      const c = t.shares * t.price + (t.fee || 0);
      const ns = p.shares + t.shares;
      p.avg = ns ? (p.cost + c) / ns : 0;
      p.cost += c;
      p.shares = ns;
      p.buys++;
    } else if (t.side === 'sell') {
      p.realized += (t.price - p.avg) * t.shares - (t.fee || 0);
      p.shares = Math.max(0, p.shares - t.shares);
      p.cost = p.avg * p.shares;
      p.sells++;
    } else if (t.side === 'pnl') {
      p.realized += t.shares;
      p.pnls++;
    }
  });
  return pos;
}

// 现金推导：初始现金(建仓日快照) + 建仓日后资金流
function deriveCash(trades, baseCash) {
  if (!trades.length) return baseCash || 0;
  const seed = trades.map((t) => t.date).sort()[0];
  let flow = 0;
  trades.forEach((t) => {
    if (t.date <= seed) return;
    if (t.side === 'buy') flow -= t.shares * t.price + (t.fee || 0);
    else if (t.side === 'sell') flow += t.shares * t.price - (t.fee || 0);
    else if (t.side === 'pnl') flow += t.shares;
  });
  return (baseCash || 0) + flow;
}

function loadReviewBasics() {
  // 预计算持仓/现金（复盘页与情绪卡共用）——原子赋值，避免中间态（现金未就绪时被读成 0）
  return Promise.all([
    loadJSON('data/trades.json').catch(() => null),
    loadJSON('config.json').catch(() => null),
  ]).then(([d, cfg]) => {
    const trades = (d && d.trades) || [];
    reviewPos = derivePositions(trades);
    reviewCash = deriveCash(trades, (cfg && cfg.capital && cfg.capital.cash) || 0);
    // 若情绪卡已渲染过（时序竞争），用完整数据重绘一次
    if (dragonData) renderSentiment();
    return { reviewPos, reviewCash };
  });
}

function loadReview() {
  loadReviewBasics().then(() => {
    loadJSON('data/trades.json').catch(() => null).then((d) => {
      const all = (d && d.trades) || [];
      renderReviewCards(all);
      renderStockSummary(all);
      renderAllTrades(all);
    });
  });
  loadEquity();
  loadDecisionClosed();
}

function renderReviewCards(trades) {
  const el = document.getElementById('review-cards');
  const meta = document.getElementById('review-meta');
  // 现价市值
  loadJSON('data/snapshot.json').catch(() => null).then((snap) => {
    const qm = {};
    ((snap && snap.quotes) || []).forEach((q) => { qm[q.code] = q.price; });
    let mv = 0, floatPnl = 0;
    Object.values(reviewPos).forEach((p) => {
      const price = qm[p.code] || 0;
      mv += p.shares * price;
      floatPnl += (price - p.avg) * p.shares;
    });
    const total = reviewCash + mv;
    const realized = Object.values(reviewPos).reduce((s, p) => s + p.realized, 0);
    const posRatio = total > 0 ? mv / total * 100 : 0;
    // 周变动：equity 最新 vs 前一条
    loadJSON('data/equity_history.json').catch(() => null).then((eq) => {
      // 同一天可能有多条（周报/日报各写一次），按日期去重取最后一条
      const byDate = {};
      ((eq && eq.entries) || []).forEach((e) => { byDate[e.date] = e; });
      const entries = Object.values(byDate).sort((a, b) => a.date.localeCompare(b.date));
      let weekChg = null;
      if (entries.length >= 2) {
        const last = entries[entries.length - 1];
        const prev = entries[entries.length - 2];
        weekChg = last.total - prev.total;
      } else if (entries.length === 1) {
        weekChg = total - entries[0].total;
      }
      const chgCls = (weekChg || 0) >= 0 ? 'up' : 'down';
      const rCls = realized >= 0 ? 'up' : 'down';
      const fCls = floatPnl >= 0 ? 'up' : 'down';
      el.innerHTML =
        `<div class="review-card"><label>总资产</label><span>${fmtMoney(total)}</span></div>` +
        `<div class="review-card"><label>周变动</label><span class="${chgCls}">${weekChg != null ? (weekChg >= 0 ? '+' : '') + fmtMoney(weekChg) : '-'}</span></div>` +
        `<div class="review-card"><label>已实现盈亏</label><span class="${rCls}">${realized >= 0 ? '+' : ''}${fmtMoney(realized)}</span></div>` +
        `<div class="review-card"><label>浮动盈亏</label><span class="${fCls}">${floatPnl >= 0 ? '+' : ''}${fmtMoney(floatPnl)}</span></div>` +
        `<div class="review-card"><label>持仓占比</label><span>${posRatio.toFixed(1)}%</span></div>`;
      meta.textContent = `现金 ${fmtMoney(reviewCash)} ｜ 持仓市值 ${fmtMoney(mv)} ｜ ${trades.length} 笔交易`;
    });
  });
}

function loadEquity() {
  loadJSON('data/equity_history.json').then((d) => {
    const entries = d.entries || [];
    const meta = document.getElementById('equity-meta');
    if (entries.length >= 2) {
      const last = entries[entries.length - 1];
      const prev = entries[entries.length - 2];
      const chg = last.total - prev.total;
      meta.textContent = `最新 ${last.date} 总资产 ${fmtMoney(last.total)}（${chg >= 0 ? '+' : ''}${fmtMoney(chg)}）`;
    } else if (entries.length === 1) {
      meta.textContent = `最新 ${entries[0].date} 总资产 ${fmtMoney(entries[0].total)}`;
    }
    if (entries.length >= 2 && document.getElementById('equity-chart')) {
      if (!equityChart) equityChart = echarts.init(document.getElementById('equity-chart'));
      // 净值归一化(首日=100) + 沪深300同期基准对比
      const base = entries[0].total;
      const nv = entries.map((e) => [e.date, +(e.total / base * 100).toFixed(2)]);
      let bench = null;
      loadJSON('data/index_cache.json').catch(() => null).then((idx) => {
        if (idx && idx.dates && idx.closes && idx.dates.length === idx.closes.length) {
          const first = entries[0].date;
          const pairs = idx.dates.map((dt, i) => [dt, idx.closes[i]]).filter((p) => p[0] >= first);
          if (pairs.length >= 2) {
            const b0 = pairs[0][1];
            bench = { dates: pairs.map((p) => p[0]), data: pairs.map((p) => +(p[1] / b0 * 100).toFixed(2)) };
          }
        }
        const series = [{
          name: '我的净值', type: 'line', data: nv, smooth: true, symbol: 'circle', symbolSize: 6,
          lineStyle: { color: '#3a7afe', width: 2 },
          areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(58,122,254,0.35)' }, { offset: 1, color: 'rgba(58,122,254,0.02)' }] } },
          markPoint: { data: [{ type: 'max', name: '高点' }, { type: 'min', name: '低点' }] },
        }];
        if (bench) {
          series.push({
            name: '沪深300(同期)', type: 'line', data: bench.data, showSymbol: false,
            lineStyle: { color: '#f5a623', width: 1.5, type: 'dashed' },
          });
        }
        equityChart.setOption({
          backgroundColor: 'transparent',
          tooltip: { trigger: 'axis' },
          legend: { data: ['我的净值', '沪深300(同期)'], textStyle: { color: '#8b96ad', fontSize: 11 }, top: 0 },
          grid: { left: 60, right: 16, top: 28, bottom: 24 },
          xAxis: { type: 'category', data: bench ? bench.dates : entries.map((e) => e.date), axisLabel: { color: '#8b96ad', fontSize: 10 }, axisLine: { lineStyle: { color: '#3a4155' } } },
          yAxis: { type: 'value', scale: true, name: '归一化(首日=100)', nameTextStyle: { color: '#8b96ad', fontSize: 10 }, axisLabel: { color: '#8b96ad', fontSize: 10 }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.06)' } } },
          series,
        }, true);
      });
    }
  }).catch(() => {});
}

function loadDecisionClosed() {
  const el = document.getElementById('decision-closed');
  // 建议命中（P1-5 打脸复盘）
  loadJSON('data/advice_history.json').catch(() => null).then((ah) => {
    const vc = { 应验: 0, 打脸: 0, 持平: 0 };
    let facepalms = [];
    ((ah && ah.entries) || []).forEach((e) => {
      Object.entries(e.verdicts || {}).forEach(([code, v]) => {
        if (vc[v.result] !== undefined) {
          vc[v.result]++;
          if (v.result === '打脸') {
            const name = (e.items || []).find((it) => it.code === code)?.name || code;
            facepalms.push(`${e.date} ${name} ${v.detail || ''}`);
          }
        }
      });
    });
    const n = vc.应验 + vc.打脸;
    const hitHtml = n > 0
      ? `<span class="dc-num ${vc.应验 >= vc.打脸 ? 'up' : 'down'}">${(vc.应验 / n * 100).toFixed(0)}%</span><span class="dc-sub">应验${vc.应验}/打脸${vc.打脸}/持平${vc.持平}</span>`
      : `<span class="dc-num">-</span><span class="dc-sub">数据积累中（14天后回填判定）</span>`;
    // 信号追踪（P1b）
    loadJSON('data/signal_history.json').catch(() => null).then((sh) => {
      const sigs = (sh && sh.signals) || [];
      const filled = sigs.filter((s) => s.filled && s.result);
      const wins = filled.filter((s) => s.result === 'win').length;
      const fails = filled.filter((s) => s.result === 'fail').length;
      const sigHtml = filled.length
        ? `<span class="dc-num ${wins >= fails ? 'up' : 'down'}">${(wins / filled.length * 100).toFixed(0)}%</span><span class="dc-sub">信号 ${wins}胜/${fails}败/${filled.length - wins - fails}平 共${filled.length}条</span>`
        : `<span class="dc-num">-</span><span class="dc-sub">信号追踪积累中（10交易日后回填，现有${sigs.length}条待判定）</span>`;
      el.innerHTML =
        `<div class="dc-card"><div class="dc-title">💡 建议命中率</div>${hitHtml}${facepalms.length ? `<div class="dc-detail">最近打脸：${facepalms.slice(-2).join('；')}</div>` : ''}</div>` +
        `<div class="dc-card"><div class="dc-title">📡 量化信号准确率</div>${sigHtml}</div>`;
    });
  });
}

function renderStockSummary(trades) {
  const el = document.getElementById('stock-summary');
  const codes = Object.keys(reviewPos);
  if (!codes.length) { el.innerHTML = '<div class="empty">暂无交易记录</div>'; return; }
  loadJSON('data/snapshot.json').catch(() => null).then((snap) => {
    const qm = {};
    ((snap && snap.quotes) || []).forEach((q) => { qm[q.code] = q.price; });
    const rows = codes.map((code) => {
      const p = reviewPos[code];
      const price = qm[code] || 0;
      const float = (price - p.avg) * p.shares;
      const rCls = p.realized >= 0 ? 'up' : 'down';
      const fCls = float >= 0 ? 'up' : 'down';
      if (p.shares > 0) {
        return `<div class="pos-card">
          <div class="pos-head"><b>${p.name}</b><span>${code}</span></div>
          <div class="pos-grid">
            <div><label>交易</label><span>${p.buys}买/${p.sells}卖</span></div>
            <div><label>持仓</label><span>${p.shares}股 均${p.avg.toFixed(3)}</span></div>
            <div><label>浮动盈亏</label><span class="${fCls}">${float >= 0 ? '+' : ''}${fmtMoney(float)}</span></div>
            <div><label>累计已实现</label><span class="${rCls}">${p.realized >= 0 ? '+' : ''}${fmtMoney(p.realized)}</span></div>
          </div>
        </div>`;
      }
      return `<div class="pos-card pos-closed">
        <div class="pos-head"><b>${p.name}</b><span>${code} 已清仓</span></div>
        <div class="pos-grid">
          <div><label>交易</label><span>${p.buys}买/${p.sells}卖</span></div>
          <div><label>已实现盈亏</label><span class="${rCls}">${p.realized >= 0 ? '+' : ''}${fmtMoney(p.realized)}</span></div>
          <div><label>盈亏记录</label><span>${p.pnls} 笔</span></div>
          <div><label>现价</label><span>${price || '-'}</span></div>
        </div>
      </div>`;
    });
    el.innerHTML = rows.join('');
  });
}

function renderAllTrades(trades) {
  const el = document.getElementById('all-trades');
  const list = trades.slice().reverse().filter((t) => flowFilter === 'all' || t.side === flowFilter);
  if (!list.length) {
    el.innerHTML = '<div class="empty">该筛选下暂无流水</div>';
    return;
  }
  // 顺序推导每笔卖出时的已实现盈亏
  const sorted = trades.slice().sort((a, b) => a.date.localeCompare(b.date) || (a.id || 0) - (b.id || 0));
  const avgBefore = {};
  const posTmp = {};
  const sellPnl = {};
  sorted.forEach((t) => {
    const key = t.code;
    if (t.side === 'buy') {
      if (!posTmp[key]) posTmp[key] = { shares: 0, avg: 0, cost: 0 };
      const p = posTmp[key];
      const ns = p.shares + t.shares;
      p.avg = ns ? (p.cost + t.shares * t.price + (t.fee || 0)) / ns : 0;
      p.cost = p.avg * ns;
      p.shares = ns;
    } else if (t.side === 'sell') {
      const p = posTmp[key] || { shares: 0, avg: 0 };
      sellPnl[t.id != null ? t.id : (t.date + t.code + t.shares + t.price)] = (t.price - p.avg) * t.shares - (t.fee || 0);
      p.shares = Math.max(0, p.shares - t.shares);
      p.cost = p.avg * p.shares;
    }
  });
  el.innerHTML = list.map((t) => {
    const sideTxt = { buy: '买入', sell: '卖出', pnl: '盈亏' }[t.side] || t.side;
    const cls = t.side === 'buy' ? 'up' : (t.side === 'sell' ? 'down' : (t.shares >= 0 ? 'up' : 'down'));
    const tkey = t.id != null ? t.id : (t.date + t.code + t.shares + t.price);
    let detail;
    if (t.side === 'pnl') {
      detail = `${t.shares >= 0 ? '+' : ''}${fmtMoney(t.shares)}`;
    } else {
      const sp = sellPnl[tkey];
      detail = `${t.shares}股 @${t.price}` + (sp !== undefined ? ` <span class="${sp >= 0 ? 'up' : 'down'}">已实现${sp >= 0 ? '+' : ''}${fmtMoney(sp)}</span>` : '');
    }
    return `<div class="trade-row"><span class="t-date">${t.date}</span><span class="${cls}">${sideTxt}</span><b>${t.name || t.code}</b><span>${detail}</span><span class="t-note">${t.reason || ''}</span></div>`;
  }).join('');
}

async function loadScanner() {
  try {
    const d = await loadJSON('data/scanner.json');
    const cands = d.candidates || [];
    $('#scanner-meta').textContent = d.updated_at ? `${d.criteria || ''} · 扫描${d.scanned}只 · 更新于${d.updated_at}` : '';
    if (!cands.length) {
      $('#scanner-list').innerHTML = '<div class="empty">暂无候选（无超跌+接近支撑的股票）</div>';
      return;
    }
    $('#scanner-list').innerHTML = cands.map((c) =>
      `<div class="cand-row">` +
      `<span class="cand-name">${c.name}<span class="cand-code">${c.code}</span></span>` +
      `<span class="cand-cell">评分 ${c.score}</span>` +
      `<span class="cand-cell">现价 ${c.price}</span>` +
      `<span class="cand-cell">支撑 ${c.support}</span>` +
      `<span class="cand-cell up">距支撑 ${c.dist_to_support}%</span>` +
      `</div>`
    ).join('');
  } catch (e) {
    $('#scanner-list').innerHTML = '<div class="empty">候选数据加载失败</div>';
  }
}

async function loadPicks() {
  try {
    const d = await loadJSON('data/picks.json').catch(() => null);
    if (!d || !d.candidates || !d.candidates.length) {
      $('#picks-meta').textContent = '暂无清单（收盘后自动生成）';
      $('#picks-list').innerHTML = '<div class="empty">收盘后自动生成 D 策略选股清单</div>';
      return;
    }
    $('#picks-meta').textContent =
      `${d.updated_at} · 市场：${(d.regime && d.regime.desc) || '-'} · ${d.strategy || ''}`;
    $('#picks-list').innerHTML = d.candidates.map((c, i) =>
      `<div class="cand-row">` +
      `<span class="cand-name">${i + 1}. ${c.name}<span class="cand-code">${c.code}</span></span>` +
      `<span class="cand-cell">${c.industry || ''}</span>` +
      `<span class="cand-cell">现价 ${c.price}</span>` +
      `<span class="cand-cell">支撑 ${c.support ?? '-'}</span>` +
      `<span class="cand-cell">${c.limit_up ? '⚠️涨停' : ''}</span>` +
      `</div>`
    ).join('');
  } catch (e) {
    $('#picks-list').innerHTML = '<div class="empty">选股清单加载失败</div>';
  }
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

let lastSnapTs = '';
let refreshTimer = null;

// 盘中静默自动刷新：只更新表格/评分，不打断图表与操作
async function refreshQuotes() {
  try {
    const snap = await loadJSON('data/snapshot.json');
    if (!snap || !snap.quotes || !snap.quotes.length) return;
    const ts = snap.updated_at || '';
    if (ts && ts === lastSnapTs) return;   // 数据没变就跳过重绘
    lastSnapTs = ts;
    await enrichQuotes(snap);
    window.__snapQuotes = snap.quotes;
    $('#updated').textContent = '更新于 ' + fmtTime(ts) + '（自动刷新）';
    renderWatchlist(snap.quotes);
    const nq = snap.quotes.find((x) => x.code === (activeStock && activeStock.code));
    if (nq) { activeStock = nq; drawFactor(nq); }
  } catch (e) { /* 静默失败，下轮再试 */ }
}

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(refreshQuotes, 60000);
}

async function init() {
  // 等图表库就绪（jsdelivr 失败会走 staticfile 回退），失败则表格仍可用
  await ensureEcharts();
  try {
    const cfg = await loadJSON('config.json').catch(() => null);
    if (cfg && cfg.strategy && cfg.strategy.active && !localStorage.getItem('strategyMode')) {
      strategyMode = normalizeStrategy(cfg.strategy.active);
    }
    const snap = await loadJSON('data/snapshot.json').catch(() => null);
    window.__snapQuotes = (snap && snap.quotes) || [];
    if (snap && snap.quotes && snap.quotes.length) {
      await enrichQuotes(snap);
      lastSnapTs = snap.updated_at || '';
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
  document.querySelectorAll('.strategy-toggle button').forEach((b) => {
    b.addEventListener('click', () => {
      strategyMode = b.dataset.strategy;
      localStorage.setItem('strategyMode', strategyMode);
      applyStrategyMode();
      const snap2 = window.__snapQuotes || [];
      if (snap2.length) loadChart(snap2[0]);
    });
  });
  // 表头排序
  document.querySelectorAll('th[data-sort]').forEach((th) => {
    th.addEventListener('click', () => {
      const k = th.dataset.sort;
      if (sortKey === k) sortDir = -sortDir;
      else { sortKey = k; sortDir = 1; }
      document.querySelectorAll('th[data-sort]').forEach((t) => t.classList.remove('sorted-asc', 'sorted-desc'));
      th.classList.add(sortDir === 1 ? 'sorted-asc' : 'sorted-desc');
      const snap2 = window.__snapQuotes || [];
      if (snap2.length) renderWatchlist(snap2);
    });
  });
  // 图表区交易按钮
  const tb = $('#chart-trade-btn');
  if (tb) tb.addEventListener('click', () => { if (activeStock) openTradeModal(activeStock); });
  // 复盘流水筛选
  document.querySelectorAll('#flow-filter button').forEach((b) => {
    b.addEventListener('click', () => {
      flowFilter = b.dataset.f;
      document.querySelectorAll('#flow-filter button').forEach((x) => x.classList.toggle('active', x === b));
      loadJSON('data/trades.json').catch(() => null).then((d) => {
        if (d) renderAllTrades(d.trades || []);
      });
    });
  });
  applyStrategyMode();
}

window.addEventListener('resize', () => { chart && chart.resize(); equityChart && equityChart.resize(); sentimentChart && sentimentChart.resize(); radarChart && radarChart.resize(); backtestChart && backtestChart.resize(); thresholdChart && thresholdChart.resize(); sectorHeatChart && sectorHeatChart.resize(); });
init();

// ===== 自选股管理 =====
function repoInfo() {
  const host = location.hostname;
  if (host.endsWith('.github.io')) {
    return { owner: host.split('.')[0], repo: location.pathname.split('/')[1] || '' };
  }
  return { owner: 'heroiscommom', repo: 'a-share-monitor' };
}

function openIssue(title, body) {
  const { owner, repo } = repoInfo();
  window.open(
    `https://github.com/${owner}/${repo}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`,
    '_blank'
  );
}

function addStock() {
  const input = $('#add-code');
  const code = input.value.trim();
  if (!/^\d{6}$/.test(code)) { alert('请输入 6 位股票代码'); return; }
  openIssue(`[自选股] 添加 ${code}`, `/add ${code}`);
  input.value = '';
}

function removeStock(code) {
  openIssue(`[自选股] 移除 ${code}`, `/remove ${code}`);
}

async function renderManageList() {
  const list = $('#manage-list');
  try {
    const cfg = await loadJSON('config.json');
    const wl = (cfg && cfg.watchlist) || [];
    list.innerHTML = '';
    if (!wl.length) {
      list.innerHTML = '<li class="empty">暂无自选股</li>';
      return;
    }
    wl.forEach((s) => {
      const li = document.createElement('li');
      const info = document.createElement('span');
      info.className = 'm-info';
      info.innerHTML =
        `<span class="m-code">${s.code}</span>` +
        `<span class="m-name">${s.name || ''}</span>` +
        `<span class="m-mkt">${s.market || ''}</span>`;
      const btn = document.createElement('button');
      btn.className = 'm-remove';
      btn.textContent = '移除';
      btn.addEventListener('click', () => removeStock(s.code));
      li.appendChild(info);
      li.appendChild(btn);
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = '<li class="empty">加载失败，请刷新重试</li>';
  }
}

function openManage() {
  $('#modal').classList.remove('hidden');
  renderManageList();
}
function closeManage() {
  $('#modal').classList.add('hidden');
}

$('#manage-btn').addEventListener('click', openManage);
$('#modal-close').addEventListener('click', closeManage);
$('#add-btn').addEventListener('click', addStock);
$('#add-code').addEventListener('keydown', (e) => { if (e.key === 'Enter') addStock(); });
$('#modal').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeManage(); });

function switchMode(mode) {
  chartMode = mode;
  $('#btn-intraday').classList.toggle('active', mode === 'intraday');
  $('#btn-daily').classList.toggle('active', mode === 'daily');
  if (activeStock) loadChart(activeStock);
}
$('#btn-intraday').addEventListener('click', () => switchMode('intraday'));
$('#btn-daily').addEventListener('click', () => switchMode('daily'));
