// ═══════════════════════════════════════════════════════════════
// 复盘视图模块（2026-08 重构：从 app.js 抽出）
// 资产摘要 / 净值曲线 / 决策闭环 / 持仓汇总 / 全部流水。
// ═══════════════════════════════════════════════════════════════

import { $, fmtMoney, esc, loadJSON, derivePositions, deriveCash } from './util.js';
import { state } from './state.js';
import { renderSentiment } from './board.js';

export function loadReviewBasics() {
  // 预计算持仓/现金（复盘页与情绪卡共用）——原子赋值，避免中间态（现金未就绪时被读成 0）
  return Promise.all([
    loadJSON('data/trades.json').catch(() => null),
    loadJSON('config.json').catch(() => null),
  ]).then(([d, cfg]) => {
    const trades = (d && d.trades) || [];
    state.reviewPos = derivePositions(trades);
    state.reviewCash = deriveCash(trades, (cfg && cfg.capital && cfg.capital.cash) || 0);
    // 若情绪卡已渲染过（时序竞争），用完整数据重绘一次
    if (state.dragonData) renderSentiment();
    return { reviewPos: state.reviewPos, reviewCash: state.reviewCash };
  });
}

export function loadReview() {
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
    Object.values(state.reviewPos).forEach((p) => {
      const price = qm[p.code] || 0;
      mv += p.shares * price;
      floatPnl += (price - p.avg) * p.shares;
    });
    const total = state.reviewCash + mv;
    const realized = Object.values(state.reviewPos).reduce((s, p) => s + p.realized, 0);
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
      meta.textContent = `现金 ${fmtMoney(state.reviewCash)} ｜ 持仓市值 ${fmtMoney(mv)} ｜ ${trades.length} 笔交易`;
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
      if (!state.equityChart) state.equityChart = echarts.init(document.getElementById('equity-chart'));
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
        state.equityChart.setOption({
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
        `<div class="dc-card"><div class="dc-title">💡 建议命中率</div>${hitHtml}${facepalms.length ? `<div class="dc-detail">最近打脸：${esc(facepalms.slice(-2).join('；'))}</div>` : ''}</div>` +
        `<div class="dc-card"><div class="dc-title">📡 量化信号准确率</div>${sigHtml}</div>`;
    });
  });
}

export function renderStockSummary(trades) {
  const el = document.getElementById('stock-summary');
  const codes = Object.keys(state.reviewPos);
  if (!codes.length) { el.innerHTML = '<div class="empty">暂无交易记录</div>'; return; }
  loadJSON('data/snapshot.json').catch(() => null).then((snap) => {
    const qm = {};
    ((snap && snap.quotes) || []).forEach((q) => { qm[q.code] = q.price; });
    const rows = codes.map((code) => {
      const p = state.reviewPos[code];
      const price = qm[code] || 0;
      const float = (price - p.avg) * p.shares;
      const rCls = p.realized >= 0 ? 'up' : 'down';
      const fCls = float >= 0 ? 'up' : 'down';
      if (p.shares > 0) {
        return `<div class="pos-card">
          <div class="pos-head"><b>${esc(p.name)}</b><span>${esc(code)}</span></div>
          <div class="pos-grid">
            <div><label>交易</label><span>${p.buys}买/${p.sells}卖</span></div>
            <div><label>持仓</label><span>${p.shares}股 均${p.avg.toFixed(3)}</span></div>
            <div><label>浮动盈亏</label><span class="${fCls}">${float >= 0 ? '+' : ''}${fmtMoney(float)}</span></div>
            <div><label>累计已实现</label><span class="${rCls}">${p.realized >= 0 ? '+' : ''}${fmtMoney(p.realized)}</span></div>
          </div>
        </div>`;
      }
      return `<div class="pos-card pos-closed">
        <div class="pos-head"><b>${esc(p.name)}</b><span>${esc(code)} 已清仓</span></div>
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

export function renderAllTrades(trades) {
  const el = document.getElementById('all-trades');
  const list = trades.slice().reverse().filter((t) => state.flowFilter === 'all' || t.side === state.flowFilter);
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
    return `<div class="trade-row"><span class="t-date">${esc(t.date)}</span><span class="${cls}">${sideTxt}</span><b>${esc(t.name || t.code)}</b><span>${detail}</span><span class="t-note">${esc(t.reason || '')}</span></div>`;
  }).join('');
}
