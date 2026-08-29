// ═══════════════════════════════════════════════════════════════
// 自选股管理 + GitHub Issue 通道（2026-08 重构：从 app.js 抽出）
// 交易录入也复用这里的 openIssue（统一打开本仓库新建 Issue）。
// ═══════════════════════════════════════════════════════════════

import { $, esc, loadJSON } from './util.js';

export function repoInfo() {
  const host = location.hostname;
  if (host.endsWith('.github.io')) {
    return { owner: host.split('.')[0], repo: location.pathname.split('/')[1] || '' };
  }
  return { owner: 'heroiscommom', repo: 'a-share-monitor' };
}

export function openIssue(title, body) {
  const { owner, repo } = repoInfo();
  window.open(
    `https://github.com/${owner}/${repo}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`,
    '_blank'
  );
}

export function addStock() {
  const input = $('#add-code');
  const code = input.value.trim();
  if (!/^\d{6}$/.test(code)) { alert('请输入 6 位股票代码'); return; }
  openIssue(`[自选股] 添加 ${code}`, `/add ${code}`);
  input.value = '';
}

export function removeStock(code) {
  openIssue(`[自选股] 移除 ${code}`, `/remove ${code}`);
}

export async function renderManageList() {
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
        `<span class="m-code">${esc(s.code)}</span>` +
        `<span class="m-name">${esc(s.name || '')}</span>` +
        `<span class="m-mkt">${esc(s.market || '')}</span>`;
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

export function openManage() {
  $('#modal').classList.remove('hidden');
  renderManageList();
}

export function closeManage() {
  $('#modal').classList.add('hidden');
}

export function initManage() {
  $('#manage-btn').addEventListener('click', openManage);
  $('#modal-close').addEventListener('click', closeManage);
  $('#add-btn').addEventListener('click', addStock);
  $('#add-code').addEventListener('keydown', (e) => { if (e.key === 'Enter') addStock(); });
  $('#modal').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeManage(); });
}
