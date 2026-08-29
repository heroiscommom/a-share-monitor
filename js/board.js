// ═══════════════════════════════════════════════════════════════
// 盯盘看板模块（2026-08 重构：从 app.js 抽出）
// 自选股表格、异动、回测、板块、扫描、选股清单、龙头梯队、情绪。
// ═══════════════════════════════════════════════════════════════

import {
  $, fmt, fmtTime, esc, isLimitUp, loadJSON, normalizeStrategy, SIGNAL_CLASS, gradeScore,
} from './util.js';
import { state } from './state.js';
import { loadChart, drawBacktestChart, drawThresholdChart, drawSectorHeatmap } from './charts.js';
import { openTradeModal } from './trade.js';

// 按当前打法取评分
export function scoreOf(q) {
  if (state.strategyMode === 'momentum') return q.momentum_score;
  if (state.strategyMode === 'ma_golden_cross') return q.ma_score;
  if (state.strategyMode === 'shrink_pullback') return q.shrink_score;
  if (state.strategyMode === 'dragon') { const dg = state.dragonMap[q.code]; return dg ? dg.dragon_score : null; }
  return q.score;
}

export function scoreSignalOf(q) {
  let score = null, signal = '-', sigKey = 'neutral';
  if (state.strategyMode === 'momentum') {
    score = q.momentum_score;
    signal = q.momentum_signal || '-';
    sigKey = gradeScore('momentum', score);
  } else if (state.strategyMode === 'ma_golden_cross') {
    score = q.ma_score;
    signal = q.ma_signal || '-';
    sigKey = gradeScore('ma_golden_cross', score);
  } else if (state.strategyMode === 'shrink_pullback') {
    score = q.shrink_score;
    signal = q.shrink_signal || '-';
    sigKey = gradeScore('shrink_pullback', score);
  } else if (state.strategyMode === 'dragon') {
    const dg = state.dragonMap[q.code];
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

// 策略共振徽标（跨策略多因子确认）
function resBadge(q) {
  const r = q && q.resonance;
  if (!r || !r.count || r.count < 2) return '';
  return `<span class="res-badge" title="${esc((r.list || []).join('+'))} 共振">🔥${r.count}</span>`;
}

export function selectRow(q) {
  document.querySelectorAll('tbody tr').forEach((r) => r.classList.remove('active'));
  const tr = document.querySelector(`tbody tr[data-code="${q.code}"]`);
  if (tr) tr.classList.add('active');
  loadChart(q);
}

export function renderWatchlist(quotes) {
  const tbody = $('tbody');
  const rows = quotes.slice();
  if (state.sortKey) {
    rows.sort((a, b) => {
      let va = a[state.sortKey], vb = b[state.sortKey];
      if (state.sortKey === 'score') { va = scoreOf(a); vb = scoreOf(b); }
      if (va === null || va === undefined) va = -Infinity;
      if (vb === null || vb === undefined) vb = -Infinity;
      return (va - vb) * state.sortDir;
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
      `<td>${esc(q.code)}</td>` +
      `<td><span class="stk-name">${esc(q.name || '-')}</span><button class="td-trade" title="买入/卖出 ${esc(q.name || q.code)}">💰</button></td>` +
      `<td class="num">${fmt(q.price)}</td>` +
      `<td class="num ${cls}">${cp >= 0 ? '+' : ''}${fmt(cp)}%</td>` +
      `<td class="num score">${score != null ? Number(score).toFixed(0) : '-'}</td>` +
      `<td><span class="sig ${sigCls}">${esc(signal)}</span>${resBadge(q)}</td>`;
    tr.addEventListener('click', () => selectRow(q));
    tr.querySelector('.td-trade').addEventListener('click', (e) => {
      e.stopPropagation();
      selectRow(q);
      openTradeModal(q);
    });
    tbody.appendChild(tr);
  });

  if (quotes.length && (!state.activeStock || !quotes.some((x) => x.code === state.activeStock.code))) {
    loadChart(quotes[0]);
  }
}

export function renderAlerts(items) {
  const ul = $('#alerts');
  ul.innerHTML = '';
  if (!items || !items.length) {
    ul.innerHTML = '<li class="empty">暂无异动记录</li>';
    return;
  }
  items.forEach((a) => {
    const li = document.createElement('li');
    const codeLabel = a.code ? `(${esc(a.code)})` : '';
    li.innerHTML =
      `<span class="time">${esc(a.time)}</span>` +
      `<span class="stock">${esc(a.name)}${codeLabel}</span>` +
      `<span class="msg">${esc(a.message)}</span>`;
    ul.appendChild(li);
  });
}

export async function loadBacktest() {
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
  return `<div class="sector-row"><span class="s-name">${esc(s.name)}</span><span class="s-pct ${cls}">${s.avg_change > 0 ? '+' : ''}${s.avg_change}%</span></div>`;
}

export async function loadSectors() {
  try {
    const d = await loadJSON('data/sectors.json');
    const secs = d.sectors || [];
    const anomalies = d.anomalies || [];
    $('#sector-meta').textContent = d.updated_at ? '更新于 ' + d.updated_at : '';
    $('#sector-anomalies').innerHTML = anomalies.length
      ? anomalies.map((a) => `<span class="sector-anom ${a.avg_change > 0 ? 'up' : 'down'}">${a.avg_change > 0 ? '📈' : '📉'} ${esc(a.name)} ${a.avg_change > 0 ? '+' : ''}${a.avg_change}%</span>`).join(' ')
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

const SENT_STYLE = { 冰点: 's-weak', 回暖: 's-neutral', 活跃: 's-bullish', 高潮: 's-strong' };

export function renderSentiment() {
  const barEl = $('#sentiment-bar');
  const chartEl = $('#sentiment-chart');
  const s = state.dragonData && state.dragonData.sentiment;
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
  if (state.reviewPos) {
    const qm = {};
    (window.__snapQuotes || []).forEach((q) => { qm[q.code] = q.price; });
    let mv = 0;
    Object.values(state.reviewPos).forEach((p) => { mv += p.shares * (qm[p.code] || 0); });
    const total = (state.reviewCash || 0) + mv;
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
      <div class="sent-item"><span class="sent-label">情绪状态</span><span class="sig ${SENT_STYLE[st] || 's-neutral'}">${esc(st)}${sm.direction ? '·' + esc(sm.direction) : ''}</span></div>
      <div class="sent-item"><span class="sent-label">最高连板</span><span class="sent-num">${t.max_lbc}板</span></div>
      <div class="sent-item"><span class="sent-label">炸板率</span><span class="sent-num">${zbcTxt}</span></div>
      <div class="sent-item"><span class="sent-label">5日/20日均</span><span class="sent-num">${s.trend.zt5}/${s.trend.zt20}</span></div>
      <div class="sent-item"><span class="sent-label">趋势</span><span class="sig ${s.trend.rising ? 's-strong' : 's-weak'}">${esc(s.trend.desc)}</span></div>
      ${posRatio != null ? `<div class="sent-item"><span class="sent-label">当前仓位</span><span class="sent-num">${posRatio.toFixed(1)}%</span></div>` : ''}
      <div class="sent-item sent-pos"><span class="sent-label">💡 仓位建议</span><span class="sent-pos-txt">${esc(sm.position_advice || '-')}${posHint}</span></div>
    </div>`;
  // 30 日涨停家数迷你曲线
  const hist = (s.history || []).slice(-30);
  if (hist.length >= 3 && chartEl) {
    if (!state.sentimentChart) state.sentimentChart = echarts.init(chartEl);
    const dates = hist.map((h) => h.date.replace(/^(\d{4})(\d{2})(\d{2})$/, '$2-$3'));
    const counts = hist.map((h) => h.zt_count);
    state.sentimentChart.setOption({
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
  } else if (chartEl && state.sentimentChart) {
    state.sentimentChart.clear();
  }
}

export function renderDragon() {
  const meta = $('#dragon-meta');
  const tiersEl = $('#dragon-tiers');
  const blEl = $('#dragon-breaklow');
  if (!state.dragonData) {
    meta.textContent = '暂无数据（收盘后采集）';
    tiersEl.innerHTML = '<div class="empty">等待采集</div>';
    blEl.innerHTML = '';
    return;
  }
  meta.textContent = `${state.dragonData.date} 涨停 ${state.dragonData.zt_count} 只`;
  renderSentiment();
  const order = ['S', 'A', 'B', 'C'];
  const tierNames = { S: 'S 龙头确认', A: 'A 龙头候选', B: 'B 观察池', C: 'C 参考' };
  tiersEl.innerHTML = order.map((k) => {
    const list = (state.dragonData.tiers && state.dragonData.tiers[k]) || [];
    const rows = list.slice(0, 12).map((it) =>
      `<tr>
        <td>${esc(it.name)}</td><td>${esc(it.code)}</td>
        <td class="num">${it.lbc}板</td>
        <td class="num score">${it.dragon_score}</td>
        <td class="num">${it.fund != null ? (it.fund / 1e8).toFixed(1) + '亿' : '-'}</td>
        <td class="num">${it.fbt ? String(it.fbt).padStart(6, '0').slice(0, 4).replace(/^(\d{2})(\d{2})/, '$1:$2') : '-'}</td>
        <td class="num">${it.zbc}</td>
        <td>${esc(it.hybk || '-')}</td>
      </tr>`).join('');
    return `<div class="tier-block">
      <h3>${tierNames[k]}（${list.length}）</h3>
      ${rows ? `<div class="dragon-table-wrap"><table class="dragon-table"><thead><tr><th>名称</th><th>代码</th><th>连板</th><th>强度分</th><th>封单</th><th>首板</th><th>炸板</th><th>板块</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="empty">无</div>'}
    </div>`;
  }).join('');

  const bl = state.dragonData.break_low || [];
  if (!bl.length) {
    blEl.innerHTML = '<div class="empty">今日无断板低吸候选</div>';
    return;
  }
  blEl.innerHTML = `<div class="dragon-table-wrap"><table class="dragon-table"><thead><tr><th>名称</th><th>代码</th><th>昨连板</th><th>昨强度</th><th>现价</th><th>支撑位</th><th>守住率</th><th>风险评分</th><th>板块</th></tr></thead><tbody>` +
    bl.map((c) =>
      `<tr>
        <td>${esc(c.name)}</td><td>${esc(c.code)}</td>
        <td class="num">${c.prev_lbc}板</td>
        <td class="num score">${c.prev_score || '-'}</td>
        <td class="num">${fmt(c.now_price)}</td>
        <td class="num">${c.support != null ? c.support : '-'}</td>
        <td class="num ${c.support_held != null && c.support_held >= 60 ? 'up' : ''}">${c.support_held != null ? c.support_held + '%' : '-'}</td>
        <td class="num ${c.risk_score != null && c.risk_score >= 65 ? 'up' : (c.risk_score != null && c.risk_score < 40 ? 'down' : '')}">${c.risk_score != null ? c.risk_score + '(' + esc(c.risk_level || '') + ')' : '-'}</td>
        <td>${esc(c.hybk || '-')}</td>
      </tr>`).join('') + `</tbody></table></div>`;
}

export function applyStrategyMode() {
  document.querySelectorAll('.strategy-toggle button').forEach((b) => {
    b.classList.toggle('active', b.dataset.strategy === state.strategyMode);
  });
  const snap = window.__snapQuotes || [];
  if (snap.length) renderWatchlist(snap);
}

export function renderStrategyButtons() {
  const wrap = document.querySelector('.strategy-toggle');
  if (!wrap || !state.strategyList.length) return;
  wrap.innerHTML = state.strategyList.map((s) => {
    const key = normalizeStrategy(s.name);
    return `<button data-strategy="${key}" class="${key === state.strategyMode ? 'active' : ''}">${esc(s.display_name)}</button>`;
  }).join('');
  wrap.querySelectorAll('button').forEach((b) => {
    b.addEventListener('click', () => {
      state.strategyMode = normalizeStrategy(b.dataset.strategy);
      localStorage.setItem('strategyMode', state.strategyMode);
      applyStrategyMode();
      const snap2 = window.__snapQuotes || [];
      if (snap2.length) loadChart(snap2[0]);
    });
  });
}

export function loadStrategies() {
  return loadJSON('data/strategies.json').then((d) => {
    state.strategyList = d.strategies || [];
    renderStrategyButtons();
  }).catch(() => { state.strategyList = []; });
}

export function loadDragon() {
  return loadJSON('data/dragon_head.json').then((d) => {
    state.dragonData = d;
    state.dragonMap = {};
    (['S', 'A', 'B', 'C']).forEach((k) => {
      ((d.tiers && d.tiers[k]) || []).forEach((it) => {
        it.tier = k;
        state.dragonMap[it.code] = it;
      });
    });
    renderDragon();
  }).catch(() => {
    state.dragonData = null;
    renderDragon();
  });
}

export async function loadScanner() {
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
      `<span class="cand-name">${esc(c.name)}<span class="cand-code">${esc(c.code)}</span></span>` +
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

export async function loadPicks() {
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
      `<span class="cand-name">${i + 1}. ${esc(c.name)}<span class="cand-code">${esc(c.code)}</span></span>` +
      `<span class="cand-cell">${esc(c.industry || '')}</span>` +
      `<span class="cand-cell">现价 ${c.price}</span>` +
      `<span class="cand-cell">支撑 ${c.support ?? '-'}</span>` +
      `<span class="cand-cell">${c.limit_up ? '⚠️涨停' : ''}</span>` +
      `</div>`
    ).join('');
  } catch (e) {
    $('#picks-list').innerHTML = '<div class="empty">选股清单加载失败</div>';
  }
}
