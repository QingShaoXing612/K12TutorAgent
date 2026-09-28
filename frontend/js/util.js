// js/util.js
// 通用工具：HTML 转义（防 XSS）+ 轻量 Markdown 渲染 + 格式化

export function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// 行内渲染：先转义，再处理 **加粗** / `行内代码`
function inline(s) {
  let h = escapeHtml(s);
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
  return h;
}

// 轻量 Markdown → HTML：段落 / 无序·有序列表 / 标题 / 加粗 / 行内代码
export function renderMarkdown(text) {
  const lines = String(text ?? '').split('\n');
  let html = '';
  let listType = null; // 'ul' | 'ol'
  const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };

  for (const raw of lines) {
    const line = raw.trim();
    if (/^[-*+]\s+/.test(line)) {
      if (listType !== 'ul') { closeList(); html += '<ul>'; listType = 'ul'; }
      html += '<li>' + inline(line.replace(/^[-*+]\s+/, '')) + '</li>';
    } else if (/^\d+[.)]\s+/.test(line)) {
      if (listType !== 'ol') { closeList(); html += '<ol>'; listType = 'ol'; }
      html += '<li>' + inline(line.replace(/^\d+[.)]\s+/, '')) + '</li>';
    } else if (/^#{1,4}\s+/.test(line)) {
      closeList();
      const n = line.match(/^#+/)[0].length;
      html += `<h${n}>` + inline(line.replace(/^#+\s+/, '')) + `</h${n}>`;
    } else if (line === '') {
      closeList();
    } else {
      closeList();
      html += '<p>' + inline(line) + '</p>';
    }
  }
  closeList();
  return html;
}

// 0.87 → '87%'
export function percent(x, digits = 0) {
  const n = Number(x);
  if (!isFinite(n)) return '—';
  return (n * 100).toFixed(digits) + '%';
}

// 简洁的 DOM 构建辅助
export function h(tag, className, html) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (html != null) el.innerHTML = html;
  return el;
}

// 通用字段渲染：string/number → 转义文本（换行转 <br>）；原始值数组 → <ul>；对象 → 兜底 JSON
export function fmt(v) {
  if (v == null || v === '') return '';
  if (Array.isArray(v)) {
    if (!v.length) return '';
    if (v.every((x) => typeof x === 'string' || typeof x === 'number')) {
      return '<ul>' + v.map((x) => '<li>' + escapeHtml(x) + '</li>').join('') + '</ul>';
    }
    return v.map(fmt).join('');
  }
  if (typeof v === 'object') {
    return '<pre style="white-space:pre-wrap;font-size:12.5px">' + escapeHtml(JSON.stringify(v, null, 2)) + '</pre>';
  }
  return escapeHtml(v).replace(/\n/g, '<br>');
}

// 生成 UUID v4（兼容非安全上下文：crypto.randomUUID 仅 HTTPS/localhost 可用，公网 HTTP 会缺失）
export function uuid() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

// 时间显示：ISO 字符串 → 浏览器本地时区 'YYYY-MM-DD HH:mm'（后端 PG 时间走 UTC，统一转北京时间）
export function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return String(iso).slice(0, 16).replace('T', ' ');
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
