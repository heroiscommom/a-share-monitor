// ═══════════════════════════════════════════════════════════════
// 交易模块（2026-08 重构：从 app.js 抽出）
// 交易视图（持仓/流水/录入）与交易弹窗。
// ═══════════════════════════════════════════════════════════════

import { $, fmt, fmtMoney, esc, loadJSON, derivePositions } from './util.js';
import { state } from './state.js';
import { openIssue } from './manage.js';

export function loadTrade() {
  loadJSON('data/trades.json').then((d) => {
    const trades = d.trades || [];
    // 推导持仓（与复盘视图共用 derivePositions，加权平均成本）
    const pos = derivePositions(trades);
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

export function renderPositions(pos, qm) {
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
        <div class="pos-head"><b>${esc(p.name)}</b><span>${esc(code)}</span></div>
        <div class="pos-grid">
          <div><label>持仓</label><span>${p.shares}股</span></div>
          <div><label>均价</label><span>${p.avg.toFixed(3)}</span></div>
          <div><label>现价</label><span>${price || '-'}</span></div>
          <div><label>浮动盈亏</label><span class="${pnlCls}">${fp >= 0 ? '+' : ''}${fmtMoney(fp)}</span></div>
        </div>
      </div>`;
    }
    return `<div class="pos-card pos-closed">
      <div class="pos-head"><b>${esc(p.name)}</b><span>${esc(code)} 已清仓</span></div>
      <div class="pos-grid"><div><label>已实现盈亏</label><span class="${p.realized >= 0 ? 'up' : 'down'}">${p.realized >= 0 ? '+' : ''}${fmtMoney(p.realized)}</span></div></div>
    </div>`;
  }).join('');
  el.innerHTML = rows || '<div class="empty">暂无持仓</div>';
  sumEl.textContent = `持仓市值 ${fmtMoney(mv)} ｜ 浮动 ${floatPnl >= 0 ? '+' : ''}${fmtMoney(floatPnl)}`;
}

export function renderTradeList(trades) {
  const el = document.getElementById('trade-list');
  if (!trades.length) { el.innerHTML = '<div class="empty">暂无交易流水</div>'; return; }
  el.innerHTML = trades.map((t) => {
    const sideTxt = { buy: '买入', sell: '卖出', pnl: '盈亏' }[t.side] || t.side;
    const cls = t.side === 'buy' ? 'up' : (t.side === 'sell' ? 'down' : '');
    if (t.side === 'pnl') {
      return `<div class="trade-row"><span class="t-date">${esc(t.date)}</span><span class="${t.shares >= 0 ? 'up' : 'down'}">${sideTxt}</span><b>${esc(t.name || t.code)}</b><span>${t.shares >= 0 ? '+' : ''}${fmtMoney(t.shares)}</span><span class="t-note">${esc(t.reason || '')}</span></div>`;
    }
    return `<div class="trade-row"><span class="t-date">${esc(t.date)}</span><span class="${cls}">${sideTxt}</span><b>${esc(t.name || t.code)}</b><span>${t.shares}股 @${t.price}</span><span class="t-note">${esc(t.reason || '')}</span></div>`;
  }).join('');
}

// ═══════════ 交易录入表单 ═══════════
export function buildTradeCommand() {
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

function sniperText(q) {
  // 从 sr_risk/alerts 找狙击点位(简化: 用支撑/压力)
  const sup = q.supports && q.supports[0];
  const res = q.resistances && q.resistances[0];
  const parts = [];
  if (sup) parts.push(`理想买 ${sup.price}${sup.held_rate != null ? '(守' + sup.held_rate + '%)' : ''}`);
  if (res) parts.push(`止盈 ${res.price}`);
  return parts.join(' ｜ ') || '暂无价位参考';
}

export function openTradeModal(q) {
  state.tmStock = q;
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
    const pos = derivePositions(trades);
    const hold = pos[q.code] ? pos[q.code].shares : 0;
    $('#tm-qty').placeholder = hold > 0 ? `股数(可卖 ${hold})` : '股数';
    if ($('#tm-side').value === 'sell' && hold > 0) {
      $('#tm-qty').value = hold;
    }
  });
  const m = document.getElementById('trade-modal');
  m.classList.remove('hidden');
}

export function closeTradeModal() {
  document.getElementById('trade-modal').classList.add('hidden');
}

export function submitTradeModal() {
  if (!state.tmStock) return;
  const side = document.getElementById('tm-side').value;
  const price = document.getElementById('tm-price').value.trim();
  const qty = document.getElementById('tm-qty').value.trim();
  const note = document.getElementById('tm-note').value.trim();
  if (!qty || Number(qty) <= 0) { alert('请输入股数'); return; }
  if (!price) { alert('请输入成交价位'); return; }
  const cmd = `/trade ${side} ${state.tmStock.code} ${qty} ${price} ${state.tmStock.name || ''} ${note ? note : (side === 'buy' ? '页面录入买入' : '页面录入卖出')}`.trim();
  openIssue('[trade] 交易录入', cmd + '\n\n（页面自动生成）');
  closeTradeModal();
}

export function initTradeForm() {
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
    openIssue('[trade] 交易录入', cmd + '\n\n（自动生成，提交后自动录入并关闭）');
  });
}
