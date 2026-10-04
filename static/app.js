/* 考公脑库 · 间隔重复学习系统 - 前端 SPA (vanilla) */

/* ---------- 版本自愈 ----------
   旧版页面被浏览器 / CDN 边缘节点用 max-age=86400 强缓存，导致"改了代码但打开还是旧界面"。
   这里让脚本核对页面里的版本标记：不一致就一次性跳到带版本号的新地址，
   绕过浏览器与边缘节点的两层缓存。kg_healed 防止极端情况下死循环。 */
const APP_VER = '20261005a';
let HEALING = false;
(function selfHeal() {
  try {
    const meta = document.querySelector('meta[name="kg-app-ver"]');
    if ((meta && meta.getAttribute('content')) === APP_VER) return;
    if (sessionStorage.getItem('kg_healed') === APP_VER) return;
    sessionStorage.setItem('kg_healed', APP_VER);
    HEALING = true;
    location.replace('/index.html?v=' + APP_VER + (location.hash || ''));
  } catch (e) { }
})();

/* 注册 Service Worker：让页面导航与 js/css 一律重新校验网络，
   彻底摆脱本机 HTTP 缓存里的旧版本（仅 https 下生效，失败不影响正常使用）。 */
(function regSW() {
  try {
    if (!('serviceWorker' in navigator) || location.protocol !== 'https:') return;
    navigator.serviceWorker.register('/sw.js').catch(() => { });
  } catch (e) { }
})();

/* ---------- 账号：用户名 + 密码登录，token 鉴权（服务端按账号隔离数据） ---------- */
let TOKEN = '';
let ME = null;   // 当前登录账号 {id,name,username}
try { TOKEN = localStorage.getItem('kg_token') || ''; } catch (e) { }
function tok() { return TOKEN || ''; }
function setTok(t) {
  TOKEN = t || '';
  try { if (t) localStorage.setItem('kg_token', t); else localStorage.removeItem('kg_token'); } catch (e) { }
}
function withU(p) { return p + (p.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(tok()); }

async function API(p) {
  const r = await fetch(withU(p));
  if (r.status === 401) { onExpired(); return {}; }
  return r.json();
}
async function POST(p, b) {
  const r = await fetch(p, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({}, b, { token: tok() })),
  });
  if (r.status === 401) { onExpired(); return {}; }
  return r.json();
}
function onExpired() { setTok(''); showLogin('登录已失效，请重新登录'); }
const esc = (s) => (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const app = document.getElementById('app');
let BOOT = null;

const state = { session: null, exam: null };

/* ---------- 功能2：限时模式配置 ---------- */
const LIMIT_DEFAULT = {
  '言语理解与表达': 45, '判断推理': 40, '数量关系': 90,
  '资料分析': 60, '常识判断': 30, '政治理论': 45,
};
function loadLimits() {
  try { return Object.assign({}, LIMIT_DEFAULT, JSON.parse(localStorage.getItem('kg_limits') || '{}')); }
  catch (e) { return Object.assign({}, LIMIT_DEFAULT); }
}
function saveLimits(o) { try { localStorage.setItem('kg_limits', JSON.stringify(o)); } catch (e) { } }
let LIMIT_ON = false;
try { LIMIT_ON = localStorage.getItem('kg_limit_on') === '1'; } catch (e) { }

/* ---------- 功能4：搜索历史（最近 5 条） ---------- */
function loadSearchHist() {
  try { const a = JSON.parse(localStorage.getItem('kg_search_hist') || '[]'); return Array.isArray(a) ? a.slice(0, 5) : []; }
  catch (e) { return []; }
}
function pushSearchHist(kw) {
  try {
    const a = loadSearchHist().filter(x => x !== kw); a.unshift(kw);
    localStorage.setItem('kg_search_hist', JSON.stringify(a.slice(0, 5)));
  } catch (e) { }
}

/* ---------- 功能5：下载 Markdown ---------- */
function downloadMd(filename, text) {
  const blob = new Blob([text || ''], { type: 'text/markdown;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

const MODE_LABEL = { new: '新题', mix: '智能复习', review: '复习', wrong: '错题重练', wrongredo: '错题重练', fav: '收藏练习', material: '材料组题' };

/* ---------- 练习 session 本地快照（继续上次刷题） ---------- */
const SS_KEY = 'kg_session_v1';
function saveSession() {
  try {
    const S = state.session; if (!S || !S.queue || !S.queue.length) return;
    localStorage.setItem(SS_KEY, JSON.stringify({
      mode: S.mode, cfg: S.cfg, queue: S.queue, idx: S.idx,
      tally: S.tally, marked: Array.from(S.marked || []),
      kind: 'study', savedAt: Date.now(),
    }));
  } catch (e) { /* localStorage 不可用（隐私模式等）时静默降级 */ }
}
function loadSession() {
  try {
    const s = localStorage.getItem(SS_KEY); if (!s) return null;
    const o = JSON.parse(s);
    if (!o || o.kind !== 'study' || !o.queue || !o.queue.length) return null;
    if (o.idx >= o.queue.length) return null; // 已全部做完
    return o;
  } catch (e) { return null; }
}
function loadSeen() {
  try { const a = JSON.parse(localStorage.getItem('kg_seen') || '[]'); return Array.isArray(a) ? a : []; }
  catch (e) { return []; }
}
function addSeen(qids) {
  try {
    const s = new Set(loadSeen());
    qids.forEach(q => s.add(String(q)));
    localStorage.setItem('kg_seen', JSON.stringify(Array.from(s).slice(-800))); // 仅保留近期 800 个
  } catch (e) { /* 隐私模式降级 */ }
}

function clearSession() { try { localStorage.removeItem(SS_KEY); } catch (e) { } }
function resumeSession() {
  const o = loadSession();
  if (!o) { clearSession(); return; }
  // 修复「继续上次刷题」按了没反应：恢复路径必须补齐 session 全部字段，
  // 否则 renderCard 里 S.marked.has() / S.times.push() 抛 TypeError，页面停在原地。
  state.session = {
    mode: o.mode, cfg: o.cfg || {}, queue: o.queue, idx: o.idx,
    phase: 'question', chosen: null, full: null,
    tally: o.tally || { done: 0, right: 0, wrong: 0 },
    marked: new Set(o.marked || []),
    limitOn: !!(o.cfg && o.cfg.limitOn),
    limits: (o.cfg && o.cfg.limits) || loadLimits(),
    ids: (o.cfg && o.cfg.ids) || null,
    startTs: Date.now(), times: [], timeoutN: 0, qStart: Date.now(),
  };
  try {
    renderCard();
  } catch (e) {
    // 快照损坏 / 结构不兼容：清理后回总览，保证按钮永远不会「没反应」
    console.error('恢复练习失败，已清理本地快照', e);
    state.session = null;
    clearSession();
    try { viewDashboard(); } catch (e2) { location.hash = '#/'; }
  }
}
function resumeAgo(ts) {
  if (!ts) return '—';
  const sec = Math.floor((Date.now() - ts) / 1000);
  if (sec < 60) return '刚刚';
  if (sec < 3600) return Math.floor(sec / 60) + ' 分钟前';
  if (sec < 86400) return Math.floor(sec / 3600) + ' 小时前';
  const d = new Date(ts);
  const p = n => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* ---------- router ---------- */
function nav() {
  const h = (location.hash || '#/').split('?')[0];
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href').split('?')[0] === h));
  if (h === '#/') return viewDashboard();
  if (h === '#/study') return viewStudy();
  if (h === '#/review') return startSession({ mode: 'review' });
  if (h === '#/browse') return viewBrowse();
  if (h === '#/kadian') return viewKadian();
  if (h === '#/wrongbook') return viewWrongbook();
  if (h === '#/favorites') return viewFavorites();
  if (h === '#/exam') return viewExam();
  if (h === '#/materials') return viewMaterials();
  if (h === '#/doubts') return viewDoubts();
  if (h === '#/stats') return viewStats();
  if (h === '#/settings') return viewSettings();
  if (h === '#/detail') return viewDetail(+new URLSearchParams(location.hash.split('?')[1]).get('id'));
  viewDashboard();
}
window.addEventListener('hashchange', nav);

/* ---------- side stats ---------- */
async function refreshSide() {
  try {
    const s = await API('/api/stats');
    document.getElementById('side-stats').innerHTML = `
      <div class="stat-chip"><span>待复习</span><b style="color:var(--warn)">${s.due_review}</b></div>
      <div class="stat-chip"><span>未学新题</span><b style="color:var(--brand)">${s.new}</b></div>
      <div class="stat-chip"><span>已学</span><b>${s.learned}</b></div>
      <div class="stat-chip"><span>今日已练</span><b style="color:var(--ok)">${s.studied_today}</b></div>
      <div class="stat-chip"><span>错题本</span><b style="color:var(--bad)">${s.wrong}</b></div>
      <div class="stat-chip"><span>收藏</span><b style="color:var(--purple)">${s.fav}</b></div>`;
    return s;
  } catch (e) { return null; }
}

function loading(t = '加载中…') { app.innerHTML = `<div class="loading">${t}</div>`; }
function fatal(e) { app.innerHTML = `<div class="card" style="border-color:var(--bad)">出错了：${esc(String(e))}
  <div class="row" style="margin-top:14px"><button class="btn ghost" onclick="location.reload()">重试</button></div></div>`; }

/* ---------- dashboard ---------- */
async function viewDashboard() {
  loading();
  const s = await API('/api/stats');
  await refreshSide();
  const resumeBtn = (() => {
    try {
      const o = loadSession();
      if (!o || o.idx >= o.queue.length) return '';
      const left = o.queue.length - o.idx;
      const label = MODE_LABEL[o.mode] || '练习';
      const t = resumeAgo(o.savedAt);
      return `<a class="btn big resume" href="#" onclick="try{resumeSession()}catch(e){console.error(e)};return false;">⏯ 继续上次刷题（${label} · 剩 ${left} 题）· 上次 ${t}</a>`;
    } catch (e) { return ''; }
  })();
  const dueMod = {}; s.due_by_module.forEach(d => dueMod[d.m] = d.n);
  const mastery = s.mastery; const mkeys = ['学习中', '初记', '短期', '长期', '稳固'];
  const maxM = Math.max(1, ...mkeys.map(k => mastery[k] || 0));
  app.innerHTML = `
  <h1>总览面板</h1><p class="sub">共 ${BOOT.total.toLocaleString()} 道行测真题 · 逐题深度标注 · 按记忆曲线安排复习</p>
  <div class="grid g4">
    <div class="card kpi warn"><div class="n">${s.due_review}</div><div class="l">🔔 今日待复习</div></div>
    <div class="card kpi brand"><div class="n">${s.new.toLocaleString()}</div><div class="l">✨ 未学新题</div></div>
    <div class="card kpi ok"><div class="n">${s.studied_today}</div><div class="l">✅ 今日已练习</div></div>
    <div class="card kpi purple"><div class="n">${s.learned.toLocaleString()}</div><div class="l">📈 已纳入复习</div></div>
  </div>
  <div class="grid g4" style="margin-top:16px">
    <a class="card kpi" href="#/wrongbook" style="text-decoration:none"><div class="n" style="color:var(--bad)">${s.wrong}</div><div class="l">📕 错题本</div></a>
    <a class="card kpi" href="#/favorites" style="text-decoration:none"><div class="n" style="color:var(--purple)">${s.fav}</div><div class="l">⭐ 我的收藏</div></a>
    <a class="card kpi" href="#/exam" style="text-decoration:none"><div class="n" style="color:var(--brand)">📝</div><div class="l">模拟考试</div></a>
    <a class="card kpi" href="#/stats" style="text-decoration:none"><div class="n" style="color:var(--warn)">📊</div><div class="l">数据统计</div></a>
  </div>
  <div class="center" style="padding:24px">
    ${resumeBtn}
    <div class="row">
      <a class="btn big ok" href="#/review">开始复习到期 (${s.due_review})</a>
      <a class="btn big" href="#/study">练习新题</a>
      <a class="btn big ghost" href="#/browse">浏览真题库</a>
      <a class="btn big ghost" href="#/kadian">考点导航</a>
      <a class="btn big ghost" href="#/materials">材料组题</a>
      <a class="btn big ghost" href="#/doubts">疑点复核</a>
    </div>
  </div>
  <h2>各模块进度</h2>
  <div class="grid g2">
    ${s.by_module.map(m => {
      const pct = Math.round(m.learned / m.total * 100);
      return `<div class="card"><div class="row" style="justify-content:space-between">
        <b>${esc(m.m)}</b><span class="tag">已学 ${m.learned}/${m.total} (${pct}%)</span></div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        <div class="sub" style="margin:8px 0 0">今日待复习：${dueMod[m.m] || 0}</div>
        <div class="row" style="margin-top:12px">
          <a class="btn ghost" href="#/study?module=${encodeURIComponent(m.m)}">练习该模块</a></div>
      </div>`;
    }).join('')}
  </div>
  <h2>掌握度分布（按复习间隔）</h2>
  <div class="card">
    ${mkeys.map(k => `<div class="row" style="justify-content:space-between;margin:6px 0">
      <span>${k}</span><span><b>${(mastery[k] || 0).toLocaleString()}</b>
      <span style="display:inline-block;vertical-align:middle;width:180px;height:8px;background:var(--panel2);border-radius:9px;margin-left:10px">
      <span style="display:block;height:100%;width:${(mastery[k] || 0) / maxM * 100}%;background:linear-gradient(90deg,var(--brand),var(--purple));border-radius:9px"></span></span></span>
    </div>`).join('')}
  </div>
  <div class="footer">SM-2 间隔重复 · 忘记/困难/一般/熟悉 四档自评 · 图片全程离线</div>`;
}

/* ---------- study setup ---------- */
function viewStudy() {
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  const msel = q.get('module') || '';
  const csel = q.get('category') || '';
  const ksel = q.get('kadian') || '';
  const cats = BOOT.categories.filter(c => !msel || c.module === msel);
  app.innerHTML = `
  <h1>练习新题</h1><p class="sub">选择范围后开始，系统按记忆曲线自动排入复习计划</p>
  ${ksel ? `<div class="askmodel" style="margin-bottom:14px">🎯 锁定考点：<b>${esc(ksel.split('/').pop())}</b> · <a class="linkback" href="#/study">清除筛选</a></div>` : ''}
  <div class="card" style="max-width:640px">
    <div class="filters">
      <label class="fld">模块<select id="s-mod">
        <option value="">全部</option>
        ${BOOT.modules.map(m => `<option ${m.module === msel ? 'selected' : ''} value="${esc(m.module)}">${esc(m.module)} (${m.n})</option>`).join('')}
      </select></label>
      <label class="fld">大类<select id="s-cat"><option value="">全部大类</option>
        ${cats.map(c => `<option ${c.category === csel ? 'selected' : ''} value="${esc(c.category)}">${esc(c.category)} (${c.n})</option>`).join('')}
      </select></label>
      <label class="fld">考点关键字<input id="s-kd" placeholder="如 增长率 / 图形推理" size="18" value="${esc(ksel)}"></label>
    </div>
    <div class="row"><button class="btn big ok" id="s-go">开始练习 →</button></div>
    <p class="sub" style="margin:14px 0 0">提示：新题会先以「点击作答→自评」方式学习，答错/忘记的题目会更频繁地回来。作答后可收藏、写笔记。</p>
  </div>
  <div class="card" style="max-width:640px;margin-top:16px">
    <h2 style="margin-top:0">⏱ 单题限时训练（可选）</h2>
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <span>开启后每题倒计时，超时自动记为「超时答错」并进入错题本</span>
      <label class="switch"><input type="checkbox" id="s-limit" ${LIMIT_ON ? 'checked' : ''}><span class="slider"></span></label>
    </div>
    <div id="s-limit-box" style="${LIMIT_ON ? '' : 'display:none'}">
      <div class="grid g3">
        ${Object.entries(loadLimits()).map(([m, sec]) => `<label class="fld">${esc(m)}<input type="number" min="5" max="600" value="${sec}" data-mod="${esc(m)}" class="s-lim"></label>`).join('')}
      </div>
      <p class="sub" style="margin:10px 0 0">单位：秒。建议：言语45 / 判断40 / 数量90 / 资料60 / 常识30。</p>
    </div>
  </div>`;
  const modSel = document.getElementById('s-mod');
  modSel.onchange = () => {
    const c = document.getElementById('s-cat'); const mv = modSel.value;
    c.innerHTML = '<option value="">全部大类</option>' + BOOT.categories.filter(x => !mv || x.module === mv)
      .map(x => `<option value="${esc(x.category)}">${esc(x.category)} (${x.n})</option>`).join('');
  };
  const sLimit = document.getElementById('s-limit');
  if (sLimit) sLimit.onchange = () => {
    const box = document.getElementById('s-limit-box');
    if (box) box.style.display = sLimit.checked ? '' : 'none';
  };
  document.getElementById('s-go').onclick = () => {
    const on = sLimit ? sLimit.checked : false;
    const limits = {};
    document.querySelectorAll('.s-lim').forEach(i => { limits[i.dataset.mod] = Math.max(5, +i.value || 30); });
    saveLimits(limits); LIMIT_ON = on;
    try { localStorage.setItem('kg_limit_on', on ? '1' : '0'); } catch (e) { }
    startSession({
      mode: 'mix', module: modSel.value, category: document.getElementById('s-cat').value,
      kadian: document.getElementById('s-kd').value.trim(),
      limitOn: on, limits,
    });
  };
}

/* ---------- session ---------- */
async function startSession(cfg) {
  loading('拉取题目…');
  const p = new URLSearchParams();
  p.set('mode', cfg.mode); p.set('size', cfg.size || '20');
  if (cfg.mode !== 'exam') p.set('exclude', loadSeen().join(','));
  if (cfg.module) p.set('module', cfg.module);
  if (cfg.category) p.set('category', cfg.category);
  if (cfg.kadian) p.set('kadian', cfg.kadian);
  if (cfg.ref) p.set('ref', cfg.ref);
  if (cfg.ids && cfg.ids.length) p.set('ids', cfg.ids.join(','));
  let res;
  try { res = await API('/api/next?' + p); } catch (e) { return fatal(e); }
  if (!res.cards.length) {
    const emptyMsg = {
      review: ['没有到期需要复习的题目', '去练习一些新题，或换个范围。'],
      wrong: ['错题本是空的', '先去练习，答错的题会自动收集到这里。'],
      fav: ['还没有收藏任何题目', '练习时在答案页点「⭐ 收藏」即可加入。'],
      material: ['这组材料没有更多题目了', '回到材料组题页选别的材料。'],
    }[cfg.mode] || ['这个范围的题都学过啦', '换个模块/大类试试。'];
    app.innerHTML = `<div class="empty"><div class="big">🎉</div>
      <h2>${emptyMsg[0]}</h2><p>${emptyMsg[1]}</p>
      <div class="row"><a class="btn" href="#/study">练习新题</a><a class="btn ghost" href="#/">返回总览</a></div></div>`;
    return;
  }
  state.session = {
    mode: cfg.mode, cfg, queue: res.cards, idx: 0,
    phase: 'question', chosen: null, full: null,
    tally: { done: 0, right: 0, wrong: 0 },
    marked: new Set(), limitOn: !!cfg.limitOn, limits: cfg.limits || loadLimits(),
    ids: cfg.ids || null, startTs: Date.now(), times: [], timeoutN: 0, qStart: Date.now(),
  };
  addSeen(res.cards.map(c => c.qid));
  renderCard();
  saveSession();
}

function renderCard() {
  const S = state.session; const c = S.queue[S.idx];
  if (!c) { clearSession(); return finishSession(); }
  S.phase = 'question'; S.chosen = null; S.full = null; S.qStart = Date.now();
  const hasMat = !!c.material; window.__curMatRaw = c.material;
  app.innerHTML = `
  <div class="progress"><div class="pp-main"><span id="pp-txt">${S.idx + 1} / ${S.queue.length} &nbsp;·&nbsp; ${MODE_LABEL[S.mode] || '练习'}模式
    &nbsp;·&nbsp; 本次对 <b style="color:var(--ok)">${S.tally.right}</b> 错 <b style="color:var(--bad)">${S.tally.wrong}</b>${S.timeoutN ? ` &nbsp;·&nbsp; <b style="color:var(--warn)">超时 ${S.timeoutN}</b>` : ''}</span>
    <div class="pbar"><i style="width:${S.idx / S.queue.length * 100}%"></i></div></div>
    <div id="ctime-box"></div></div>
  <div class="qhead">
    <span class="tag">${esc(c.module)}/${esc(c.category)}</span>
    <span class="tag">${esc(c.region || '?')} ${esc(c.year || '')}</span>
    <span class="tag" title="${esc(c.kadian)}">${esc((c.kadian || '').split('/').pop().slice(0, 22))}</span>
    <button class="qtool ${S.marked.has(c.id) ? 'on' : ''}" id="t-mark" title="临时标记本题（仅本组有效，便于复盘）">⚑ ${S.marked.has(c.id) ? '已标记' : '标记'}</button>
    <a class="linkback" href="#/detail?id=${c.id}">查看完整标注</a>
    ${c.verified ? `<span class="tag vbadge" title="该题答案已经过联网权威来源核实，可信">✅ 已联网复核</span>` : ''}
  </div>
  ${c.module === '资料分析' && !hasMat ? `<div class="material-wrap mat-missing">⚠️ 本题材料缺失（原始数据未收录），可到「材料组题」模式换其他材料组练习</div>` : ''}
  ${hasMat ? `<details class="material-wrap" open><summary style="cursor:pointer;color:var(--brand);margin-bottom:8px">📄 给定材料 <span class="m-hint">（疑点提示将在作答后显示）</span></summary><div class="material" id="mat-stripped">${stripCallouts(c.material)}</div></details>` : ''}
  ${c.ask_model ? `<div class="askmodel">💡 问法模型：${esc(c.ask_model)}</div>` : ''}
  <div class="stem">${c.stem || '<i>（题干为图片，见下方选项）</i>'}</div>
  <div id="opts"></div>
  <div id="act"></div>`;
  renderOptions();
  // 功能3：临时标记按钮（就地切换，不重渲染以免丢失已揭示答案）
  const mb = document.getElementById('t-mark');
  if (mb) mb.onclick = (e) => {
    const on = S.marked.has(c.id);
    if (on) S.marked.delete(c.id); else S.marked.add(c.id);
    e.currentTarget.classList.toggle('on', !on);
    e.currentTarget.textContent = on ? '⚑ 标记' : '⚑ 已标记';
  };
  // 功能2：单题限时倒计时
  const answerable = S.mode !== 'review';
  if (S.limitOn && answerable && S.phase === 'question') {
    const sec = (S.limits && S.limits[c.module]) || LIMIT_DEFAULT[c.module] || 45;
    startLimitTimer(sec);
  } else {
    const cb = document.getElementById('ctime-box'); if (cb) cb.innerHTML = '';
    stopLimitTimer();
  }
}

function renderOptions() {
  const S = state.session, c = S.queue[S.idx], box = document.getElementById('opts');
  const rev = S.full;
  const opts = rev ? rev.options : c.options;
  const answerable = S.mode !== 'review';
  if (!opts || !opts.length) { box.innerHTML = ''; }
  else if (!answerable && S.phase === 'question') {
    box.innerHTML = `<div class="row"><button class="btn" id="btn-reveal">🧠 先回忆，然后点击显示选项与答案</button></div>`;
    document.getElementById('btn-reveal').onclick = doReveal;
  } else {
    box.innerHTML = `<div class="opts">` + opts.map(o => {
      let cls = 'opt';
      if (S.phase === 'answer') {
        if (o.is_answer) cls += ' show-ans';
        if (S.chosen === o.key && !o.is_answer) cls += ' wrong';
        if (S.chosen === o.key && o.is_answer) cls += ' correct';
      }
      const clickable = (answerable && S.phase === 'question');
      return `<div class="${cls}" ${clickable ? `data-key="${o.key}"` : ''}>
        <span class="k">${o.key}</span><span class="body">${o.html || esc(o.label)}</span>
        ${S.phase === 'answer' && o.is_answer ? '<span style="color:var(--ok);font-weight:800">✔ 正确</span>' : ''}
      </div>`;
    }).join('') + `</div>`;
    if (answerable && S.phase === 'question') {
      box.querySelectorAll('.opt').forEach(el => el.onclick = () => { S.chosen = el.dataset.key; doReveal(); });
    }
  }
  renderAct();
}

function renderAct() {
  const S = state.session; const act = document.getElementById('act'); if (!act) return;
  const answerable = S.mode !== 'review';
  if (S.phase === 'question') {
    if (answerable) act.innerHTML = `<p class="sub" style="margin-top:14px">👆 点击上方选项作答</p>`;
    return;
  }
  const f = S.full;
  const isRight = answerable ? (S.chosen === f.answer) : null;
  if (answerable) { S.tally.done++; if (isRight) S.tally.right++; else S.tally.wrong++; }
  act.innerHTML = `
    <div class="qtools">
      <button class="qtool ${f.fav ? 'on' : ''}" id="t-fav">${f.fav ? '★ 已收藏' : '☆ 收藏'}</button>
      <button class="qtool" id="t-note">📝 笔记</button>
      <button class="qtool" id="t-detail">🔍 完整标注</button>
    </div>
    <div class="answer-line ${answerable ? (isRight ? 'right' : 'wrong') : ''}">
      ${!answerable ? '正确答案：<b>' + esc(f.answer) + '</b>' : (isRight ? '✓ 答对了，正确答案 ' + esc(f.answer) : '✗ 答错了，正确答案是 ' + esc(f.answer) + '，你选了 ' + esc(S.chosen || '—'))}
    </div>
    <div id="note-area"></div>
    <div class="reveal">
      ${f.mother ? `<div class="sec mother"><h4>🧩 母题抽象</h4>${inlineMd(f.mother)}</div>` : ''}
      ${f.reasoning ? `<div class="sec"><h4>🔗 推理链</h4>${inlineMd(f.reasoning)}</div>` : ''}
      ${f.fastest ? `<div class="sec fastest"><h4>⚡ 最快解法</h4>${inlineMd(f.fastest)}</div>` : ''}
      ${f.pitfalls ? `<div class="sec pitfalls"><h4>⚠ 易错点</h4>${inlineMd(f.pitfalls)}</div>` : ''}
      ${f.analysis ? `<div class="sec"><h4>📘 官方解析</h4>${f.analysis}</div>` : ''}
      ${f.doubt ? `<div class="sec doubt"><h4>❓ 疑点待复核</h4>${inlineMd(f.doubt)}</div>` : ''}
      ${f.verified ? `<div class="sec verified">
        <h4>✅ 联网复核结论${f.verified.status === 'confirmed' ? '（官方答案确认无误）' : ''}</h4>
        <p>经权威来源核实，本题正确答案为 <b>${esc(f.verified.answer || '')}</b>。</p>
        ${f.verified.supplement ? `<p><b>补全的数据链</b>：${esc(f.verified.supplement)}</p>` : ''}
        ${f.verified.reasoning ? `<p>${esc(f.verified.reasoning)}</p>` : ''}
        ${f.verified.source_url ? `<p class="vsrc">来源：<a href="${esc(f.verified.source_url)}" target="_blank" rel="noopener">${esc(f.verified.source_name || '查看来源')}</a></p>` : ''}
      </div>` : ''}
    </div>
    <div class="grade">
      <button class="g-again" data-g="1">忘记<small>again · 马上重来</small></button>
      <button class="g-hard" data-g="3">困难<small>hard</small></button>
      <button class="g-good" data-g="4">一般<small>good · 记住了</small></button>
      <button class="g-easy" data-g="5">熟悉<small>easy · 轻松</small></button>
    </div>
    <p class="sub" style="margin-top:10px">按你的真实记忆强度自评（键盘 1/2/3/4 亦可）。系统将据此计算下次复习间隔。</p>`;
  act.querySelectorAll('.grade button').forEach(b => b.onclick = () => doGrade(+b.dataset.g));
  document.getElementById('t-fav').onclick = async (e) => {
    const on = !f.fav;
    await POST('/api/fav', { id: c2id(), on });
    f.fav = on ? 1 : 0;
    const btn = document.getElementById('t-fav');
    btn.classList.toggle('on', !!on); btn.textContent = on ? '★ 已收藏' : '☆ 收藏';
    refreshSide();
  };
  document.getElementById('t-note').onclick = () => toggleNote();
  document.getElementById('t-detail').onclick = () => { location.hash = '#/detail?id=' + c2id(); };
}
function c2id() { return state.session.queue[state.session.idx].id; }

function toggleNote() {
  const area = document.getElementById('note-area'); if (!area) return;
  if (area.dataset.open === '1') { area.innerHTML = ''; area.dataset.open = '0'; return; }
  const f = state.session.full;
  area.dataset.open = '1';
  area.innerHTML = `<div class="note-box"><h4>📝 我的笔记</h4>
    <textarea id="note-ta" placeholder="写下你的思路、易错提醒、秒杀技巧…">${esc(f.note || '')}</textarea>
    <div class="row"><span class="note-saved" id="note-saved">${f.note_updated ? '上次保存 ' + esc(f.note_updated) : ''}</span>
    <button class="btn sm ok" id="note-save">保存笔记</button></div></div>`;
  document.getElementById('note-save').onclick = async () => {
    const text = document.getElementById('note-ta').value.trim();
    await POST('/api/note', { id: c2id(), text });
    f.note = text;
    document.getElementById('note-saved').textContent = text ? '已保存 ✓' : '已清空笔记';
  };
}

async function doReveal() {
  const S = state.session, c = S.queue[S.idx];
  stopLimitTimer();
  try { const d = await API('/api/question?id=' + c.id); S.full = d.card; } catch (e) { return fatal(e); }
  S.phase = 'answer'; renderOptions();
}

async function doGrade(g) {
  const S = state.session, c = S.queue[S.idx];
  const dur = Date.now() - (S.qStart || Date.now());
  S.times.push(dur); stopLimitTimer();
  try { await POST('/api/grade', { id: c.id, grade: g, choice: S.chosen || '' }); } catch (e) { }
  refreshSide();
  S.idx++;
  if (S.idx >= S.queue.length) { await moreCards(); }
  renderCard();
  saveSession();
}

/* ---------- 功能2：限时模式倒计时 / 超时自动判错 ---------- */
function startLimitTimer(sec) {
  stopLimitTimer();
  const S = state.session; if (!S) return;
  S.limitSec = sec; S.limitDeadline = Date.now() + sec * 1000;
  const box = document.getElementById('ctime-box'); if (!box) return;
  box.innerHTML = `<div class="ctime" id="ctime"><span id="ctime-n">${sec}</span></div>`;
  tickLimit();
  S.limitInterval = setInterval(tickLimit, 200);
}
function stopLimitTimer() {
  const S = state.session;
  if (S && S.limitInterval) { clearInterval(S.limitInterval); S.limitInterval = null; }
  if (S) S.limitDeadline = 0;
}
function tickLimit() {
  const S = state.session; if (!S || !S.limitDeadline) return;
  const remain = Math.max(0, (S.limitDeadline - Date.now()) / 1000);
  const el = document.getElementById('ctime'); if (!el) return;
  const pct = Math.max(0, Math.min(100, remain / (S.limitSec || 1) * 100));
  el.style.setProperty('--v', pct.toFixed(1));
  el.classList.toggle('low', remain <= 10);
  const n = document.getElementById('ctime-n'); if (n) n.textContent = Math.ceil(remain);
  if (remain <= 0) { stopLimitTimer(); timeoutCurrent(); }
}
async function timeoutCurrent() {
  const S = state.session, c = S.queue[S.idx];
  if (!S || !c) return;
  const dur = Date.now() - (S.qStart || Date.now());
  S.times.push(dur); stopLimitTimer();
  try { await POST('/api/grade', { id: c.id, grade: 1, timeout: 1, choice: '' }); } catch (e) { }
  S.tally.done++; S.tally.wrong++; S.timeoutN = (S.timeoutN || 0) + 1;
  refreshSide();
  S.idx++;
  if (S.idx >= S.queue.length) { await moreCards(); }
  renderCard();
  saveSession();
}

async function moreCards() {
  const S = state.session, p = new URLSearchParams();
  p.set('mode', S.mode); p.set('size', '20');
  const cfg = S.cfg || {};
  if (cfg.module) p.set('module', cfg.module);
  if (cfg.category) p.set('category', cfg.category);
  if (cfg.kadian) p.set('kadian', cfg.kadian);
  if (cfg.ref) p.set('ref', cfg.ref);
  let res = { cards: [] }; try { res = await API('/api/next?' + p); } catch (e) { }
  if (res.cards.length) { S.queue = res.cards; S.idx = 0; }
}

async function finishSession() {
  const S = state.session;
  const total = S.tally.done, right = S.tally.right, wrong = S.tally.wrong;
  const acc = total ? Math.round(right / total * 100) : 0;
  const marked = S.marked ? Array.from(S.marked) : [];
  let timing = '';
  if (S.limitOn && S.times && S.times.length) {
    const sum = S.times.reduce((a, b) => a + b, 0);
    const avg = sum / S.times.length / 1000;
    const tm = Math.floor(sum / 1000 / 60), ts = Math.round(sum / 1000 % 60);
    // 功能2：正确率对比（本次限时 vs 平时不限时整体）
    let cmp = '';
    try {
      const accData = await API('/api/accuracy');
      let c = 0, w = 0;
      (accData.module || []).forEach(m => { c += (m.c || 0); w += (m.w || 0); });
      const baseAcc = (c + w) ? Math.round(c / (c + w) * 100) : null;
      if (baseAcc !== null) {
        const diff = acc - baseAcc;
        const cls = diff >= 0 ? 'var(--ok)' : 'var(--bad)';
        cmp = `<p style="margin-top:6px">平时不限时正确率 <b style="color:var(--muted)">${baseAcc}%</b> · 本次限时 <b style="color:${cls}">${acc}%</b>（${diff >= 0 ? '+' : ''}${diff}）</p>`;
      }
    } catch (e) { }
    timing = `<p>总用时 ${tm} 分 ${ts} 秒 · 平均单题 ${avg.toFixed(1)}s · 本组正确率 <b style="color:${acc >= 60 ? 'var(--ok)' : 'var(--warn)'}">${acc}%</b>${S.timeoutN ? ` · 超时 <b style="color:var(--bad)">${S.timeoutN}</b> 次` : ''}</p>${cmp}`;
  }
  app.innerHTML = `<div class="empty"><div class="big">🏁</div>
    <h2>本组完成！</h2>
    <p>本轮练习 ${total} 题 · 对 ${right} · 错 ${wrong}</p>
    ${timing}
    ${marked.length ? `<div class="card" style="max-width:560px;margin:10px auto 0;text-align:left">
      <h3 style="margin:0 0 6px">⚑ 本组标记的题（${marked.length}）</h3>
      <p class="sub" style="margin:0 0 12px">这些标记仅在本组有效，退出后清除。可一键进入复盘模式单独重做。</p>
      <button class="btn warn" onclick="startSession({mode:'ids',ids:[${marked.join(',')}]})">▶ 复盘标记题（${marked.length}）</button>
    </div>` : ''}
    <div class="row"><a class="btn ok" href="#/review">继续复习到期</a>
      <a class="btn" href="#/study">练习更多新题</a>
      <a class="btn ghost" href="#/wrongbook">看错题本</a>
      <a class="btn ghost" href="#/">返回总览</a></div></div>`;
}

/* keyboard */
document.addEventListener('keydown', (e) => {
  const S = state.session;
  if (S && document.getElementById('opts')) {
    if (e.target && (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT')) return;
    const answerable = S.mode !== 'review';
    if (S.phase === 'question') {
      if (answerable && 'ABCD'.includes(e.key.toUpperCase())) {
        const o = S.queue[S.idx].options.find(x => x.key.toUpperCase() === e.key.toUpperCase());
        if (o) { S.chosen = o.key; doReveal(); }
      } else if ((e.key === ' ' || e.key === 'Enter') && !answerable) { e.preventDefault(); doReveal(); }
    } else if (S.phase === 'answer') {
      const map = { '1': 1, '2': 3, '3': 4, '4': 5 };
      if (map[e.key]) { e.preventDefault(); doGrade(map[e.key]); }
    }
  }
  if (state.exam && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
    const E = state.exam; const dir = e.key === 'ArrowRight' ? 1 : -1;
    examGoto(E.cur + dir);
  }
});

/* ---------- browse ---------- */
async function viewBrowse(extra) {
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  const f = { module: q.get('module') || '', category: q.get('category') || '', kadian: q.get('kadian') || '', region: q.get('region') || '', year: q.get('year') || '', q: q.get('q') || '', only: q.get('only') || '', page: +(q.get('page') || 1) };
  loading('查询中…');
  const [fac, res] = await Promise.all([API('/api/facets'), API('/api/browse?' + buildQ(f))]);
  const heading = f.only === 'wrong' ? '错题本' : f.only === 'fav' ? '我的收藏' : '真题浏览';
  const actionBtn = f.only === 'wrong' ? `<a class="btn ok" href="#" onclick="startSession({mode:'wrong'});return false;">▶ 只练错题</a>`
    : f.only === 'fav' ? `<a class="btn ok" href="#" onclick="startSession({mode:'fav'});return false;">▶ 只练收藏</a>` : '';
  app.innerHTML = `
  <h1>${heading}</h1><p class="sub">共匹配 ${res.total.toLocaleString()} 题 · 点击查看全部深度标注</p>
  <div class="card" style="margin-bottom:16px">
    <h2 style="margin-top:0">🔍 题干关键词搜题</h2>
    <div class="filters">
      <label class="fld">模块<select id="s-mod"><option value="">全部</option>
        ${BOOT.modules.map(m => `<option ${f.module === m.module ? 'selected' : ''}>${esc(m.module)}</option>`).join('')}</select></label>
      <label class="fld">大类<select id="s-cat"><option value="">全部</option>
        ${BOOT.categories.map(c => `<option ${f.category === c.category ? 'selected' : ''} value="${esc(c.category)}">${esc(c.category)}</option>`).join('')}</select></label>
      <label class="fld">关键词<input id="s-q" size="22" placeholder="如 增长率 / 下列表述正确" value="${esc(f.q)}"></label>
      <button class="btn" id="s-search">搜索</button>
    </div>
    <div id="search-hist" class="chipset" style="margin:6px 0 12px"></div>
    <div id="search-results"></div>
  </div>
  ${actionBtn ? `<div class="row" style="margin-bottom:14px">${actionBtn}</div>` : ''}
  <div class="card" style="margin-bottom:16px"><div class="filters">
    <label class="fld">模块<select id="b-mod"><option value="">全部</option>
      ${BOOT.modules.map(m => `<option ${f.module === m.module ? 'selected' : ''}>${esc(m.module)}</option>`).join('')}</select></label>
    <label class="fld">大类<select id="b-cat"><option value="">全部</option>
      ${BOOT.categories.map(c => `<option ${f.category === c.category ? 'selected' : ''} value="${esc(c.category)}">${esc(c.category)}</option>`).join('')}</select></label>
    <label class="fld">地区<select id="b-region"><option value="">全部</option>
      ${fac.regions.map(r => `<option ${f.region === r ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select></label>
    <label class="fld">年份<select id="b-year"><option value="">全部</option>
      ${fac.years.map(y => `<option ${f.year === y ? 'selected' : ''}>${esc(y)}</option>`).join('')}</select></label>
    <label class="fld">考点/题干<input id="b-q" size="18" placeholder="关键字" value="${esc(f.q)}"></label>
    <label class="fld">考点名<input id="b-kd" size="16" value="${esc(f.kadian)}"></label>
    <button class="btn" id="b-go">筛选</button>
  </div></div>
  <div class="card" style="padding:0;overflow:auto"><table><thead><tr>
    <th>考点 / 大类</th><th>地区</th><th>年份</th><th>答</th><th>对错</th><th>状态</th><th>下次复习</th><th>★</th><th></th></tr></thead><tbody>
    ${res.rows.map(r => `<tr>
      <td><b>${esc((r.kadian || '').split('/').pop().slice(0, 30))}</b><br><span class="tag">${esc(r.module)}/${esc(r.category)}</span></td>
      <td>${esc(r.region)}</td><td>${esc(r.year)}</td><td style="color:var(--ok);font-weight:800">${esc(r.answer)}</td>
      <td><span style="color:var(--ok)">${r.correct || 0}</span>/<span style="color:var(--bad)">${r.wrong || 0}</span></td>
      <td><span class="tag ${esc(r.status)}">${{ new: '未学', learning: '学习中', review: '复习中' }[r.status] || r.status}</span></td>
      <td>${r.status === 'new' ? '—' : esc(r.due) + (r.due <= BOOT.today ? ' ⏰' : '')} ${r.interval ? '<span class="tag">' + r.interval + 'd</span>' : ''}</td>
      <td>${r.fav ? '★' : ''}</td>
      <td><a class="linkback" href="#/detail?id=${r.id}">详情</a></td></tr>`).join('')}
  </tbody></table></div>
  ${pager(res.total, f.page, f)}`;
  document.getElementById('b-go').onclick = () => goBrowse({ module: val('b-mod'), category: val('b-cat'), region: val('b-region'), year: val('b-year'), q: val('b-q'), kadian: val('b-kd'), only: f.only, page: 1 });
  document.getElementById('s-search').onclick = runSearch;
  const sq = document.getElementById('s-q');
  if (sq) sq.addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });
  renderSearchHist();
  if (f.q) runSearch();
}
/* ---------- 功能4：题干关键词搜题 ---------- */
function jq(s) { return JSON.stringify(s); }
async function runSearch() {
  const kw = (document.getElementById('s-q') || {}).value;
  if (!kw || !kw.trim()) { const b = document.getElementById('search-results'); if (b) b.innerHTML = '<div class="sub">输入关键词后回车或点击搜索</div>'; return; }
  const term = kw.trim();
  pushSearchHist(term); renderSearchHist();
  const m = val('s-mod'), c = val('s-cat');
  const p = new URLSearchParams(); p.set('q', term); if (m) p.set('module', m); if (c) p.set('category', c); p.set('size', '30');
  const res = await API('/api/search?' + p);
  const box = document.getElementById('search-results'); if (!box) return;
  if (!res.rows.length) { box.innerHTML = `<div class="empty" style="padding:20px">没有匹配「${esc(term)}」的题干</div>`; return; }
  box.innerHTML = `<p class="sub">匹配 ${res.total} 题（显示前 ${res.rows.length} 条）· 点击卡片看完整标注</p>` +
    res.rows.map(r => `<div class="card kd-card" style="margin-bottom:10px;cursor:pointer" onclick="location.hash='#/detail?id=${r.id}'">
      <div class="row" style="justify-content:space-between"><b>${esc((r.kadian || '').split('/').pop().slice(0, 30))}</b>
        <span class="tag">${esc(r.module)}/${esc(r.category)}</span></div>
      <div class="sub" style="margin:6px 0">${esc(r.snippet)}…</div>
      <div class="row" style="gap:6px">
        <span class="tag ${r.status === 'new' ? 'new' : r.status}">${{ new: '未学', learning: '学习中', review: '复习中' }[r.status] || r.status}</span>
        ${r.wrong ? '<span class="tag learning">曾答错</span>' : ''}${r.fav ? '<span class="tag review">★</span>' : ''}
        <span class="sub" style="margin:0">正确答案 ${esc(r.answer)}</span></div>
    </div>`).join('');
}
function renderSearchHist() {
  const box = document.getElementById('search-hist'); if (!box) return;
  const h = loadSearchHist();
  box.innerHTML = h.length ? ('<span class="sub" style="margin:0 8px 0 0">最近搜索：</span>' + h.map(k => `<button class="qtool" onclick="fillSearch(${jq(k)})">${esc(k)}</button>`).join('')) : '';
}
function fillSearch(k) { const i = document.getElementById('s-q'); if (i) i.value = k; runSearch(); }

async function viewWrongbook() {
  loading();
  const st = await API('/api/wrongbook/stats');
  const list = await API('/api/browse?only=wrong&size=8').catch(() => ({ rows: [] }));
  const mkList = (rows) => rows.map(r => `<tr>
    <td><b>${esc((r.kadian || '').split('/').pop().slice(0, 30))}</b><br><span class="tag">${esc(r.module)}/${esc(r.category)}</span></td>
    <td>${esc(r.region)}</td><td>${esc(r.year)}</td>
    <td style="color:var(--ok);font-weight:800">${esc(r.answer)}</td>
    <td><span class="tag learning">错 ${r.wrong || 0}</span></td>
    <td>${r.status === 'new' ? '—' : esc(r.due) + (r.due <= BOOT.today ? ' ⏰' : '')}</td>
    <td><a class="linkback" href="#/detail?id=${r.id}">详情</a></td></tr>`).join('');
  app.innerHTML = `
  <h1>错题本</h1><p class="sub">答错的题会自动收集到这里。连续两次自评「一般/熟悉」将自动移出待重练队列。</p>
  <div class="grid g3">
    <div class="card kpi bad"><div class="n">${st.total}</div><div class="l">📕 错题总数</div></div>
    <div class="card kpi ok"><div class="n">${st.mastered}</div><div class="l">✅ 已掌握</div></div>
    <div class="card kpi warn"><div class="n">${st.pending}</div><div class="l">🔁 待重练</div></div>
  </div>
  <div class="center" style="padding:26px">
    <div class="row">
      <button class="btn big ok" id="wb-redo">▶ 开始错题重练</button>
      <a class="btn big ghost" href="#/browse?only=wrong">查看错题明细列表</a>
    </div>
  </div>
  <h2>待重练预览（最近 ${Math.min(8, (list.rows || []).length)} 题）</h2>
  ${ (list.rows && list.rows.length) ? `<div class="card" style="padding:0;overflow:auto"><table><thead><tr>
    <th>考点 / 大类</th><th>地区</th><th>年份</th><th>答</th><th>状态</th><th>下次复习</th><th></th></tr></thead><tbody>
    ${mkList(list.rows)}</tbody></table></div>` : '<div class="empty">还没有错题，先去练习吧</div>' }
  `;
  const rb = document.getElementById('wb-redo');
  if (rb) rb.onclick = () => startSession({ mode: 'wrongredo' });
}
function viewFavorites() {
  if (!location.hash.includes('only=fav')) { location.hash = '#/browse?only=fav'; return; }
  return viewBrowse();
}
function val(id) { const e = document.getElementById(id); return e ? e.value.trim() : ''; }
function buildQ(f) { const p = new URLSearchParams(); Object.entries(f).forEach(([k, v]) => { if (v) p.set(k, v); }); return p; }
function goBrowse(f) { location.hash = '#/browse?' + buildQ(f); }
function pager(total, page, f) {
  const pages = Math.ceil(total / 25); if (pages <= 1) return '';
  const mk = (p) => { const q = Object.assign({}, f, { page: p }); return `#/browse?${buildQ(q)}`; };
  return `<div class="pager">
    ${page > 1 ? `<a class="btn ghost" href="${mk(page - 1)}">← 上一页</a>` : ''}
    <span>第 ${page} / ${pages.toLocaleString()} 页</span>
    ${page < pages ? `<a class="btn ghost" href="${mk(page + 1)}">下一页 →</a>` : ''}</div>`;
}

/* ---------- detail ---------- */
async function viewDetail(id) {
  loading();
  let d; try { d = await API('/api/question?id=' + id); } catch (e) { return fatal(e); }
  const c = d.card, s = d.srs;
  app.innerHTML = `
  <a class="linkback" onclick="history.back()">← 返回</a>
  <div class="qhead" style="margin-top:10px">
    <span class="tag">${esc(c.module)}/${esc(c.category)}</span>
    <span class="tag">${esc(c.region)} ${esc(c.year)}</span>
    <span class="tag ${esc(s ? s.status : 'new')}">${{ new: '未学', learning: '学习中', review: '复习中' }[s ? s.status : 'new'] || ''}</span>
    <span class="tag">下次复习 ${s ? esc(s.due) : '—'}</span>
  </div>
  <h1 style="font-size:20px">${esc(c.kadian)}</h1>
  <p class="sub">${esc(c.paper || '')}</p>
  <div class="qtools">
    <button class="qtool ${c.fav ? 'on' : ''}" id="d-fav">${c.fav ? '★ 已收藏' : '☆ 收藏'}</button>
    <button class="qtool ${c.doubt_status === 'resolved' ? 'on' : ''}" id="d-doubt">${c.doubt_status === 'resolved' ? '✓ 疑点已复核' : '⚑ 标记疑点已复核'}</button>
  </div>
  ${c.ask_model ? `<div class="askmodel">💡 问法模型：${esc(c.ask_model)}</div>` : ''}
  ${c.material ? `<details open class="material-wrap"><summary style="cursor:pointer;color:var(--brand);margin-bottom:8px">📄 给定材料</summary><div class="material">${c.material}</div></details>` : ''}
  <div class="stem">${c.stem}</div>
  <div class="opts">${c.options.map(o => `<div class="opt ${o.is_answer ? 'show-ans' : ''}">
    <span class="k">${o.key}</span><span class="body">${o.html || esc(o.label)}</span>
    ${o.is_answer ? '<span style="color:var(--ok);font-weight:800">✔</span>' : ''}</div>`).join('')}</div>
  <div class="answer-line right">正确答案：${esc(c.answer)}</div>
  <div class="note-box"><h4>📝 我的笔记</h4>
    <textarea id="d-note">${esc(c.note || '')}</textarea>
    <div class="row"><span class="note-saved" id="d-note-saved">${c.note_updated ? '上次保存 ' + esc(c.note_updated) : ''}</span>
    <button class="btn sm ok" id="d-note-save">保存笔记</button></div></div>
  <div class="reveal">
    ${c.mother ? `<div class="sec mother"><h4>🧩 母题抽象</h4>${inlineMd(c.mother)}</div>` : ''}
    ${c.reasoning ? `<div class="sec"><h4>🔗 推理链</h4>${inlineMd(c.reasoning)}</div>` : ''}
    ${c.fastest ? `<div class="sec fastest"><h4>⚡ 最快解法</h4>${inlineMd(c.fastest)}</div>` : ''}
    ${c.pitfalls ? `<div class="sec pitfalls"><h4>⚠ 易错点</h4>${inlineMd(c.pitfalls)}</div>` : ''}
    ${c.analysis ? `<div class="sec"><h4>📘 官方解析</h4>${c.analysis}</div>` : ''}
    ${c.doubt ? `<div class="sec doubt"><h4>❓ 疑点待复核</h4>${inlineMd(c.doubt)}</div>` : ''}
  </div>
  <div class="row" style="margin-top:20px">
    <a class="btn ok" href="#/study?module=${encodeURIComponent(c.module)}&category=${encodeURIComponent(c.category)}&kadian=${encodeURIComponent(c.kadian)}">按此考点练习同类题</a>
  </div>`;
  document.getElementById('d-fav').onclick = async (e) => {
    const on = !c.fav; await POST('/api/fav', { id: c.id, on }); c.fav = on ? 1 : 0;
    const b = document.getElementById('d-fav'); b.classList.toggle('on', !!on); b.textContent = on ? '★ 已收藏' : '☆ 收藏'; refreshSide();
  };
  document.getElementById('d-note-save').onclick = async () => {
    const text = document.getElementById('d-note').value.trim();
    await POST('/api/note', { id: c.id, text });
    document.getElementById('d-note-saved').textContent = text ? '已保存 ✓' : '已清空笔记';
  };
  document.getElementById('d-doubt').onclick = async () => {
    const resolved = c.doubt_status !== 'resolved';
    await POST('/api/doubt', { id: c.id, status: resolved ? 'resolved' : '' });
    c.doubt_status = resolved ? 'resolved' : '';
    const b = document.getElementById('d-doubt'); b.classList.toggle('on', resolved);
    b.textContent = resolved ? '✓ 疑点已复核' : '⚑ 标记疑点已复核';
  };
}

/* ---------- kadian ---------- */
async function viewKadian() {
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  const m = q.get('module') || '', cat = q.get('category') || '';
  loading('加载考点…');
  const p = new URLSearchParams(); if (m) p.set('module', m); if (cat) p.set('category', cat); p.set('size', '200');
  const res = await API('/api/kadian?' + p);
  app.innerHTML = `
  <h1>考点导航</h1><p class="sub">按考点聚合，横向对比不同省份的同类考法</p>
  <div class="filters card">
    <label class="fld">模块<select id="k-mod"><option value="">全部</option>
      ${BOOT.modules.map(x => `<option ${m === x.module ? 'selected' : ''}>${esc(x.module)}</option>`).join('')}</select></label>
    <label class="fld">大类<select id="k-cat"><option value="">全部</option>
      ${BOOT.categories.filter(c => !m || c.module === m).map(c => `<option ${cat === c.category ? 'selected' : ''} value="${esc(c.category)}">${esc(c.category)}</option>`).join('')}</select></label>
    <button class="btn" id="k-go">筛选</button>
  </div>
  <div class="grid g2" style="margin-top:16px">
    ${res.rows.map(r => `<div class="card kd-card" onclick="goKadianBrowse('${enc(r.kd)}')">
      <div class="row" style="justify-content:space-between"><b>${esc((r.kd || '').split('/').pop().slice(0, 34))}</b><span class="tag">${r.n} 题</span></div>
      <div class="sub" style="margin:6px 0">${esc(r.module || r.m)}/${esc(r.category || r.c)}</div>
      ${r.mother ? `<div style="font-size:13px;color:var(--purple)">🧩 ${esc(String(r.mother).replace(/[#*>`]/g, '').slice(0, 120))}…</div>` : ''}
      </div>`).join('')}
  </div>`;
  document.getElementById('k-go').onclick = () => { location.hash = '#/kadian?module=' + encodeURIComponent(val('k-mod')) + '&category=' + encodeURIComponent(val('k-cat')); };
}
function enc(s) { return encodeURIComponent(s); }
function goKadianBrowse(kd) { location.hash = '#/browse?kadian=' + kd; }

/* markdown-lite for text fields (they contain html already) */
function inlineMd(t) { return t || ''; }

/* 做题时隐去材料里的 Obsidian 疑点/解析 callout（> [!warning] …）避免剧透答案逻辑 */
function stripCallouts(t) {
  if (!t) return t;
  let s = String(t);
  // 1) 纯文本层面先剔除 '>' 引用行（Obsidian callout 形如 '> [!warning] 疑点…'）。
  //    必须先于 innerHTML 处理：innerHTML 会把 '>' 序列化成 '&gt;'，导致后续正则漏网。
  s = s.replace(/^[ \t]*>[^\n]*(?:\n[ \t]*>[^\n]*)*/gm, '');
  // 2) 兜底：整行含强解析信号的裸解析段落（不带 callout 标记也会剧透）
  s = s.split('\n').filter(line => {
    const plain = line.replace(/<[^>]+>/g, '');
    if (!plain.trim() || /<img|<p>|<\/p>/i.test(line)) return true;
    return !/(官方解析|疑点|待人工复核|不影响\s*(正确)?答案|以\s*[A-F]\s*作答为准|与答案\s*[：:]?\s*[A-F])/.test(plain);
  }).join('\n');
  // 3) HTML 引用块
  if (/<blockquote/i.test(s)) {
    const d = document.createElement('div'); d.innerHTML = s;
    d.querySelectorAll('blockquote').forEach(e => e.remove());
    s = d.innerHTML;
  }
  // 压缩多余空行
  s = s.replace(/\n{3,}/g, '\n\n').replace(/^\s+|\s+$/g, '');
  return s;
}
function toggleMatFull() {
  const el = document.getElementById('mat-toggle'), body = document.getElementById('mat-stripped');
  if (!el || !body) return;
  const raw = window.__curMatRaw || '';
  if (el.dataset.on === '1') { body.innerHTML = stripCallouts(raw); el.dataset.on = ''; el.textContent = '显示疑点提示 ▾'; }
  else { body.innerHTML = raw; el.dataset.on = '1'; el.textContent = '隐藏疑点提示 ▴'; }
}

/* ---------- materials ---------- */
let MATREFS = [];
async function viewMaterials() {
  loading('加载材料组…');
  const res = await API('/api/materials?size=100');
  MATREFS = res.rows.map(r => r.ref);
  app.innerHTML = `
  <h1>材料组题</h1><p class="sub">资料分析按同一段材料成组出题，整组一起练最贴近真实考场</p>
  <div class="grid g2">
    ${res.rows.map((r, i) => `<div class="card kd-card" onclick="startMat(${i})">
      <div class="row" style="justify-content:space-between"><b>📄 材料组</b><span class="tag">${r.n} 题</span></div>
      <div class="sub" style="margin:8px 0;line-height:1.5">${esc(r.snippet)}…</div>
      <div class="row"><span class="btn sm ghost">整组开练 →</span></div>
    </div>`).join('')}
  </div>
  ${!res.rows.length ? '<div class="empty">暂无成组材料</div>' : ''}`;
}
function startMat(i) { startSession({ mode: 'material', ref: MATREFS[i] }); }

/* ---------- doubts ---------- */
async function viewDoubts() {
  const q = new URLSearchParams(location.hash.split('?')[1] || '');
  const only = q.get('only') || '';
  const page = +(q.get('page') || 1);
  loading('加载疑点…');
  const p = new URLSearchParams(); if (only) p.set('only', only); p.set('page', page); p.set('size', '20');
  const res = await API('/api/doubts?' + p);
  const mk = (pg) => { const x = new URLSearchParams(); if (only) x.set('only', only); x.set('page', pg); return `#/doubts?${x}`; };
  app.innerHTML = `
  <h1>疑点复核</h1><p class="sub">共 ${res.total.toLocaleString()} 题标注了「官方解析可能存在疑点」，逐条复核形成自己的判断</p>
  <div class="chipset">
    <a class="btn sm ${!only ? '' : 'ghost'}" href="#/doubts">全部疑点</a>
    <a class="btn sm ${only === 'pending' ? '' : 'ghost'}" href="#/doubts?only=pending">仅看待复核</a>
  </div>
  ${res.rows.map(r => `<div class="card" style="margin-bottom:12px">
    <div class="qhead">
      <span class="tag">${esc(r.module)}/${esc(r.category)}</span>
      <span class="tag">${esc(r.region)} ${esc(r.year)}</span>
      ${r.ds_status === 'resolved' ? '<span class="tag review">✓ 已复核</span>' : '<span class="tag learning">待复核</span>'}
    </div>
    <div class="sub" style="margin:4px 0">${esc((r.kadian || '').split('/').pop().slice(0, 40))}</div>
    <div class="reveal" style="margin-top:8px"><div class="sec doubt"><h4>❓ 疑点</h4>${inlineMd(r.doubt)}</div></div>
    <div class="row" style="margin-top:10px">
      <a class="linkback" href="#/detail?id=${r.id}">查看完整题</a>
      <button class="btn sm ${r.ds_status === 'resolved' ? 'ghost' : 'ok'}" onclick="markDoubt(${r.id}, '${r.ds_status === 'resolved' ? '' : 'resolved'}', this)">
        ${r.ds_status === 'resolved' ? '撤销复核标记' : '✓ 标记已复核'}</button>
    </div>
  </div>`).join('')}
  ${res.total > 20 ? `<div class="pager">
    ${page > 1 ? `<a class="btn ghost" href="${mk(page - 1)}">← 上一页</a>` : ''}
    <span>第 ${page} 页 · 共 ${Math.ceil(res.total / 20)} 页</span>
    ${page * 20 < res.total ? `<a class="btn ghost" href="${mk(page + 1)}">下一页 →</a>` : ''}</div>` : ''}`;
}
async function markDoubt(id, status, btn) {
  await POST('/api/doubt', { id, status });
  if (btn) { btn.textContent = status === 'resolved' ? '撤销复核标记' : '✓ 标记已复核'; btn.className = 'btn sm ' + (status === 'resolved' ? 'ghost' : 'ok'); }
  const tag = btn && btn.closest('.card') && btn.closest('.card').querySelector('.qhead .tag.review, .qhead .tag.learning');
}

/* ---------- exam ---------- */
function viewExam() {
  const cats = BOOT.categories;
  app.innerHTML = `
  <h1>模拟考试</h1><p class="sub">限定题量与时长，整卷作答，交卷即时判分并按记忆曲线记录</p>
  <div class="card" style="max-width:640px">
    <div class="filters">
      <label class="fld">模块<select id="e-mod"><option value="">全部混合</option>
        ${BOOT.modules.map(m => `<option value="${esc(m.module)}">${esc(m.module)}</option>`).join('')}</select></label>
      <label class="fld">大类<select id="e-cat"><option value="">全部大类</option>
        ${cats.map(c => `<option value="${esc(c.category)}">${esc(c.category)}</option>`).join('')}</select></label>
      <label class="fld">题量<input id="e-n" type="number" value="10" min="5" max="60" style="width:90px"></label>
      <label class="fld">时长(分钟)<input id="e-t" type="number" value="15" min="1" max="180" style="width:90px"></label>
    </div>
    <div class="row"><button class="btn big warn" id="e-go">▶ 开始模考</button></div>
  </div>
  <h2>历史记录</h2>
  <div class="card" style="padding:0;overflow:auto" id="e-hist"><div class="loading">加载历史…</div></div>`;
  const emod = document.getElementById('e-mod');
  emod.onchange = () => {
    const c = document.getElementById('e-cat'); const mv = emod.value;
    c.innerHTML = '<option value="">全部大类</option>' + BOOT.categories.filter(x => !mv || x.module === mv)
      .map(x => `<option value="${esc(x.category)}">${esc(x.category)}</option>`).join('');
  };
  document.getElementById('e-go').onclick = () => startExam({
    module: emod.value, category: document.getElementById('e-cat').value,
    n: Math.max(5, Math.min(60, +document.getElementById('e-n').value || 10)),
    minutes: Math.max(1, +document.getElementById('e-t').value || 15),
  });
  loadExamHistory();
}
async function loadExamHistory() {
  const box = document.getElementById('e-hist'); if (!box) return;
  const d = await API('/api/exam/history');
  if (!d.rows.length) { box.innerHTML = '<div class="empty" style="padding:24px">还没有模考记录</div>'; return; }
  box.innerHTML = `<table><thead><tr><th>时间</th><th>名称</th><th>题量</th><th>正确</th><th>得分</th><th>用时</th></tr></thead><tbody>
    ${d.rows.map(r => `<tr><td>${esc((r.ts || '').replace('T', ' ').slice(0, 16))}</td><td>${esc(r.title)}</td>
      <td>${r.total}</td><td style="color:var(--ok)">${r.correct}</td>
      <td><b style="color:${r.score >= 60 ? 'var(--ok)' : 'var(--warn)'}">${r.score}</b></td>
      <td>${Math.floor((r.duration || 0) / 60)}分${(r.duration || 0) % 60}秒</td></tr>`).join('')}</tbody></table>`;
}

async function startExam(cfg) {
  loading('组卷中…');
  const p = new URLSearchParams(); p.set('mode', 'new'); p.set('size', cfg.n);
  if (cfg.module) p.set('module', cfg.module);
  if (cfg.category) p.set('category', cfg.category);
  let res; try { res = await API('/api/next?' + p); } catch (e) { return fatal(e); }
  if (!res.cards.length) { app.innerHTML = `<div class="empty"><div class="big">📭</div><h2>该范围没有可组的新题</h2>
    <div class="row"><a class="btn" href="#/exam">返回</a></div></div>`; return; }
  const reveal = await Promise.all(res.cards.map(c => API('/api/question?id=' + c.id).then(d => d.card)));
  state.exam = {
    items: reveal.map(c => ({ card: c, chosen: null })),
    cur: 0, total: reveal.length,
    deadline: Date.now() + cfg.minutes * 60 * 1000,
    minutes: cfg.minutes, submitted: false, title: (cfg.module || '全模块') + ' 模考',
  };
  renderExam();
  clearInterval(state.exam.timer);
  state.exam.timer = setInterval(examTick, 1000);
}

function examTick() {
  const E = state.exam; if (!E || E.submitted) return;
  const remain = E.deadline - Date.now();
  const el = document.getElementById('ex-time');
  if (el) {
    const s = Math.max(0, Math.floor(remain / 1000));
    el.textContent = fmt(s); el.classList.toggle('low', s <= 60);
  }
  if (remain <= 0) { clearInterval(E.timer); examSubmit(true); }
}
function fmt(s) { const m = Math.floor(s / 60), r = s % 60; return (m < 10 ? '0' : '') + m + ':' + (r < 10 ? '0' : '') + r; }

function renderExam() {
  const E = state.exam; if (!E) return;
  const it = E.items[E.cur], c = it.card;
  app.innerHTML = `
  <div class="exam-timer"><div><b style="font-size:15px">模拟考试 · ${esc(E.title)}</b><div class="sub" style="margin:2px 0 0">第 ${E.cur + 1} / ${E.total} 题</div></div>
    <div style="text-align:right"><div class="t" id="ex-time">--:--</div><div class="sub" style="margin:0">剩余时间</div></div></div>
  <div class="exam-nav">${E.items.map((x, i) => `<button class="exam-dot ${x.chosen ? 'answered' : ''} ${i === E.cur ? 'cur' : ''}" onclick="examGoto(${i})">${i + 1}</button>`).join('')}</div>
  ${c.material ? `<details open class="material-wrap"><summary style="cursor:pointer;color:var(--brand);margin-bottom:8px">📄 给定材料 <span class="m-hint">（疑点提示将在作答后显示）</span></summary><div class="material" style="max-height:340px">${stripCallouts(c.material)}</div></details>` : ''}
  <div class="qhead"><span class="tag">${esc(c.module)}/${esc(c.category)}</span><span class="tag">${esc((c.kadian || '').split('/').pop().slice(0, 22))}</span></div>
  <div class="stem exam-q">${c.stem || '<i>（题干为图片）</i>'}</div>
  <div class="opts">${c.options.map(o => `<div class="opt ${it.chosen === o.key ? 'sel' : ''}" data-key="${o.key}">
    <span class="k">${o.key}</span><span class="body">${o.html || esc(o.label)}</span></div>`).join('')}</div>
  <div class="row" style="margin-top:20px;justify-content:space-between">
    <button class="btn ghost" ${E.cur === 0 ? 'disabled' : ''} onclick="examGoto(${E.cur - 1})">← 上一题</button>
    <div class="row">
      ${E.cur < E.total - 1 ? `<button class="btn" onclick="examGoto(${E.cur + 1})">下一题 →</button>` : ''}
      <button class="btn ok" onclick="examSubmit(false)">✅ 交卷</button>
    </div>
  </div>`;
  examTick();
  document.querySelectorAll('.opt').forEach(el => el.onclick = () => { it.chosen = el.dataset.key; renderExam(); });
}
function examGoto(i) {
  const E = state.exam; if (!E || i < 0 || i >= E.total) return;
  E.cur = i; renderExam();
}

async function examSubmit(auto) {
  const E = state.exam; if (!E || E.submitted) return;
  E.submitted = true; clearInterval(E.timer);
  const used = Math.round((E.minutes * 60 * 1000 - Math.max(0, E.deadline - Date.now())) / 1000);
  const items = E.items.map(x => ({ id: x.card.id, correct: !!(x.chosen && x.chosen === x.card.answer) }));
  let res; try { res = await POST('/api/exam/submit', { title: E.title, duration: used, items }); } catch (e) { return fatal(e); }
  const answered = E.items.filter(x => x.chosen).length;
  app.innerHTML = `
  <div class="exam-result card">
    <div class="sub" style="margin-bottom:6px">${auto ? '⏰ 时间到，已自动交卷' : '🎉 交卷成功'}</div>
    <div class="score" style="color:${res.score >= 60 ? 'var(--ok)' : 'var(--warn)'}">${res.score}</div>
    <div class="sub">得分</div>
    <div class="row" style="justify-content:center;margin:16px 0">
      <span class="tag">共 ${res.total} 题</span><span class="tag review">答对 ${res.correct}</span>
      <span class="tag">作答 ${answered}</span><span class="tag">用时 ${Math.floor(used / 60)}分${used % 60}秒</span>
    </div>
    <p class="sub">成绩已按「对→一般 / 错→忘记」写入记忆曲线，错题自动进错题本。</p>
    <div class="row" style="justify-content:center">
      <button class="btn" onclick="examReview()">逐题查看</button>
      <a class="btn ok" href="#/exam">再来一卷</a>
      <a class="btn ghost" href="#/wrongbook">看错题本</a>
      <a class="btn ghost" href="#/">返回总览</a>
    </div>
  </div>
  <div id="ex-review"></div>`;
  refreshSide();
}
function examReview() {
  const E = state.exam; const box = document.getElementById('ex-review');
  box.innerHTML = `<h2>逐题对照</h2>` + E.items.map((x, i) => {
    const c = x.card; const ok = x.chosen && x.chosen === c.answer;
    return `<div class="card" style="margin-bottom:12px">
      <div class="row" style="justify-content:space-between"><b>第 ${i + 1} 题</b>
        <span class="tag ${ok ? 'review' : 'learning'}">${ok ? '✓ 正确' : (x.chosen ? '✗ 选' + x.chosen + '，应' + c.answer : '未作答，应' + c.answer)}</span></div>
      <div class="stem" style="font-size:15px;margin:10px 0">${c.stem}</div>
      <div class="opts">${c.options.map(o => `<div class="opt ${o.is_answer ? 'show-ans' : ''} ${x.chosen === o.key && !o.is_answer ? 'wrong' : ''}">
        <span class="k">${o.key}</span><span class="body">${o.html || esc(o.label)}</span></div>`).join('')}</div>
    </div>`;
  }).join('');
  box.scrollIntoView({ behavior: 'smooth' });
}

/* ---------- stats ---------- */
async function viewStats() {
  loading('统计中…');
  const [st, heat, sk, acc, weak] = await Promise.all([
    API('/api/stats'), API('/api/heatmap?days=180'), API('/api/streak'), API('/api/accuracy'), API('/api/weak'),
  ]);
  const hm = {}; heat.days.forEach(d => hm[d.d] = d.n);
  const cells = [];
  const todayD = new Date();
  const N = 182;
  const start = new Date(todayD); start.setDate(start.getDate() - (N - 1));
  // align start to Sunday
  start.setDate(start.getDate() - start.getDay());
  for (let d = new Date(start); d <= todayD; d.setDate(d.getDate() + 1)) {
    const key = d.toISOString().slice(0, 10);
    const n = hm[key] || 0;
    let lv = 0; if (n >= 20) lv = 4; else if (n >= 10) lv = 3; else if (n >= 5) lv = 2; else if (n >= 1) lv = 1;
    cells.push(`<div class="heat-cell l${lv}" title="${key}: ${n} 题"></div>`);
  }
  const goalPct = sk.goal ? Math.min(100, Math.round(sk.today / sk.goal * 100)) : 0;
  const maxAcc = (a) => { const t = (a.c || 0) + (a.w || 0); return t ? Math.round(a.c / t * 100) : 0; };
  app.innerHTML = `
  <h1>数据统计</h1><p class="sub">学习节奏 · 正确率 · 薄弱考点，一眼看清</p>
  <div class="grid g4">
    <div class="card kpi ok"><div class="n">${sk.current}</div><div class="l">🔥 连续打卡(天)</div></div>
    <div class="card kpi purple"><div class="n">${sk.best}</div><div class="l">🏆 最长连续(天)</div></div>
    <div class="card kpi brand"><div class="n">${st.studied_today}</div><div class="l">✅ 今日已练</div></div>
    <div class="card kpi warn"><div class="n">${st.wrong}</div><div class="l">📕 错题总数</div></div>
  </div>
  <div class="card" style="margin-top:16px">
    <div class="ring-row">
      <div class="ring" style="--v:${goalPct}"><div class="in"><b>${sk.today}/${sk.goal}</b><span>今日目标</span></div></div>
      <div>
        <h2 style="margin:0 0 8px">每日目标</h2>
        <p class="sub" style="margin:0 0 12px">当前每天目标 <b>${sk.goal}</b> 题</p>
        <div class="row">
          <input id="g-in" type="number" min="1" max="500" value="${sk.goal}" style="width:100px">
          <button class="btn sm" id="g-set">更新目标</button>
        </div>
      </div>
    </div>
  </div>
  <h2>练习热力图（近半年）</h2>
  <div class="card"><div class="heat-wrap"><div class="heat-grid">${cells.join('')}</div></div>
    <div class="heat-legend">少 <span class="heat-cell"></span><span class="heat-cell l1"></span><span class="heat-cell l2"></span><span class="heat-cell l3"></span><span class="heat-cell l4"></span> 多</div></div>
  <h2>各模块正确率</h2>
  <div class="card">
    ${acc.module.map(a => { const pc = maxAcc(a); return `<div class="acc-row">
      <span class="name">${esc(a.k)}</span><div class="track"><i style="width:${pc}%"></i></div>
      <span class="pct">${pc}% · 对${a.c}/错${a.w}</span></div>`; }).join('') || '<div class="sub" style="margin:0">练习后这里会显示正确率</div>'}
  </div>
  <h2>各大类正确率</h2>
  <div class="card">
    ${acc.category.slice(0, 12).map(a => { const pc = maxAcc(a); return `<div class="acc-row">
      <span class="name" title="${esc(a.k)}">${esc((a.k || '').slice(0, 14))}</span><div class="track"><i style="width:${pc}%"></i></div>
      <span class="pct">${pc}% · 对${a.c}/错${a.w}</span></div>`; }).join('') || '<div class="sub" style="margin:0">暂无数据</div>'}
  </div>
  <h2>薄弱考点（错误 / 遗忘最多）</h2>
  <div class="grid g2">
    ${weak.rows.slice(0, 12).map(r => `<div class="card kd-card" onclick="goKadianBrowse('${enc(r.kd)}')">
      <div class="row" style="justify-content:space-between"><b>${esc((r.kd || '').split('/').pop().slice(0, 26))}</b><span class="tag">${esc(r.m)}</span></div>
      <div class="sub" style="margin:6px 0">做过 ${r.seen} · 对 ${r.c || 0} 错 ${r.w || 0} · 遗忘 ${r.lp || 0}</div>
      <div class="bar"><i style="width:${r.acc}%;background:linear-gradient(90deg,var(--bad),var(--warn))"></i></div>
      <div class="sub" style="margin:4px 0 0">正确率 ${r.acc}%</div>
    </div>`).join('') || '<div class="sub">还没有练过错题，暂无薄弱点</div>'}
  </div>`;
  document.getElementById('g-set').onclick = async () => {
    const v = +document.getElementById('g-in').value || 20;
    await POST('/api/goal', { value: v }); viewStats();
  };
}

/* ---------- settings ---------- */
function viewSettings() {
  app.innerHTML = `
  <h1>设置</h1><p class="sub">外观与学习习惯</p>
  <div class="card" style="max-width:640px">
    <h2 style="margin-top:0">外观</h2>
    <div class="row" style="justify-content:space-between;margin:14px 0">
      <span>主题（点击循环：暗色 → 亮色 → 跟随系统）</span>
      <button class="btn ghost" id="st-theme">当前：<b id="st-theme-name"></b></button>
    </div>
    <div style="margin:14px 0">
      <div class="sub" style="margin-bottom:8px">字号</div>
      <div class="row" id="st-fonts">
        ${[14, 15, 16, 17, 18, 19].map(px => `<button class="qtool" data-px="${px}" style="font-size:${px}px">${px}</button>`).join('')}
      </div>
    </div>
    <div class="row" style="justify-content:space-between;margin:14px 0">
      <span>专注模式（隐藏侧栏，居中窄列）</span>
      <button class="btn ghost" id="st-focus"></button>
    </div>
  </div>
  <div class="card" style="max-width:640px;margin-top:16px">
    <h2 style="margin-top:0">每日目标</h2>
    <div class="row"><input id="st-goal" type="number" min="1" max="500" style="width:100px"><button class="btn" id="st-goal-save">保存</button>
      <span class="sub" id="st-goal-cur" style="margin:0"></span></div>
  </div>
  <div class="card" style="max-width:640px;margin-top:16px">
    <h2 style="margin-top:0">📤 数据导出</h2>
    <p class="sub" style="margin:0 0 12px">将错题本 / 笔记导出为 Markdown 文件，可粘贴到 Obsidian、Notion 等笔记软件。导出内容仅含当前账号数据。</p>
    <div class="row">
      <button class="btn" id="st-exp-wb">📕 导出错题本 (.md)</button>
      <button class="btn" id="st-exp-note">📝 导出笔记 (.md)</button>
    </div>
  </div>
    <div class="card" style="max-width:640px;margin-top:16px">
    <h2 style="margin-top:0">👤 账号管理</h2>
    <div class="row" style="justify-content:space-between;gap:16px;flex-wrap:wrap">
      <div>当前账号：<b id="st-user">—</b>${(ME && ME.username) ? '（用户名 ' + esc(ME.username) + '）' : ''}<br><span class="sub" style="margin:4px 0 0">${(ME && ME.anon) ? '游客模式：学习数据保存在本浏览器对应的云端身份，清空浏览器数据后将无法恢复。' : '各账号的学习进度、收藏与笔记相互隔离，凭用户名 + 密码登录。'}</span></div>
      <div class="row">
        <button class="btn ghost" id="st-switch">切换账号</button>
        <button class="btn" id="st-pw">修改密码</button>
        <button class="btn" id="st-out">退出账号</button>
        <button class="btn danger" id="st-del">删除当前账号</button>
        <button class="btn ok" id="st-upgrade">升级为正式账号</button>
      </div>
    </div>
    ${(ME && ME.anon) ? '<p class="sub" style="margin:10px 0 0">游客模式无需登录即可使用；如需跨设备同步或在其它浏览器继续学习，可「升级为正式账号」设定用户名与密码。</p>' : ''}
  </div>
  <div class="card" style="max-width:640px;margin-top:16px">
    <h2 style="margin-top:0">关于</h2>
    <p class="sub" style="margin:0">题库与学习数据均保存在服务端数据库，登录后使用。共 ${BOOT.total.toLocaleString()} 题。
    重新导入题库不影响已积累的复习进度、笔记与收藏。</p>
  </div>`;
  document.getElementById('st-theme-name').textContent = themeName(getTheme());
  document.getElementById('st-theme').onclick = () => { toggleTheme(); document.getElementById('st-theme-name').textContent = themeName(getTheme()); };
  const fs = getFont();
  document.querySelectorAll('#st-fonts .qtool').forEach(b => {
    if (+b.dataset.px === fs) b.classList.add('on');
    b.onclick = () => { setFont(+b.dataset.px); document.querySelectorAll('#st-fonts .qtool').forEach(x => x.classList.remove('on')); b.classList.add('on'); };
  });
  const fbtn = document.getElementById('st-focus');
  const syncFocus = () => { fbtn.textContent = isFocus() ? '已开启 · 关闭' : '已关闭 · 开启'; fbtn.className = 'btn ' + (isFocus() ? 'ok' : 'ghost'); };
  syncFocus(); fbtn.onclick = () => { setFocus(!isFocus()); syncFocus(); };
  API('/api/streak').then(s => {
    document.getElementById('st-goal').value = s.goal;
    document.getElementById('st-goal-cur').textContent = '今日 ' + s.today + '/' + s.goal;
  });
  document.getElementById('st-goal-save').onclick = async () => {
    const v = +document.getElementById('st-goal').value || 20;
    await POST('/api/goal', { value: v });
    const s = await API('/api/streak'); document.getElementById('st-goal-cur').textContent = '今日 ' + s.today + '/' + s.goal;
    alert('已保存每日目标：' + v + ' 题');
  };
  // 功能5：导出 / 功能6：账号管理
  document.getElementById('st-exp-wb').onclick = async () => {
    const d = await API('/api/export/wrongbook');
    if (!d.text) { alert('当前账号还没有错题'); return; }
    downloadMd(d.filename, d.text);
  };
  document.getElementById('st-exp-note').onclick = async () => {
    const d = await API('/api/export/notes');
    if (!d.text) { alert('当前账号还没有笔记'); return; }
    downloadMd(d.filename, d.text);
  };
  const su = document.getElementById('st-user'); if (su) su.textContent = ME ? (ME.name || ME.username) : '未登录';
  const anon = !!(ME && ME.anon);
  if (document.getElementById('st-switch')) document.getElementById('st-switch').onclick = logoutUser;   // 切换账号 = 退出后用另一账号登录
  const pwBtn = document.getElementById('st-pw'); if (pwBtn) pwBtn.style.display = anon ? 'none' : '';
  const delBtn = document.getElementById('st-del'); if (delBtn) delBtn.style.display = anon ? 'none' : '';
  const upBtn = document.getElementById('st-upgrade'); if (upBtn) upBtn.style.display = anon ? '' : 'none';
  if (pwBtn) pwBtn.onclick = changePassword;
  document.getElementById('st-out').onclick = logoutUser;
  if (delBtn) delBtn.onclick = doDeleteUser;
  if (upBtn) upBtn.onclick = upgradeAccount;
}

/* 游客账号升级为正式账号：设定用户名 + 密码，解除匿名 */
function upgradeAccount() {
  const box = document.createElement('div');
  box.className = 'modal-mask';
  box.innerHTML = `<div class="modal card">
    <h2 style="margin-top:0">🛡️ 升级为正式账号</h2>
    <p class="sub" style="margin-bottom:14px">为游客身份设定用户名和密码后，即可在任何浏览器凭账号登录、继续你的学习进度（数据不变）。</p>
    <label class="fld" style="display:block;margin-bottom:8px">用户名<input id="up-user" size="20" autocomplete="username"></label>
    <label class="fld" style="display:block;margin-bottom:8px">密码（至少 6 位）<input id="up-pw" type="password" size="20" autocomplete="new-password"></label>
    <button class="btn ok" id="up-go" style="width:100%">升级并保存</button>
    <button class="btn ghost" id="up-cancel" style="width:100%;margin-top:8px">取消</button>
  </div>`;
  document.body.appendChild(box);
  const $ = id => box.querySelector('#' + id);
  const cancel = () => box.remove();
  box.querySelector('#up-cancel').onclick = cancel;
  box.onclick = e => { if (e.target === box) cancel(); };
  $('up-go').onclick = async () => {
    const username = $('up-user').value.trim();
    const password = $('up-pw').value;
    if (!username || password.length < 6) { alert('用户名不能为空，密码至少 6 位'); return; }
    try {
      const r = await POST('/api/auth/upgrade', { username, password });
      if (!r.ok) { alert(r.error || '升级失败'); return; }
      onLoginOk(r); box.remove(); location.reload();
    } catch (e) { alert('操作失败：' + e); }
  };
  $('up-user').focus();
}

/* ---------- 主题 / 字号 / 专注 ---------- */
const THEME_ORDER = ['dark', 'light', 'auto'];
function getTheme() { try { return localStorage.getItem('kg_theme') || 'dark'; } catch (e) { return 'dark'; } }
function themeName(t) { return { dark: '暗色', light: '亮色', auto: '跟随系统' }[t] || t; }
function resolveReal(t) {
  if (t === 'auto') return (window.matchMedia && matchMedia('(prefers-color-scheme: light)').matches) ? 'light' : 'dark';
  return t;
}
function applyTheme(t) {
  const real = resolveReal(t);
  document.documentElement.setAttribute('data-theme', real);
  const b = document.getElementById('theme-toggle');
  if (b) { b.textContent = real === 'light' ? '🌙' : (t === 'auto' ? '🌓' : '☀️'); b.title = '主题：' + themeName(t) + '（点击切换）'; }
}
function toggleTheme() {
  const cur = getTheme();
  const next = THEME_ORDER[(THEME_ORDER.indexOf(cur) + 1) % THEME_ORDER.length];
  try { localStorage.setItem('kg_theme', next); } catch (e) { }
  applyTheme(next);
}
function getFont() { try { return +(localStorage.getItem('kg_fontscale')) || 15; } catch (e) { return 15; } }
function setFont(px) { try { localStorage.setItem('kg_fontscale', px); } catch (e) { } document.documentElement.style.fontSize = px + 'px'; }
function isFocus() { return document.documentElement.getAttribute('data-focus') === '1'; }
function setFocus(on) {
  if (on) document.documentElement.setAttribute('data-focus', '1'); else document.documentElement.removeAttribute('data-focus');
  try { localStorage.setItem('kg_focus', on ? '1' : '0'); } catch (e) { }
}

/* ---------- 账号：登录 / 注册 / 退出 / 密码 ---------- */
function syncUserChip() {
  const el = document.getElementById('user-name'); if (!el) return;
  el.textContent = ME ? (ME.name || ME.username) : '未登录';
}

function onLoginOk(d) {
  setTok(d.token); ME = d.user;
  try { localStorage.removeItem('kg_cur_user'); } catch (e) { }   // 老标识不再使用
  syncUserChip();
}

/* 登录 / 注册界面（未登录时全屏，不列出任何账号名，避免泄露他人昵称） */
function showLogin(msg) {
  document.querySelectorAll('.modal-mask').forEach(m => m.remove());
  const box = document.createElement('div');
  box.className = 'modal-mask';
  box.innerHTML = `<div class="modal card">
    <h2 style="margin-top:0">🔐 考公脑库</h2>
    <p class="sub" style="margin-bottom:14px">${msg ? esc(msg) + '<br>' : ''}登录后使用，各账号的学习数据相互隔离。</p>
    <div class="row" style="gap:8px;margin-bottom:12px">
      <button class="qtool on" id="lg-tab-login">登录</button>
      <button class="qtool" id="lg-tab-reg">注册新账号</button>
    </div>
    <label class="fld" style="display:block;margin-bottom:8px">用户名<input id="lg-user" size="20" autocomplete="username"></label>
    <label class="fld" style="display:block;margin-bottom:8px">密码<input id="lg-pw" type="password" size="20" autocomplete="current-password"></label>
    <label class="fld" id="lg-name-wrap" style="display:none;margin-bottom:8px">昵称（可留空）<input id="lg-name" size="20" maxlength="12"></label>
    <button class="btn ok" id="lg-go" style="width:100%">登录</button>
    <div class="guest-sep"><span>或</span></div>
    <button class="btn guest" id="lg-guest" style="width:100%">🚪 游客模式 · 免注册直接使用</button>
    <p class="sub" style="margin:12px 0 0;font-size:12px">💡 首次使用点「注册新账号」。用户名 2-20 字，密码至少 6 位。<br>游客模式无需账号，进度仅保存在本浏览器对应的云端身份，清空浏览器数据后将无法恢复。</p>
  </div>`;
  document.body.appendChild(box);
  const $ = id => box.querySelector('#' + id);
  let mode = 'login';
  const setMode = m => {
    mode = m;
    $('lg-tab-login').classList.toggle('on', m === 'login');
    $('lg-tab-reg').classList.toggle('on', m === 'reg');
    $('lg-go').textContent = m === 'login' ? '登录' : '注册并进入';
    $('lg-name-wrap').style.display = m === 'reg' ? 'block' : 'none';
  };
  $('lg-tab-login').onclick = () => setMode('login');
  $('lg-tab-reg').onclick = () => setMode('reg');
  const submit = async () => {
    const username = $('lg-user').value.trim();
    const password = $('lg-pw').value;
    if (!username || !password) { alert('请输入用户名和密码'); return; }
    try {
      if (mode === 'reg') {
        const r = await fetch('/api/auth/register', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password, name: $('lg-name').value.trim() }),
        }).then(r => r.json());
        if (!r.ok) { alert(r.error || '注册失败'); return; }
        onLoginOk(r); box.remove(); location.reload();
      } else {
        const r = await fetch('/api/auth/login', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username, password }),
        }).then(r => r.json());
        if (r.need_password) { box.remove(); return showSetPassword(username); }
        if (!r.ok) { alert(r.error || '登录失败'); return; }
        onLoginOk(r); box.remove(); location.reload();
      }
    } catch (e) { alert('操作失败：' + e); }
  };
  $('lg-go').onclick = submit;
  $('lg-pw').onkeydown = e => { if (e.key === 'Enter') submit(); };
  $('lg-guest').onclick = async () => {
    try {
      const r = await fetch('/api/auth/guest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      }).then(r => r.json());
      if (!r.ok) { alert(r.error || '进入游客模式失败'); return; }
      onLoginOk(r); box.remove(); location.reload();
    } catch (e) { alert('操作失败：' + e); }
  };
  $('lg-user').focus();
}

/* 老账号首次补设密码 */
function showSetPassword(username) {
  const box = document.createElement('div');
  box.className = 'modal-mask';
  box.innerHTML = `<div class="modal card">
    <h2 style="margin-top:0">🔑 为账号设置密码</h2>
    <p class="sub" style="margin-bottom:14px">账号「${esc(username)}」还没有设置密码。请设置一个密码来保护你的学习记录，设置后其他人就无法进入你的账号。</p>
    <label class="fld" style="display:block;margin-bottom:8px">用户名<input id="sp-user" size="20" value="${esc(username)}"></label>
    <label class="fld" style="display:block;margin-bottom:8px">新密码（至少 6 位）<input id="sp-pw" type="password" size="20" autocomplete="new-password"></label>
    <button class="btn ok" id="sp-go" style="width:100%">设置密码并进入</button>
    <p class="sub" style="margin:12px 0 0;font-size:12px">⚠️ 请尽快完成设置，在此之前任何知道你用户名的人都可能占用该账号。</p>
  </div>`;
  document.body.appendChild(box);
  const $ = id => box.querySelector('#' + id);
  const submit = async () => {
    const username2 = $('sp-user').value.trim();
    const password = $('sp-pw').value;
    if (!username2 || password.length < 6) { alert('密码至少 6 位'); return; }
    let legacy_id = '';
    try { legacy_id = localStorage.getItem('kg_cur_user') || ''; } catch (e) { }
    const r = await fetch('/api/auth/claim', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username2, password, legacy_id }),
    }).then(r => r.json());
    if (!r.ok) { alert(r.error || '设置失败'); return; }
    onLoginOk(r); box.remove(); location.reload();
  };
  $('sp-go').onclick = submit;
  $('sp-pw').onkeydown = e => { if (e.key === 'Enter') submit(); };
  $('sp-pw').focus();
}

/* 账号菜单：只显示自己，不列出他人账号 */
function openUserMenu() {
  const box = document.createElement('div');
  box.className = 'modal-mask';
  box.innerHTML = `<div class="modal card">
    <h2 style="margin-top:0">👤 账号</h2>
    <p class="sub" style="margin-bottom:14px">当前登录：<b>${esc((ME && ME.name) || '—')}</b>${(ME && ME.username) ? '（用户名 ' + esc(ME.username) + '）' : ''}</p>
    <button class="btn" id="um-pw" style="width:100%;margin-bottom:8px">🔑 修改密码</button>
    <button class="btn danger" id="um-out" style="width:100%;margin-bottom:8px">退出账号</button>${(ME && ME.anon) ? '<p class="sub" style="margin:0">游客模式：退出将清除本浏览器的全部学习数据。</p>' : ''}
    <a class="btn ghost" href="#/settings" style="width:100%;margin-bottom:8px" onclick="document.querySelector('.modal-mask').remove()">⚙ 账号管理</a>
    <button class="btn ghost" id="um-close" style="width:100%">关闭</button>
  </div>`;
  document.body.appendChild(box);
  const close = () => box.remove();
  box.querySelector('#um-close').onclick = close;
  box.onclick = e => { if (e.target === box) close(); };
  box.querySelector('#um-out').onclick = () => { close(); logoutUser(); };
  box.querySelector('#um-pw').onclick = () => { close(); changePassword(); };
  if (ME && ME.anon) {
    const pwBtn = box.querySelector('#um-pw');
    if (pwBtn) pwBtn.remove();
  }
}

function changePassword() {
  const box = document.createElement('div');
  box.className = 'modal-mask';
  box.innerHTML = `<div class="modal card">
    <h2 style="margin-top:0">🔑 修改密码</h2>
    <label class="fld" style="display:block;margin-bottom:8px">原密码<input id="cp-old" type="password" size="20"></label>
    <label class="fld" style="display:block;margin-bottom:12px">新密码（至少 6 位）<input id="cp-new" type="password" size="20" autocomplete="new-password"></label>
    <div class="row" style="justify-content:flex-end">
      <button class="btn ghost" id="cp-cancel">取消</button>
      <button class="btn ok" id="cp-go">确认修改</button>
    </div>
  </div>`;
  document.body.appendChild(box);
  const $ = id => box.querySelector('#' + id);
  $('cp-cancel').onclick = () => box.remove();
  $('cp-go').onclick = async () => {
    const old_password = $('cp-old').value, password = $('cp-new').value;
    if (password.length < 6) { alert('新密码至少 6 位'); return; }
    const r = await POST('/api/auth/password', { old_password, password });
    if (!r.ok) { alert(r.error || '修改失败'); return; }
    onLoginOk(r); box.remove(); alert('密码已修改 ✓');
  };
}

/* 退出账号：清除本机登录凭证，数据仍安全保存在服务端 */
async function logoutUser() {
  if (!confirm('退出当前账号？\n你的学习数据仍安全保存在服务器上，之后用用户名和密码即可重新登录。')) return;
  await fetch('/api/auth/logout', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token: tok() }),
  }).catch(() => { });
  setTok(''); ME = null;
  location.reload();
}

async function doDeleteUser() {
  if (!ME) return;
  if (!confirm(`确定删除账号「${ME.name}」吗？\n该账号的复习进度、错题本、收藏和笔记将被永久清除，且无法恢复。`)) return;
  if (!confirm('再次确认：此操作不可撤销，真的要删除吗？')) return;
  const password = prompt('请输入当前密码以确认删除：');
  if (password === null) return;
  const r = await POST('/api/users/delete', { password });
  if (!r.ok) { alert(r.error || '删除失败'); return; }
  setTok(''); ME = null;
  location.reload();
}

/* ---------- boot ---------- */
(async function boot() {
  // 注意：跳转中(HEALING)不 return。若 location.replace 因故未生效，
  // 页面还能继续正常渲染，避免永远停在"加载中…"。
  applyTheme(getTheme());
  setFont(getFont());
  const tb = document.getElementById('theme-toggle'); if (tb) tb.onclick = toggleTheme;
  const fe = document.getElementById('focus-exit'); if (fe) fe.onclick = () => { setFocus(false); };
  if (window.matchMedia) {
    try { matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => { if (getTheme() === 'auto') applyTheme('auto'); }); } catch (e) { }
  }
  // bootstrap 与 auth/me 并行发起（省一个往返 RTT；me 依赖的 token 在 localStorage，无先后依赖）
  const bootP = API('/api/bootstrap').then(b => ({ ok: true, b }), e => ({ ok: false, e }));
  const meP = fetch('/api/auth/me?token=' + encodeURIComponent(tok()))
    .then(r => r.json()).catch(() => null);
  const br = await bootP;
  if (!br.ok) return fatal('后端未就绪：' + br.e);
  BOOT = br.b;

  // 判断是否已登录：未登录则显示登录 / 注册界面
  let me = (await meP) || { ok: false };
  if (me && me.ok && me.user) {
    ME = me.user;
  } else {
    setTok('');
    let legacy = '';
    try { legacy = localStorage.getItem('kg_cur_user') || ''; } catch (e) { }
    showLogin(legacy ? '检测到本机已有账号但尚未设置密码，请输入用户名登录后设置。' : '');
    return;   // 登录成功后会 reload
  }

  const un = document.getElementById('user-name'); if (un) un.onclick = () => openUserMenu();
  syncUserChip();
  nav(); refreshSide();
})();
