/* DebugKnife — Real-time dashboard logic */
(function () {
  'use strict';

  const BASE = '/api';
  const POLL_MS = 5000;

  /* ── Theme toggle ── */
  function initTheme() {
    const saved = localStorage.getItem('dk-theme');
    const pref = saved || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
    document.documentElement.setAttribute('data-theme', pref);
    updateThemeIcon(pref);
  }
  function toggleTheme() {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('dk-theme', next);
    updateThemeIcon(next);
  }
  function updateThemeIcon(theme) {
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = theme === 'dark' ? '\u2600\uFE0F' : '\uD83C\uDF19';
  }

  /* ── Fetch helper ── */
  async function fetchJSON(path) {
    try {
      const r = await fetch(BASE + path, { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } catch { return null; }
  }

  /* ── Status color ── */
  function statusColor(s) {
    if (!s) return 'gray';
    s = s.toUpperCase();
    if (['CONNECTED', 'ENABLED', 'UP'].includes(s)) return 'green';
    if (['NOT_CONFIGURED', 'DISABLED', 'DEGRADED'].includes(s)) return 'yellow';
    return 'red';
  }
  function statusColorClass(s) {
    const c = statusColor(s);
    return c === 'gray' ? '' : c;
  }

  /* ── Single poll: /api/status ── */
  async function poll() {
    const data = await fetchJSON('/status');
    if (!data) return;

    // App info
    const verEl = document.getElementById('app-version');
    if (verEl) verEl.textContent = 'v' + data.app.version + ' \u00b7 ' + data.app.environment;

    // Overall dot
    const dot = document.getElementById('overall-dot');
    if (dot) {
      const anyDown = Object.values(data.infra).some(v => v === 'Down');
      dot.className = 'dot' + (anyDown ? ' degraded' : '');
    }

    // KPI: endpoints
    const epEl = document.getElementById('kpi-endpoints');
    if (epEl) epEl.textContent = data.endpoints;

    // KPI: PG
    const pgEl = document.getElementById('kpi-pg');
    if (pgEl) { pgEl.textContent = data.infra.pg_status; pgEl.className = 'kpi-value ' + statusColorClass(data.infra.pg_status); }

    // KPI: Redis
    const rdEl = document.getElementById('kpi-redis');
    if (rdEl) { rdEl.textContent = data.infra.redis_status; rdEl.className = 'kpi-value ' + statusColorClass(data.infra.redis_status); }

    // Status cards
    ['postgresql', 'redis', 'otel', 'prom'].forEach(name => {
      const key = name === 'postgresql' ? 'pg_status' : name === 'prom' ? 'prom_status' : name + '_status';
      const val = data.infra[key];
      if (!val) return;
      const card = document.getElementById('status-' + name);
      if (!card) return;
      const dotEl = card.querySelector('.status-dot');
      const detEl = card.querySelector('.status-detail');
      if (dotEl) dotEl.className = 'status-dot pulse ' + statusColor(val);
      if (detEl) detEl.textContent = val;
    });

    // Endpoints table
    const tbody = document.getElementById('endpoints-tbody');
    if (!tbody) return;
    const routes = (data.routes || [])
      .filter(m => m.name && !['openapi','swagger_ui_html','swagger_ui_redirect','redoc_html'].includes(m.name))
      .sort((a, b) => a.path.localeCompare(b.path));

    tbody.innerHTML = routes.map(r => {
      const methods = (r.methods || []).map(m =>
        '<span class="method-badge ' + m.toLowerCase() + '">' + m + '</span>'
      ).join(' ');
      const authTag = (r.path.includes('/debug/') || r.path === '/api/mgmt/threaddump')
        ? ' <span class="tag-auth">auth</span>' : '';
      return '<tr>' +
        '<td>' + methods + '</td>' +
        '<td><span class="path-text">' + r.path + '</span>' + authTag + '</td>' +
        '<td>' + (r.name || '') + '</td>' +
        '<td><button class="copy-btn" data-path="' + r.path + '" title="Copy URL">&#128203;</button></td>' +
        '</tr>';
    }).join('');
  }

  /* ── Copy to clipboard ── */
  function handleCopy(e) {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const path = btn.dataset.path;
    const url = location.origin + path;
    navigator.clipboard.writeText(url).then(() => {
      btn.classList.add('copied');
      btn.textContent = '\u2713';
      showToast('Copied: ' + url);
      setTimeout(() => { btn.classList.remove('copied'); btn.innerHTML = '&#128203;'; }, 1500);
    });
  }

  /* ── Toast ── */
  function showToast(msg) {
    let t = document.getElementById('toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'toast'; t.className = 'toast';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2000);
  }

  /* ── Uptime counter ── */
  const startTime = Date.now();
  function updateUptime() {
    const el = document.getElementById('kpi-uptime');
    if (!el) return;
    const diff = Math.floor((Date.now() - startTime) / 1000);
    const h = Math.floor(diff / 3600);
    const m = Math.floor((diff % 3600) / 60);
    const s = diff % 60;
    el.textContent = (h > 0 ? h + 'h ' : '') + m + 'm ' + s + 's';
  }

  /* ── Init ── */
  function init() {
    initTheme();
    document.getElementById('theme-toggle')?.addEventListener('click', toggleTheme);
    document.addEventListener('click', handleCopy);
    poll();
    setInterval(poll, POLL_MS);
    setInterval(updateUptime, 1000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
