// js/router.js
// 极简 hash 路由：SPA 页面切换不触发服务器请求，后端无需 fallback

export function currentPath() {
  const h = (location.hash || '').replace(/^#/, '');
  return h || '/chat';
}

export function navigate(path) {
  const p = path.startsWith('/') ? path : '/' + path;
  if (currentPath() === p) {
    // 已在目标路由：手动派发，让 app 重跑一次当前视图（刷新标题/高亮/渲染）
    window.dispatchEvent(new Event('route:refresh'));
  } else {
    location.hash = '#' + p;
  }
}

export function initRouter(onRoute) {
  window.addEventListener('hashchange', () => onRoute(currentPath()));
  window.addEventListener('route:refresh', () => onRoute(currentPath()));
}
