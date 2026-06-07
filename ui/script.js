/* ============================================================
   MTD Secure — script.js  (v5 — Gerçek Backend Entegrasyonu)
   ============================================================ */
'use strict';

/* ---- DURUM DEĞİŞKENLERİ ---- */
let currentMode    = 'advanced';
let currentVariant = 'dark';
let currentPort    = 0;
let portSwitches   = 0;
let msgTotal       = 0;
let protoSwitches  = 0;
let connLost       = false;
let uptimeSeconds  = 0;
let lastMsgIndex   = 0;
let allMessages    = [];
let logs           = [];
let logClearOffset  = 0;
let backendInfo    = {};

/* Mod başına tema hafızası */
const modeVariant = { advanced: 'dark', simple: 'dark' };

/* ---- TEMA ---- */
function applyTheme() {
  const prefix = currentMode === 'advanced' ? 'adv' : 'sim';
  document.body.setAttribute('data-theme', prefix + '-' + currentVariant);
}

function setMode(mode) {
  currentMode = mode;
  currentVariant = modeVariant[mode];
  applyTheme();
  document.body.setAttribute('data-mode', mode);
  document.getElementById('topAdvBtn').classList.toggle('active', mode === 'advanced');
  document.getElementById('topSimBtn').classList.toggle('active', mode === 'simple');
  syncVariantBtns();
  renderMessages();
  renderLogs();
}

function setThemeVariant(variant) {
  currentVariant = variant;
  modeVariant[currentMode] = variant;
  applyTheme();
  syncVariantBtns();
}

function syncVariantBtns() {
  const isDark = currentVariant === 'dark';
  document.getElementById('topDarkBtn').classList.toggle('active', isDark);
  document.getElementById('topLightBtn').classList.toggle('active', !isDark);
}

/* ---- HTML ESKEYPLEYİCİ ---- */
function escapeHtml(text) {
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML;
}

function formatBytes(value) {
  if (!Number.isFinite(value) || value <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 ? 0 : 1;
  return `${size.toFixed(precision)} ${units[unitIndex]}`;
}

/* ---- GERÇEK API POLLING ---- */
async function pollStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    backendInfo = data;

    /* Bağlantı durumu */
    const wasLost = connLost;
    connLost = !data.connected;
    if (connLost !== wasLost) {
      document.getElementById('connBanner').classList.toggle('show', connLost);
      document.getElementById('statusDot').className = connLost ? 'dot dot-red' : 'dot dot-green';
      document.getElementById('statusText').textContent = connLost ? 'Disconnected' : 'Connected';
    }

    /* Port değişikliği — Toast göster */
    if (data.current_port !== currentPort && data.current_port > 0) {
      const oldPort = currentPort;
      currentPort = data.current_port;
      if (oldPort > 0) {
        showToast(`Port switched: ${oldPort} → ${currentPort} (${data.protocol})`);
      }
    }
    document.getElementById('portDisplay').textContent = 'Port: ' + (currentPort || '—');

    /* Metrikler */
    portSwitches  = data.port_switches;
    protoSwitches = data.proto_switches;
    msgTotal      = data.msg_count;
    uptimeSeconds = data.uptime;

    document.getElementById('mcPorts').textContent  = portSwitches;
    document.getElementById('mcProto').textContent  = protoSwitches;
    document.getElementById('mcMsgs').textContent   = msgTotal;
    document.getElementById('latVal').textContent    = data.latency_ms + 'ms';
    document.getElementById('mcLat').textContent     = data.latency_ms + 'ms';
    document.getElementById('mcLoss').textContent    = data.loss_rate + '%';
    document.getElementById('mcData').textContent    = formatBytes(data.data_transferred || 0);
    document.getElementById('mcDataSub').textContent = `${formatBytes(data.bytes_sent || 0)} sent / ${formatBytes(data.bytes_received || 0)} recv`;

    /* Uptime */
    const h = Math.floor(uptimeSeconds / 3600);
    const m = Math.floor((uptimeSeconds % 3600) / 60);
    const s = uptimeSeconds % 60;
    document.getElementById('mcUptime').textContent = [h, m, s].map(v => v.toString().padStart(2, '0')).join(':');

    /* Barlar */
    document.getElementById('mPorts').style.width = Math.min(portSwitches * 5, 100) + '%';
    document.getElementById('mMsgs').style.width  = Math.min(msgTotal * 4, 100) + '%';

    /* Canlı durum ve ayarlar */
    document.getElementById('protoChip').textContent = data.protocol || 'TCP';
    document.getElementById('roleChip').textContent = data.role || '—';
    document.getElementById('setIntervalVal').textContent = (data.interval || 0) + 's';
    document.getElementById('setProtoVal').textContent = data.protocol || 'TCP';
    document.getElementById('setPortRangeVal').textContent = `${data.port_range_min} – ${data.port_range_max}`;
    document.getElementById('setRoleVal').textContent = data.role || '—';
    document.getElementById('setStepVal').textContent = data.step ?? '—';

    /* Mod butonlarını senkronize et */
    const currentMode = data.mode || 'AUTO';
    document.querySelectorAll('#cfgModeGroup .cfg-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.mode === currentMode);
    });

    /* Port range inputlarına placeholder olarak mevcut değerleri koy */
    const minInput = document.getElementById('cfgMinPort');
    const maxInput = document.getElementById('cfgMaxPort');
    if (!minInput.matches(':focus')) minInput.placeholder = data.port_range_min;
    if (!maxInput.matches(':focus')) maxInput.placeholder = data.port_range_max;

    /* Bekleyen config değişiklikleri banner */
    const pendingBanner = document.getElementById('cfgPendingBanner');
    if (data.pending_config) {
      const entries = Object.entries(data.pending_config);
      const parts = entries.map(([step, changes]) => {
        const desc = Object.entries(changes).map(([k,v]) => `${k}=${v}`).join(', ');
        return `Step ${step}: ${desc}`;
      });
      document.getElementById('cfgPendingText').textContent = parts.join(' | ');
      pendingBanner.style.display = 'flex';
    } else {
      pendingBanner.style.display = 'none';
    }

  } catch (e) { /* sunucu henüz hazır değil */ }
}

async function pollMessages() {
  try {
    const res = await fetch('/api/messages?after=' + lastMsgIndex);
    const data = await res.json();
    if (data.messages.length > 0) {
      allMessages = allMessages.concat(data.messages);
      lastMsgIndex = data.total;
      renderMessages();
    }
  } catch (e) {}
}

async function pollLogs() {
  try {
    const res = await fetch('/api/logs');
    const data = await res.json();
    const allLogs = data.logs || [];
    logs = allLogs.slice(logClearOffset);
    if (currentMode === 'advanced') renderLogs();
  } catch (e) {}
}

/* ---- MESAJLARI ÇİZ ---- */
function renderMessages() {
  const container = document.getElementById('chatMessages');
  container.innerHTML = '';
  const isSimple = currentMode === 'simple';

  allMessages.forEach(m => {
    const wrap = document.createElement('div');
    if (isSimple) {
      wrap.className = `uf-msg-wrap ${m.dir}`;
      wrap.innerHTML = `<div class="uf-bubble">${escapeHtml(m.text)}</div><div class="uf-time">${m.time}</div>`;
    } else {
      wrap.className = `msg-wrap ${m.dir}`;
      const checks = m.dir === 'out' ? '<span class="check">✓✓</span>' : '';
      wrap.innerHTML = `
        <div class="msg-bubble">${escapeHtml(m.text)}</div>
        <div class="msg-meta">
          Proto: <span class="hl-cyan">${m.proto || '—'}</span> ·
          Port: <span class="hl">${m.port || '—'}</span>
        </div>
        <div class="msg-time-row">${m.time} ${checks}</div>`;
    }
    container.appendChild(wrap);
  });
  container.scrollTop = container.scrollHeight;
}

/* ---- LOGLARI ÇİZ ---- */
function renderLogs() {
  const c = document.getElementById('logContainer');
  if (!c) return;
  c.innerHTML = '';
  logs.forEach(l => {
    const el = document.createElement('div');
    el.className = 'log-entry';
    el.innerHTML = `
      <div class="log-time">${l.time}</div>
      <div class="log-ports log-${l.type}">${l.from} → ${l.to}</div>
      <div class="log-proto">${l.proto} · ok</div>`;
    c.appendChild(el);
  });
}

/* ---- MESAJ GÖNDER (GERÇEK) ---- */
async function sendMessage() {
  const input = document.getElementById('msgInput');
  const text  = input.value.trim();
  if (!text) return;
  input.value = '';

  try {
    await fetch('/api/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text })
    });
  } catch (e) {
    console.error('Mesaj gönderilemedi:', e);
  }
}

/* ---- TOAST BİLDİRİMİ ---- */
function showToast(message) {
  const toast = document.getElementById('toast');
  document.getElementById('toastMsg').textContent = message;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2500);
}

/* ---- LOG TEMİZLEME & DIŞA AKTARMA ---- */
function clearLogs() {
  logClearOffset += logs.length;
  logs = [];
  renderLogs();
}

function exportLogs() {
  const blob = new Blob([JSON.stringify(logs, null, 2)], { type:'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'mtd-port-logs.json';
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---- AYARLAR PANELİ ---- */
function openSettings()  { document.getElementById('settingsOverlay').classList.add('open'); }
function closeSettings() { document.getElementById('settingsOverlay').classList.remove('open'); }

/* ---- CONFIG DEĞİŞİKLİKLERİ ---- */
async function sendModeChange(mode) {
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ changes: { mode } })
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Mode → ${mode} (applies at step ${data.effective_step})`);
    } else {
      showToast(`Error: ${data.error || 'Failed to change mode'}`);
    }
  } catch (e) {
    console.error('Config update failed:', e);
  }
}

async function sendPortRangeChange() {
  const minVal = document.getElementById('cfgMinPort').value;
  const maxVal = document.getElementById('cfgMaxPort').value;
  if (!minVal || !maxVal) {
    showToast('Please enter Min and Max port values');
    return;
  }
  const min_p = parseInt(minVal, 10);
  const max_p = parseInt(maxVal, 10);
  if (isNaN(min_p) || isNaN(max_p) || min_p >= max_p || min_p < 1024 || max_p > 65535) {
    showToast('Invalid port range (1024-65535, min < max)');
    return;
  }
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ changes: { port_range_min: min_p, port_range_max: max_p } })
    });
    const data = await res.json();
    if (data.ok) {
      showToast(`Port: ${min_p}-${max_p} (applies at step ${data.effective_step})`);
      document.getElementById('cfgMinPort').value = '';
      document.getElementById('cfgMaxPort').value = '';
    } else {
      showToast(`Error: ${data.error || 'Failed to change port range'}`);
    }
  } catch (e) {
    console.error('Config update failed:', e);
  }
}

/* ---- BAŞLATMA ---- */
renderMessages();
renderLogs();
setInterval(pollStatus, 500);
setInterval(pollMessages, 500);
setInterval(pollLogs, 2000);
