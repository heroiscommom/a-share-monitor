// ═══════════════════════════════════════════════════════════════
// 图表模块（2026-08 重构：从 app.js 抽出）
// 主图（分时/日K）、量化因子雷达、回测/阈值/板块热力图等 ECharts 渲染。
// ═══════════════════════════════════════════════════════════════

import {
  $, fmt, loadJSON, isLimitUp,
  FACTOR_LABELS, MOM_LABELS, DRAGON_LABELS, MA_LABELS, SHRINK_LABELS, SIGNAL_CLASS, gradeScore,
} from './util.js';
import { state } from './state.js';

export function showChartEmpty(msg) {
  const el = $('#chart');
  if (!el) return;
  if (!window.echarts) {
    el.innerHTML = `<div class="empty" style="height:100%;display:flex;align-items:center;justify-content:center;">${msg}</div>`;
    return;
  }
  if (!state.chart) state.chart = echarts.init(el);
  state.chart.clear();
  state.chart.setOption({
    backgroundColor: 'transparent',
    graphic: { type: 'text', left: 'center', top: 'middle', style: { text: msg, fill: '#8b96ad', fontSize: 14 } },
  }, true);
}

export async function loadChart(q) {
  state.activeStock = q;
  state.activeCode = q.code;
  drawFactor(q);
  if (!window.echarts) { showChartEmpty('图表库加载失败，请刷新重试'); return; }
  if (state.chartMode === 'intraday') await loadIntraday(q);
  else await loadDaily(q);
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
  if (!state.chart) state.chart = echarts.init($('#chart'));
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

  state.chart.setOption({
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
  if (!state.chart) state.chart = echarts.init($('#chart'));

  const dates = hist.map((h) => h.date);
  const closes = hist.map((h) => h.close);
  const volumes = hist.map((h) => h.volume);
  const srLines = [];
  (q.resistances || []).forEach((r) => srLines.push({ yAxis: r.price, lineStyle: { color: '#ef232a', type: 'dashed', width: 1 }, label: { formatter: `卖${r.price}${r.held_rate != null ? ' 守' + r.held_rate + '%' : ''}`, color: '#ef232a', position: 'insideEndTop', fontSize: 10 } }));
  (q.supports || []).forEach((s) => srLines.push({ yAxis: s.price, lineStyle: { color: '#14b143', type: 'dashed', width: 1 }, label: { formatter: `买${s.price}${s.held_rate != null ? ' 守' + s.held_rate + '%' : ''}`, color: '#14b143', position: 'insideEndBottom', fontSize: 10 } }));

  state.chart.setOption({
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

export function drawFactor(q) {
  $('#factor-title').textContent = `${q.name || q.code} (${q.code}) 量化因子`;
  if (!window.echarts) {
    $('#factor-score').innerHTML = '';
    $('#factor-radar').innerHTML = '<div class="empty" style="height:100%;display:flex;align-items:center;justify-content:center;">图表库加载失败</div>';
    drawFundamental(q);
    return;
  }
  let factors = null, labels = FACTOR_LABELS, score = null, sigKey = 'neutral', sigText = '';
  if (state.strategyMode === 'momentum') {
    factors = q.momentum_indicators;
    labels = MOM_LABELS;
    score = q.momentum_score;
    sigText = q.momentum_signal || '';
    sigKey = gradeScore('momentum', score);
  } else if (state.strategyMode === 'ma_golden_cross') {
    factors = q.ma_factors;
    labels = MA_LABELS;
    score = q.ma_score;
    sigText = q.ma_signal || '';
    sigKey = gradeScore('ma_golden_cross', score);
  } else if (state.strategyMode === 'shrink_pullback') {
    factors = q.shrink_factors;
    labels = SHRINK_LABELS;
    score = q.shrink_score;
    sigText = q.shrink_signal || '';
    sigKey = gradeScore('shrink_pullback', score);
  } else if (state.strategyMode === 'dragon') {
    const dg = state.dragonMap[q.code];
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
    $('#factor-score').innerHTML = `<span class="empty">${state.strategyMode === 'dragon' ? '非涨停股无龙头分' : '暂无评分'}</span>`;
  }
  if (!factors || !Object.keys(factors).length) {
    // 无因子时显示占位提示（而非纯空白）
    if (state.radarChart) { try { state.radarChart.dispose(); } catch (e) {} state.radarChart = null; }
    const fel = $('#factor-radar');
    if (fel) fel.innerHTML = `<div class="empty" style="height:100%;display:flex;align-items:center;justify-content:center;">${
      state.strategyMode === 'dragon' ? (isLimitUp(q) ? '涨停未入池 — 无龙头分，切其他策略看因子' : '非涨停股无龙头分 — 切换到其他策略查看因子') : '暂无因子数据'}</div>`;
    drawFundamental(q);
    return;
  }
  const fel2 = $('#factor-radar');
  if (fel2) fel2.innerHTML = '';
  if (!state.radarChart) state.radarChart = echarts.init(fel2);
  const inds = Object.keys(labels).filter((k) => factors[k] !== undefined);
  state.radarChart.setOption({
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

export function drawBacktestChart(groups) {
  if (!state.backtestChart) state.backtestChart = echarts.init($('#backtest-chart'));
  const labels = groups.map((g) => g.label);
  const values = groups.map((g) => g.avg_return);
  const colors = values.map((v) => (v >= 0 ? '#ef232a' : '#14b143'));
  state.backtestChart.setOption({
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

export function drawThresholdChart(thresholds) {
  if (!state.thresholdChart) state.thresholdChart = echarts.init($('#threshold-chart'));
  const labels = thresholds.map((t) => `≥${t.threshold}`);
  const win = thresholds.map((t) => t.win_rate);
  state.thresholdChart.setOption({
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

export function drawSectorHeatmap(sectors) {
  if (!state.sectorHeatChart) state.sectorHeatChart = echarts.init($('#sector-heatmap'));
  const maxAbs = Math.max(...sectors.map((s) => Math.abs(s.avg_change)), 0.5);
  const data = sectors.map((s) => ({
    name: s.name,
    value: 1,
    change: s.avg_change,
    up: s.up,
    down: s.down,
    itemStyle: { color: heatColor(s.avg_change, maxAbs), borderColor: '#0f1420', borderWidth: 1, gapWidth: 1 },
  }));
  state.sectorHeatChart.setOption({
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

function heatColor(pct, maxAbs) {
  const t = Math.min(Math.abs(pct) / maxAbs, 1);
  const a = 0.2 + t * 0.8;
  return pct >= 0 ? `rgba(239, 35, 42, ${a.toFixed(2)})` : `rgba(20, 177, 67, ${a.toFixed(2)})`;
}
