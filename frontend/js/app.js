// js/app.js
// 应用入口：登录态管理 + 主布局渲染 + 导航 + hash 路由分发

import { api, getToken, setToken, setUser, getUser, clearAuth } from './api.js';
import { initRouter, currentPath, navigate } from './router.js';
import { renderChat } from './views/chat.js';
import { renderExam } from './views/exam.js';
import { renderLessonPrep } from './views/lesson-prep.js';
import { renderLearningAnalysis } from './views/learning-analysis.js';
import { renderHomework } from './views/homework.js';

const $ = (id) => document.getElementById(id);

const ROLE_LABEL = { teacher: '教师', student: '学生', admin: '管理员' };

const ICONS = {
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  exam: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h6"/></svg>',
  lesson: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
  analysis: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><rect x="7" y="12" width="3" height="6"/><rect x="12" y="8" width="3" height="10"/><rect x="17" y="5" width="3" height="13"/></svg>',
  homework: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
};

const NAV_ITEMS = [
  { path: '/chat', title: 'AI 助手', icon: ICONS.chat, roles: null },             // null = 所有角色可见
  { path: '/exam', title: '试卷批改', icon: ICONS.exam, roles: null },
  { path: '/lesson-prep', title: '智能备课', icon: ICONS.lesson, roles: ['teacher', 'admin'] },
  { path: '/learning-analysis', title: '学情报告', icon: ICONS.analysis, roles: ['teacher', 'admin'] },
  { path: '/homework', title: '作业', icon: ICONS.homework, roles: null },
];

const VIEWS = {
  '/chat': { title: 'AI 助手', viewId: 'view-chat', render: (el) => renderChat(el, getUser()) },
  '/exam': { title: '试卷批改', viewId: 'view-exam', render: (el) => renderExam(el, getUser()) },
  '/lesson-prep': { title: '智能备课', viewId: 'view-lesson-prep', render: renderLessonPrep },
  '/learning-analysis': { title: '学情报告', viewId: 'view-learning-analysis', render: renderLearningAnalysis },
  '/homework': { title: '作业', viewId: 'view-homework', render: (el) => renderHomework(el, getUser()) },
};

// ── 视图切换 ────────────────────────────────────────────────
function showLogin() {
  $('view-login').classList.remove('hidden');
  $('app').classList.add('hidden');
}

function showApp() {
  $('view-login').classList.add('hidden');
  $('app').classList.remove('hidden');
  renderNav();
  renderUser();
}

function renderNav() {
  const user = getUser();
  const role = user ? user.role : null;
  const items = NAV_ITEMS.filter((it) => !it.roles || it.roles.includes(role));
  const nav = $('nav');
  nav.innerHTML = items.map((it) =>
    `<a class="nav-item" data-path="${it.path}">${it.icon}<span>${it.title}</span></a>`
  ).join('');
  nav.querySelectorAll('.nav-item').forEach((a) =>
    a.addEventListener('click', () => navigate(a.dataset.path))
  );
}

function renderUser() {
  const user = getUser();
  if (!user) return;
  const name = user.username || '—';
  $('user-name').textContent = name;
  $('user-role').textContent = ROLE_LABEL[user.role] || user.role || '—';
  $('user-avatar').textContent = (name[0] || 'U').toUpperCase();
  const badge = $('role-badge');
  badge.textContent = ROLE_LABEL[user.role] || user.role || '—';
  badge.className = 'badge role-' + (user.role || 'student');
}

function onRoute(path) {
  // 剥离 query（如 /lesson-prep?subject=数学），纯 path 用于 VIEWS 匹配；query 保留在 location.hash 供 getQueryParam 读
  const purePath = path.split('?')[0];
  // 学生（非教师/管理员）访问教师专用页面 → 重定向到 AI 助手
  const user = getUser();
  const role = user ? user.role : null;
  const item = NAV_ITEMS.find((it) => it.path === purePath);
  if (item && item.roles && !item.roles.includes(role)) {
    navigate('/chat');
    return;
  }

  const view = VIEWS[purePath] || VIEWS['/chat'];
  const routePath = VIEWS[purePath] ? purePath : '/chat';

  document.querySelectorAll('.view').forEach((v) => v.classList.add('hidden'));
  const section = $(view.viewId);
  section.classList.remove('hidden');

  $('page-title').textContent = view.title;
  document.querySelectorAll('.nav-item').forEach((a) =>
    a.classList.toggle('active', a.dataset.path === routePath)
  );

  view.render(section);
}

// ── 登录 / 登出 ─────────────────────────────────────────────
function bindLogin() {
  $('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = $('login-username').value.trim();
    const password = $('login-password').value;
    const errEl = $('login-error');
    const btn = $('login-btn');
    errEl.textContent = '';
    btn.disabled = true;
    btn.textContent = '登录中…';
    try {
      const res = await api.post('/api/v1/auth/login', { username, password });
      setToken(res.access_token);
      setUser({ username, role: res.role, user_id: res.user_id });
      showApp();
      navigate('/chat');
    } catch (err) {
      errEl.textContent = err.message || '登录失败';
    } finally {
      btn.disabled = false;
      btn.textContent = '登 录';
    }
  });

  // 点演示账号快捷填充
  document.querySelectorAll('.hint-row').forEach((row) => {
    row.addEventListener('click', () => {
      $('login-username').value = row.dataset.u;
      $('login-password').value = row.dataset.p;
    });
  });
}

function bindLogout() {
  $('logout-btn').addEventListener('click', () => {
    clearAuth();
    location.reload();
  });
}

// token 失效（api.js 在 401 时触发）：整页重置回登录态
window.addEventListener('auth:logout', () => location.reload());

// ── 启动 ────────────────────────────────────────────────────
function init() {
  bindLogin();
  bindLogout();
  initRouter(onRoute);
  if (getToken()) {
    showApp();
    onRoute(currentPath());
  } else {
    showLogin();
  }
}

init();
