// ═══════════════════════════════════════════════════════════════
// 通用工具与常量（2026-08 重构：从 app.js 抽出）
// 零依赖模块 —— 只提供纯函数与常量。
// ═══════════════════════════════════════════════════════════════

export const $ = (sel) => document.querySelector(sel);

// 各策略因子中文标签
export const FACTOR_LABELS = { rsi: '超卖', drawdown: '超跌', deviation: '偏离', position: '低位', volume: '量能', volatility: '稳定' };
export const MOM_LABELS = { mom20: '20日动量', mom60: '60日动量', rsi: 'RSI', pos60: '60日位置', vol_ratio: '量比', volatility: '波动' };
export const DRAGON_LABELS = { lbc: '连板高度', seal: '封板强度', fbt: '首板时间', zbc: '炸板', hs: '换手' };
export const MA_LABELS = { cross: '金叉强度', slope: '均线斜率', vol: '量能确认', hold: '回踩守住' };
export const SHRINK_LABELS = { trend: '趋势向上', shrink: '缩量程度', pullback: '回调深度', support: '近支撑' };

export const SIGNAL_CLASS = { strong: 's-strong', bullish: 's-bullish', neutral: 's-neutral', bearish: 's-bearish', weak: 's-weak' };

// 策略评分分级阈值（与后端 quant.py / monitor.py 保持一致）
export const STRATEGY_GRADES = {
  momentum: { strong: 70, bullish: 55, neutral: 40 },
  ma_golden_cross: { strong: 70, bullish: 55, neutral: 0 },
  shrink_pullback: { strong: 70, bullish: 55, neutral: 0 },
};

// 策略名归一化：yaml/strategies.json 里龙头叫 dragon_head，前端统一用 dragon
export function normalizeStrategy(name) {
  return name === 'dragon_head' ? 'dragon' : name;
}

// 按策略评分得出信号档位（strong/bullish/neutral/weak），集中管理阈值
export function gradeScore(strategy, score) {
  if (score === null || score === undefined || Number.isNaN(score)) return 'neutral';
  const g = STRATEGY_GRADES[strategy];
  if (!g) return 'neutral';
  if (score >= g.strong) return 'strong';
  if (score >= g.bullish) return 'bullish';
  if (g.neutral !== undefined && score >= g.neutral) return 'neutral';
  return 'weak';
}

export async function loadJSON(url, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { cache: 'no-store', signal: ctrl.signal });
    if (!r.ok) throw new Error(url + ' → ' + r.status);
    return await r.json();
  } finally { clearTimeout(timer); }
}

export function ensureEcharts(timeoutMs = 8000) {
  if (window.echarts) return Promise.resolve(true);
  return new Promise((res) => {
    const done = () => { window.removeEventListener('echarts-ready', done); res(!!window.echarts); };
    window.addEventListener('echarts-ready', done);
    setTimeout(done, timeoutMs);
  });
}

export function fmt(n, d = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-';
  return Number(n).toFixed(d);
}

export function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d)) return ts;
  const now = new Date();
  const p = (n) => String(n).padStart(2, '0');
  if (d.toDateString() === now.toDateString()) return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function fmtMoney(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-';
  return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

// HTML 转义：外部 API 数据（名称/消息等）进 innerHTML 前必须过一遍
const ESC_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ESC_MAP[c]);
}

// 涨停判断：创业板/科创板 20%，主板 10%（ST 5% 未纳入，按 9.8% 阈值）
export function isLimitUp(q) {
  const c = q.code || '';
  const thr = /^(300|301|688)/.test(c) ? 19.8 : 9.8;
  return q.change_pct != null && q.change_pct >= thr;
}

// ═══════════ 持仓推导（交易/复盘两视图共用，逻辑与后端 trade.py 一致：加权平均成本） ═══════════
export function derivePositions(trades) {
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
export function deriveCash(trades, baseCash) {
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
