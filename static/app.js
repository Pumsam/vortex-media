/* ============================================================
   Vortex — фронтенд
   ============================================================ */
const $ = (id) => document.getElementById(id);

const els = {
  url: $('urlInput'),
  analyze: $('analyzeBtn'),
  paste: $('pasteBtn'),
  clear: $('clearBtn'),
  status: $('status'),
  statusText: $('statusText'),
  result: $('result'),
  thumb: $('thumb'),
  duration: $('duration'),
  platform: $('platform'),
  title: $('title'),
  meta: $('videoMeta'),
  description: $('description'),
  openOriginal: $('openOriginal'),
  formatList: $('formatList'),
  formatCount: $('formatCount'),
  filters: $('filters'),
  toast: $('toast'),
  settingsToggle: $('settingsToggle'),
  settingsPanel: $('settingsPanel'),
  proxy: $('proxyInput'),
  saveProxy: $('saveProxy'),
  cookiesStatus: $('cookiesStatus'),
};

let currentUrl = '';
let currentFormats = [];
let recommendedId = '';
let activeFilter = 'all';
let debounceTimer = null;
let lastAnalyzed = '';
let requestSeq = 0;

/* ---------- Утилиты ---------- */
function isValidUrl(value) {
  return /^https?:\/\/[\w\-]+(\.[\w\-]+)+[^\s]*$/i.test(value.trim());
}

function toast(message, type = '') {
  els.toast.textContent = message;
  els.toast.className = 'toast show ' + type;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { els.toast.className = 'toast'; }, 3600);
}

function setStatus(text, error = false) {
  if (!text) {
    els.status.classList.add('hidden');
    els.status.classList.remove('error');
    return;
  }
  els.status.classList.remove('hidden');
  els.status.classList.toggle('error', error);
  els.statusText.textContent = text;
}

function formatTime(seconds) {
  if (!seconds && seconds !== 0) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`;
}

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);
}

function iconDownload() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12M7 10l5 5 5-5M4 19h16"/></svg>';
}
function iconVideo() {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/></svg>';
}

/* ---------- Анализ ссылки ---------- */
async function analyze(url) {
  url = (url || '').trim();
  if (!url) {
    toast('Вставьте ссылку на видео.', 'error');
    els.url.focus();
    return;
  }
  if (!isValidUrl(url)) {
    toast('Похоже, это не корректная ссылка.', 'error');
    return;
  }

  currentUrl = url;
  lastAnalyzed = url;
  els.result.classList.add('hidden');
  setStatus('Ищем видео и читаем метаданные…');
  els.analyze.disabled = true;
  els.analyze.classList.add('loading');
  const seq = ++requestSeq;

  try {
    const response = await fetch('/api/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await response.json().catch(() => ({}));
    if (seq !== requestSeq) return;
    if (!response.ok) throw new Error(data.error || 'Не удалось получить данные.');
    renderResult(data);
    setStatus('');
    toast(`Найдено форматов: ${data.formats.length}`, 'success');
  } catch (err) {
    if (seq !== requestSeq) return;
    setStatus(err.message || 'Ошибка подключения.', true);
    toast(err.message || 'Ошибка подключения.', 'error');
  } finally {
    els.analyze.disabled = false;
    els.analyze.classList.remove('loading');
  }
}

/* ---------- Отрисовка результата ---------- */
function renderResult(data) {
  currentFormats = data.formats || [];
  recommendedId = data.recommended || '';

  els.thumb.src = data.thumbnail || '';
  els.thumb.onerror = () => { els.thumb.style.opacity = '0'; };
  els.thumb.style.opacity = '1';
  els.duration.textContent = formatTime(data.duration) || '';
  els.duration.style.display = data.duration ? '' : 'none';
  els.platform.textContent = data.platform || 'Сайт';
  els.title.textContent = data.title || 'Без названия';

  const meta = [];
  if (data.uploader) meta.push(escapeHtml(data.uploader));
  if (data.views) meta.push(`${data.views} просмотров`);
  if (data.upload_date) meta.push(escapeHtml(data.upload_date));
  els.meta.innerHTML = meta.map((m) => `<span>${m}</span>`).join('');

  els.description.textContent = data.description || '';
  els.description.style.display = data.description ? '' : 'none';
  els.openOriginal.href = data.web_url || '#';

  els.formatCount.textContent = `${currentFormats.length} из ${data.format_count || currentFormats.length}`;
  renderFormats();
  els.result.classList.remove('hidden');
  els.result.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderFormats() {
  const filtered = currentFormats.filter((f) => {
    if (activeFilter === 'all') return true;
    if (activeFilter === 'av') return f.has_video && f.has_audio;
    if (activeFilter === 'silent') return f.has_video && !f.has_audio;
    if (activeFilter === 'audio') return !f.has_video && f.has_audio;
    return true;
  });

  if (!filtered.length) {
    els.formatList.innerHTML = '<p style="color:var(--muted);font-size:13px;">Нет форматов для этого фильтра.</p>';
    return;
  }

  els.formatList.innerHTML = filtered.map((f) => {
    const recommended = f.id === recommendedId ? 'recommended' : '';
    const tag = recommended ? '<span class="recommended-tag">ВЫБОР</span>' : '';
    const size = f.size_text
      ? `<span class="fc-size">${escapeHtml(f.size_text)}</span>`
      : '<span class="fc-size unknown">размер неизвестен</span>';
    return `
      <div class="format-card ${recommended}" data-format="${escapeHtml(f.id)}">
        ${tag}
        <div class="fc-top">
          <span class="fc-quality">${escapeHtml(f.quality)}</span>
          <span class="fc-ext">${escapeHtml(f.ext)}</span>
        </div>
        <div class="fc-type">${iconVideo()} ${escapeHtml(f.type_label)}${f.fps ? ' · ' + Math.round(f.fps) + ' fps' : ''}</div>
        <div class="fc-note" title="${escapeHtml(f.note || '')}">${escapeHtml(f.note || f.ext)}</div>
        <div class="fc-size-row"><span class="fc-size-label">Размер файла</span>${size}</div>
        <button class="download-btn" type="button">${iconDownload()}<span>Скачать</span></button>
      </div>`;
  }).join('');
}

/* ---------- Скачивание с прогрессом ---------- */
function parseFilename(disposition) {
  if (!disposition) return null;
  let m = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (m) {
    try { return decodeURIComponent(m[1].trim().replace(/^"|"$/g, '')); } catch (e) { /* noop */ }
  }
  m = disposition.match(/filename="?([^";]+)"?/i);
  return m ? m[1].trim() : null;
}

function ensureProgress(card) {
  let fill = card.querySelector('.progress-fill');
  if (!fill) {
    const wrap = document.createElement('div');
    wrap.className = 'progress-wrap';
    wrap.innerHTML = '<div class="progress-track"><div class="progress-fill"></div></div>';
    card.appendChild(wrap);
    fill = wrap.querySelector('.progress-fill');
  }
  return fill;
}

async function downloadFormat(card) {
  const formatId = card.dataset.format;
  const format = currentFormats.find((f) => f.id === formatId) || { ext: 'mp4' };
  const button = card.querySelector('.download-btn');
  const original = button.innerHTML;
  const fill = ensureProgress(card);

  button.disabled = true;
  button.innerHTML = '<span>Подготовка…</span>';
  fill.style.width = '4%';

  try {
    const params = new URLSearchParams({ url: currentUrl, format: formatId });
    const response = await fetch('/api/download?' + params.toString());
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || `Ошибка сервера (${response.status})`);
    }

    const total = Number(response.headers.get('Content-Length') || 0);
    const reader = response.body.getReader();
    const chunks = [];
    let received = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      const pct = total
        ? (received / total) * 100
        : format.size
          ? Math.min(96, (received / format.size) * 100)
          : Math.min(96, 4 + chunks.length * 2);
      fill.style.width = Math.max(4, Math.min(100, pct)) + '%';
    }
    fill.style.width = '100%';

    const blob = new Blob(chunks, { type: response.headers.get('Content-Type') || 'video/mp4' });
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objectUrl;
    a.download = parseFilename(response.headers.get('Content-Disposition')) || `video.${format.ext}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 15000);

    button.innerHTML = '<span>Готово ✓</span>';
    toast('Файл сохранён', 'success');
    setTimeout(() => { fill.style.width = '0%'; }, 1200);
  } catch (err) {
    fill.style.width = '0%';
    toast(err.message || 'Не удалось скачать файл.', 'error');
  } finally {
    button.disabled = false;
    setTimeout(() => { button.innerHTML = original; }, 1800);
  }
}

/* ---------- События ---------- */
els.analyze.addEventListener('click', () => analyze(els.url.value));

els.url.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') analyze(els.url.value);
});

els.url.addEventListener('input', () => {
  els.clear.classList.toggle('hidden', !els.url.value);
  clearTimeout(debounceTimer);
  const value = els.url.value.trim();
  if (isValidUrl(value) && value !== lastAnalyzed) {
    debounceTimer = setTimeout(() => analyze(value), 850);
  }
});

els.url.addEventListener('paste', () => {
  clearTimeout(debounceTimer);
  setTimeout(() => {
    const value = els.url.value.trim();
    if (isValidUrl(value) && value !== lastAnalyzed) analyze(value);
  }, 80);
});

els.paste.addEventListener('click', async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      els.url.value = text.trim();
      els.clear.classList.remove('hidden');
      analyze(text);
    } else {
      toast('Буфер обмена пуст.', 'error');
    }
  } catch (e) {
    toast('Не удалось прочитать буфер. Вставьте ссылку вручную (Ctrl+V).', 'error');
    els.url.focus();
  }
});

els.clear.addEventListener('click', () => {
  els.url.value = '';
  els.clear.classList.add('hidden');
  lastAnalyzed = '';
  els.result.classList.add('hidden');
  setStatus('');
  els.url.focus();
});

els.filters.addEventListener('click', (e) => {
  const btn = e.target.closest('.filter');
  if (!btn) return;
  activeFilter = btn.dataset.filter;
  els.filters.querySelectorAll('.filter').forEach((b) => b.classList.toggle('active', b === btn));
  renderFormats();
});

els.formatList.addEventListener('click', (e) => {
  const btn = e.target.closest('.download-btn');
  if (!btn) return;
  downloadFormat(btn.closest('.format-card'));
});

/* ---------- Настройки ---------- */
els.settingsToggle.addEventListener('click', () => {
  els.settingsPanel.classList.toggle('hidden');
});

els.saveProxy.addEventListener('click', async () => {
  const proxy = els.proxy.value.trim();
  try {
    await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proxy }),
    });
    toast(proxy ? 'Прокси сохранён. Повторите анализ.' : 'Прокси отключён.', 'success');
  } catch (e) {
    toast('Не удалось сохранить настройки.', 'error');
  }
});

/* ---------- Стартовая проверка сервера ---------- */
fetch('/api/health').then((r) => r.json()).then((d) => {
  if (d.ytdlp) $('version').textContent = 'yt-dlp ' + d.ytdlp;
}).catch(() => {});

fetch('/api/config').then((r) => r.json()).then((c) => {
  els.proxy.value = c.proxy || '';
  els.cookiesStatus.textContent = c.cookies
    ? '✓ cookies.txt подключён'
    : 'cookies.txt не найден — если сайт требует вход, добавьте его в папку проекта.';
}).catch(() => {});
