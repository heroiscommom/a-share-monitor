// A股盯盘助手 - 前端逻辑
const $ = (sel) => document.querySelector(sel);

let chart = null;
let activeCode = null;

async function loadJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(url + ' → ' + r.status);
  return r.json();
}

function fmt(n, d = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-';
  return Number(n).toFixed(d);
}

function renderWatchlist(quotes) {
  const tbody = $('tbody');
  tbody.innerHTML = '';
  quotes.forEach((q) => {
    const cp = q.change_pct;
    const cls = cp >= 0 ? 'up' : 'down';
    const tr = document.createElement('tr');
    tr.dataset.code = q.code;
    tr.innerHTML =
      `<td>${q.code}</td>` +
      `<td>${q.name || '-'}</td>` +
      `<td class="num">${fmt(q.price)}</td>` +
      `<td class="num ${cls}">${cp >= 0 ? '+' : ''}${fmt(cp)}%</td>` +
      `<td class="num">${q.volume ? Number(q.volume).toLocaleString() : '-'}</td>`;
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
  activeCode = q.code;
  $('#chart-title').textContent = `${q.name || q.code} (${q.code}) 走势`;
  try {
    const hist = await loadJSON(`data/history/${q.code}.json`);
    if (hist && hist.length) drawChart(q, hist);
    else $('#chart').innerHTML = '<div class="empty">暂无历史数据，等待首次采集</div>';
  } catch (e) {
    $('#chart').innerHTML = '<div class="empty">暂无历史数据，等待首次采集</div>';
  }
}

function drawChart(q, hist) {
  if (!$('#chart')) return;
  if (!chart) chart = echarts.init($('#chart'));

  const dates = hist.map((h) => h.date);
  const closes = hist.map((h) => h.close);
  const volumes = hist.map((h) => h.volume);

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
      },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes,
        itemStyle: { color: '#5a6478' },
      },
    ],
  });
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
    li.innerHTML =
      `<span class="time">${a.time}</span>` +
      `<span class="stock">${a.name}(${a.code})</span>` +
      `<span class="msg">${a.message}</span>`;
    ul.appendChild(li);
  });
}

async function init() {
  try {
    const snap = await loadJSON('data/snapshot.json');
    $('#updated').textContent = snap.updated_at
      ? '更新于 ' + snap.updated_at
      : '等待首次采集';
    if (snap.quotes && snap.quotes.length) {
      renderWatchlist(snap.quotes);
    } else {
      $('tbody').innerHTML =
        '<tr><td colspan="5" class="empty">暂无数据，等待首次采集</td></tr>';
    }
    const al = await loadJSON('data/alerts.json');
    renderAlerts(al.items || []);
  } catch (e) {
    $('#updated').textContent = '加载失败：' + e.message;
  }
}

window.addEventListener('resize', () => chart && chart.resize());
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
