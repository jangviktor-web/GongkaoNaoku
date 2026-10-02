/* 考公脑库 · 版本自愈 Service Worker
 *
 * 背景：页面曾被下发 Cache-Control: public, max-age=86400，浏览器和 CDN 边缘节点
 * 都会把旧版本留 24 小时，导致"改完代码打开还是旧界面"、甚至页面卡在加载中。
 *
 * 策略（只做一件事：让裸链接也能拿到最新首页，不缓存任何业务数据）：
 *   仅拦截 / 与 /index.html 的导航请求，改取 /index.html?v=<ver>（新 URL，必回源）。
 *   js / css 已自带 ?v= 版本号，天然穿透缓存，这里刻意不碰，把风险降到最低；
 *   /api/* 与图片同样完全不拦截。
 * 任何一步失败都退回普通网络请求，最坏情况等同于没有 SW。
 */
const SW_VER = '20261001d';

self.addEventListener('install', () => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });
self.addEventListener('message', (e) => { if (e.data === 'skipWaiting') self.skipWaiting(); });

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  let url;
  try { url = new URL(req.url); } catch (err) { return; }
  if (url.origin !== self.location.origin) return;

  const isNav = req.mode === 'navigate' ||
    (req.headers.get('accept') || '').indexOf('text/html') >= 0;
  if (!isNav) return;
  if (url.pathname !== '/' && url.pathname !== '/index.html') return;

  e.respondWith(
    fetch('/index.html?v=' + SW_VER, { cache: 'reload' })
      .then((res) => (res && res.ok ? res : fetch(req)))
      .catch(() => fetch(req))
      .catch(() => Response.error())
  );
});
