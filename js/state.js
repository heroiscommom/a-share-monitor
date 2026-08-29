// ═══════════════════════════════════════════════════════════════
// 共享可变状态（2026-08 重构：从 app.js 抽出）
// 所有模块通过 import { state } 读写同一份状态，避免全局变量散落。
// ═══════════════════════════════════════════════════════════════

import { normalizeStrategy } from './util.js';

export const state = {
  // ECharts 实例
  chart: null,            // 主图（分时/日K）
  radarChart: null,
  backtestChart: null,
  thresholdChart: null,
  sectorHeatChart: null,
  sentimentChart: null,
  equityChart: null,

  // 当前选中股票与图表模式
  activeCode: null,
  chartMode: 'intraday',
  activeStock: null,

  // 策略（打法）
  strategyMode: normalizeStrategy(localStorage.getItem('strategyMode') || 'mean_reversion'),
  strategyList: [],       // strategies.json 动态列表
  dragonData: null,
  dragonMap: {},

  // 自选股表格排序
  sortKey: null,
  sortDir: 1,

  // 复盘视图
  flowFilter: 'all',
  reviewPos: {},          // 按股票汇总（含已清仓）
  reviewCash: 0,

  // 交易弹窗
  tmStock: null,

  // 自动刷新
  lastSnapTs: '',
  refreshTimer: null,
};
