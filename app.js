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
const SIGNAL_CLASS = { strong: 's-strong', bullish: 's-bullish', neutral: 's-neutral', bearish: 's-bearish', weak: 's-weak' };

async function loadJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(url + ' → ' + r.status);
  return r.json();
}

function fmt(n, d = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-';
  return Number(n).toFixed(d);
}

function showChartEmpty(msg) {
  if (!chart) chart = echarts.init($('#chart'));
  chart.clear();
  chart.setOption({
    backgroundColor: 'transparent',
    graphic: { type: 'text', left: 'center', top: 'middle', style: { text: msg, fill: '#8b96ad', fontSize: 14 } },
  }, true);
}

function renderWatchlist(quotes) {
  const tbody = $('tbody');
  tbody.innerHTML = '';
  quotes.forEach((q) => {
    const cp = q.change_pct;
    const cls = cp >= 0 ? 'up' : 'down';
    const score = q.score;
    const sigCls = SIGNAL_CLASS[q.signal_key] || 's-neutral';
    const tr = document.createElement('tr');
    tr.dataset.code = q.code;
    tr.innerHTML =
      `<td>${q.code}</td>` +
      `<td>${q.name || '-'}</td>` +
      `<td class="num">${fmt(q.price)}</td>` +
      `<td class="num ${cls}">${cp >= 0 ? '+' : ''}${fmt(cp)}%</td>` +
      `<td class="num score">${score != null ? Number(score).toFixed(0) : '-'}</td>` +
      `<td><span class="sig ${sigCls}">${q.signal || '-'}</span></td>`;
    tr.addEventListener('click', () => {
      document.querySelectorAll('tbody tr').forEach((r) => r.classList.remove('active'));
      tr.classList.add('active');
      loadChart(q);
    });
    tbody.appendChild(tr);
  });

  if (quotes.length) loadChart(quotes[0]);
}

async function loadChart(q) {
  activeStock = q;
  activeCode = q.code;
  drawFactor(q);
  if (chartMode === 'intraday') await loadIntraday(q);
  else await loadDaily(q);
}

function drawFundamental(q) {
  const parts = [];
  if (q.sector) parts.push(`板块 ${q.sector}`);
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
  if (q.score != null) {
    $('#factor-score').innerHTML =
      `<span class="score-big">${Number(q.score).toFixed(0)}</span>` +
      `<span class="sig ${SIGNAL_CLASS[q.signal_key] || 's-neutral'}">${q.signal || ''}</span>`;
  } else {
    $('#factor-score').innerHTML = '<span class="empty">暂无评分</span>';
  }
  const factors = q.factors;
  if (!factors) {
    if (radarChart) radarChart.clear();
    return;
  }
  if (!radarChart) radarChart = echarts.init($('#factor-radar'));
  const inds = Object.keys(FACTOR_LABELS).filter((k) => factors[k] !== undefined);
  radarChart.setOption({
    backgroundColor: 'transparent',
    radar: {
      indicator: inds.map((k) => ({ name: `${FACTOR_LABELS[k]} ${factors[k]}`, max: 100 })),
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
  (q.resistances || []).forEach((r) => srLines.push({ yAxis: r.price, lineStyle: { color: '#ef232a', type: 'dashed', width: 1 }, label: { formatter: `卖${r.price}`, color: '#ef232a', position: 'insideEndTop', fontSize: 10 } }));
  (q.supports || []).forEach((s) => srLines.push({ yAxis: s.price, lineStyle: { color: '#14b143', type: 'dashed', width: 1 }, label: { formatter: `买${s.price}`, color: '#14b143', position: 'insideEndBottom', fontSize: 10 } }));

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

async function init() {
  try {
    const snap = await loadJSON('data/snapshot.json');
    const quantData = await loadJSON('data/quant.json').catch(() => null);
    const mfData = await loadJSON('data/moneyflow.json').catch(() => null);
    const sectorData = await loadJSON('data/sectors.json').catch(() => null);
    const srData = await loadJSON('data/support_resistance.json').catch(() => null);
    const sigData = await loadJSON('data/signals.json').catch(() => null);
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
      if (s) { q.score = s.score; q.signal = s.signal; q.signal_key = s.signal_key; q.factors = s.factors; }
      const m = mfMap[q.code];
      if (m) { q.netamount = m.netamount; q.r0_net = m.r0_net; }
      q.sector = sectorMap[q.code] || '';
      const sr = srMap[q.code];
      if (sr) { q.supports = sr.supports; q.resistances = sr.resistances; }
      const sg = sigMap[q.code];
      if (sg) { q.daily_buy = sg.daily_buy; q.daily_sell = sg.daily_sell; q.intraday_buy = sg.intraday_buy; q.intraday_sell = sg.intraday_sell; }
    });
    $('#updated').textContent = snap.updated_at
      ? '更新于 ' + snap.updated_at
      : '等待首次采集';
    if (snap.quotes && snap.quotes.length) {
      renderWatchlist(snap.quotes);
    } else {
      $('tbody').innerHTML =
        '<tr><td colspan="6" class="empty">暂无数据，等待首次采集</td></tr>';
    }
    const al = await loadJSON('data/alerts.json');
    renderAlerts(al.items || []);
    loadBacktest();
    loadSectors();
    loadScanner();
    loadPicks();
  } catch (e) {
    $('#updated').textContent = '加载失败：' + e.message;
  }
}

window.addEventListener('resize', () => { chart && chart.resize(); radarChart && radarChart.resize(); backtestChart && backtestChart.resize(); thresholdChart && thresholdChart.resize(); sectorHeatChart && sectorHeatChart.resize(); });
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
