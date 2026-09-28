// js/api.js
// API 封装：token 注入 + 401 拦截 + 统一错误 + SSE 流式解析（fetch ReadableStream）

const TOKEN_KEY = 'teachmate_token';
const USER_KEY = 'teachmate_user';

export function getToken() { return localStorage.getItem(TOKEN_KEY); }
export function setToken(t) { localStorage.setItem(TOKEN_KEY, t); }
export function getUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY)) || null; }
  catch { return null; }
}
export function setUser(u) { localStorage.setItem(USER_KEY, JSON.stringify(u)); }
export function clearAuth() { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); }

export class ApiError extends Error {
  constructor(msg, status) { super(msg); this.name = 'ApiError'; this.status = status; }
}

// 从 FastAPI 错误体里尽量提取人类可读信息（detail 可能是 string / {message} / 422 数组）
function extractError(data) {
  if (!data) return '请求失败，请稍后重试';
  const d = data.detail;
  if (typeof d === 'string') return d;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  if (Array.isArray(d) && d.length) {
    return d.map(x => (x && x.msg) || JSON.stringify(x)).join('；');
  }
  return JSON.stringify(data);
}

function handleUnauthorized() {
  clearAuth();
  window.dispatchEvent(new CustomEvent('auth:logout'));
}

async function request(method, path, body, isForm = false) {
  const headers = {};
  if (getToken()) headers['Authorization'] = `Bearer ${getToken()}`;
  if (body != null && !isForm) headers['Content-Type'] = 'application/json';

  const res = await fetch(path, {
    method,
    headers,
    body: body == null ? undefined : (isForm ? body : JSON.stringify(body)),
  });

  if (res.status === 401) { handleUnauthorized(); throw new ApiError('登录已过期，请重新登录', 401); }

  let data = null;
  try { data = await res.json(); } catch { /* 非 JSON 响应 */ }

  if (!res.ok) throw new ApiError(extractError(data), res.status);
  return data;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body),
  postForm: (path, formData) => request('POST', path, formData, true),
  del: (path) => request('DELETE', path),
};

// SSE 流式：POST + ReadableStream 读取，按事件回调 onEvent(json)
export async function streamSSE(path, body, onEvent) {
  const res = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${getToken()}`,
    },
    body: JSON.stringify(body),
  });

  if (res.status === 401) { handleUnauthorized(); return; }
  if (!res.ok) {
    let data = null;
    try { data = await res.json(); } catch {}
    throw new ApiError(extractError(data), res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  const flushBlock = (block) => {
    for (const line of block.split('\n')) {
      if (line.startsWith('data:')) {
        const payload = line.slice(5).trim();
        if (!payload) continue;
        try { onEvent(JSON.parse(payload)); } catch { /* 忽略非 JSON 心跳 */ }
      }
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    console.log(`[read ${(performance.now() / 1000).toFixed(3)}s] ${value.length}B`);
    buf += decoder.decode(value, { stream: true });
    // 归一化行尾：sse_starlette 用 \r\n 分隔事件，统一成 \n，
    // 否则 indexOf('\n\n') 永远匹配不到，整段 body 攒到结尾一次性 flush → 答案瞬间弹出。
    buf = buf.replace(/\r\n/g, '\n');
    let idx;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      flushBlock(block);
      // 关键：每个事件之间让出一帧（~16ms）再继续，浏览器才有机会 paint。
      // setTimeout(0) 会立即回调，浏览器来不及绘制，token 全 append 完才统一画一帧 →
      // 答案「一瞬间弹出来」。16ms 约一帧，逐 token 渲染肉眼可见。
      await new Promise((r) => setTimeout(r, 16));
    }
  }
  if (buf.trim()) flushBlock(buf); // 收尾：无 \n\n 结尾的最后一个事件
}
