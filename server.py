#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""考公间隔重复系统 - 零依赖后端 (Python 标准库)
启动:  python server.py    然后浏览器打开 http://127.0.0.1:8300
"""
import os, re, sys, json, sqlite3, datetime, mimetypes, urllib.parse, threading, email.utils, hashlib, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sm2
import migrate_v2

# ---------- 多用户（纯本地身份标识，最多 3 个账号） ----------
DEFAULT_UID = "u1"
MAX_USERS = 3

VAULT_ROOT = os.environ.get("KAOGONG_VAULT") or os.path.abspath(os.path.join(HERE, "..", "kaogongzhentizhengliu-main"))
DB_PATH = os.path.join(HERE, "kaogong.db")
# 图片优先读本目录内的自包含副本（WebP），没有再回退到原题库
_LOCAL_IMG = os.path.join(HERE, "90-图片")
IMG_ROOT = _LOCAL_IMG if os.path.isdir(_LOCAL_IMG) else os.path.join(VAULT_ROOT, "90-图片")
STATIC = os.path.join(HERE, "static")
PORT = int(os.environ.get("PORT") or os.environ.get("KAOGONG_PORT") or "8300")
mimetypes.add_type("image/webp", ".webp")

_LOCK = threading.Lock()
_conn_local = threading.local()

def db():
    if getattr(_conn_local, "c", None) is None:
        _conn_local.c = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn_local.c.row_factory = sqlite3.Row
    return _conn_local.c

def today():
    return datetime.date.today().isoformat()

def now():
    return datetime.datetime.now().isoformat(timespec="seconds")

def q_one(sql, args=()):
    r = db().execute(sql, args).fetchone()
    return dict(r) if r else None

def q_all(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]

class Unauthorized(Exception):
    """未登录 / token 失效。由 do_GET / do_POST 统一捕获并返回 401。"""


def auth_uid(token):
    """由 token 反查账号 id；无效或缺失返回 None。
    注意：绝不采信客户端传来的 u= 参数，避免越权访问他人数据。"""
    tok = (token or "").strip()
    if not tok:
        return None
    r = q_one("SELECT id FROM users WHERE token=? AND IFNULL(token,'')<>''", (tok,))
    return r["id"] if r else None


def need_uid(token):
    """解析 token → 账号 id；失败抛 Unauthorized（上层转 401）。"""
    uid = auth_uid(token)
    if not uid:
        raise Unauthorized()
    return uid


def ensure_user(uid):
    """确保账号存在；不存在则按默认名创建。返回用户行。"""
    r = q_one("SELECT * FROM users WHERE id=?", (uid,))
    if not r:
        db().execute("INSERT INTO users(id,name,created) VALUES(?,?,?)",
                     (uid, "用户" + uid[-1], now()))
        db().commit()
        r = q_one("SELECT * FROM users WHERE id=?", (uid,))
    return r


def init_user_srs(uid):
    """新账号初始化：为每道题生成一条 status='new' 的进度行（数据按用户隔离）。"""
    n = q_one("SELECT COUNT(*) n FROM srs WHERE user_id=?", (uid,))["n"]
    if n == 0:
        db().execute("INSERT INTO srs(qid,user_id,status) SELECT qid,?,'new' FROM questions", (uid,))
        db().commit()
    return n


# ============ 云端账号：密码哈希 / token 鉴权 ============
def pw_hash(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, 200000)
    return f"{salt.hex()}$200000${dk.hex()}"


def pw_verify(pw, stored):
    try:
        salt_hex, it, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt_hex), int(it))
        return secrets.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def new_token():
    return secrets.token_hex(24)


def cloud_uid(token):
    """由 token 反查云端账号 id（即用户名）；无效返回 None。"""
    if not token:
        return None
    r = q_one("SELECT id FROM cloud_accounts WHERE token=?", (token,))
    return r["id"] if r else None


# ============ 数据导出 / 导入（同步载体，支持本地与云端两套命名空间） ============
def _read_ns(prefix, uid_col, uid):
    """从指定命名空间（本地 srs / 云端 cloud_srs）读取某账号的全量业务数据。
    uid_col 为该命名空间的账号列名（本地为 user_id，云端为 uid）。"""
    return {
        "srs": q_all(f"""SELECT qid,status,ease,interval,repetitions,lapses,due,last_review,
                                correct,wrong,wb_status,wb_streak,last_choice
                         FROM {prefix}srs WHERE {uid_col}=?""", (uid,)),
        "reviews": q_all(f"""SELECT id,qid,grade,ts,interval_before,interval_after,ease,timeout
                           FROM {prefix}reviews WHERE {uid_col}=?""", (uid,)),
        "notes": q_all(f"SELECT qid,text,updated FROM {prefix}notes WHERE {uid_col}=?", (uid,)),
        "favorites": q_all(f"SELECT qid,added FROM {prefix}favorites WHERE {uid_col}=?", (uid,)),
        "doubt_status": q_all(f"SELECT qid,status,note,updated FROM {prefix}doubt_status WHERE {uid_col}=?", (uid,)),
        "exam_history": q_all(f"SELECT id,title,total,correct,score,duration,ts FROM {prefix}exam_history WHERE {uid_col}=?", (uid,)),
    }


def _write_ns(prefix, uid_col, uid, data):
    """把全量业务数据覆盖写入指定命名空间。全量覆盖（个人单用户、最后写入优先）。"""
    if not isinstance(data, dict):
        return
    with _LOCK:
        # srs：保留 qid 维度，按账号覆盖
        db().execute(f"DELETE FROM {prefix}srs WHERE {uid_col}=?", (uid,))
        for r in (data.get("srs") or []):
            db().execute(f"""INSERT OR REPLACE INTO {prefix}srs
                (qid,{uid_col},status,ease,interval,repetitions,lapses,due,last_review,
                 correct,wrong,wb_status,wb_streak,last_choice)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["qid"], uid, r.get("status", "new"), r.get("ease", 2.5), r.get("interval", 0),
                 r.get("repetitions", 0), r.get("lapses", 0), r.get("due"), r.get("last_review"),
                 r.get("correct", 0), r.get("wrong", 0), r.get("wb_status", "active"),
                 r.get("wb_streak", 0), r.get("last_choice")))
        db().execute(f"DELETE FROM {prefix}reviews WHERE {uid_col}=?", (uid,))
        for r in (data.get("reviews") or []):
            db().execute(f"""INSERT OR REPLACE INTO {prefix}reviews
                (id,qid,{uid_col},grade,ts,interval_before,interval_after,ease,timeout)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (r.get("id"), r.get("qid"), uid, r.get("grade"), r.get("ts"),
                 r.get("interval_before"), r.get("interval_after"), r.get("ease"), r.get("timeout", 0)))
        db().execute(f"DELETE FROM {prefix}notes WHERE {uid_col}=?", (uid,))
        for r in (data.get("notes") or []):
            db().execute(f"INSERT OR REPLACE INTO {prefix}notes(qid,{uid_col},text,updated) VALUES(?,?,?,?)",
                         (r["qid"], uid, r.get("text"), r.get("updated")))
        db().execute(f"DELETE FROM {prefix}favorites WHERE {uid_col}=?", (uid,))
        for r in (data.get("favorites") or []):
            db().execute(f"INSERT OR REPLACE INTO {prefix}favorites(qid,{uid_col},added) VALUES(?,?,?)",
                         (r["qid"], uid, r.get("added")))
        db().execute(f"DELETE FROM {prefix}doubt_status WHERE {uid_col}=?", (uid,))
        for r in (data.get("doubt_status") or []):
            db().execute(f"INSERT OR REPLACE INTO {prefix}doubt_status(qid,{uid_col},status,note,updated) VALUES(?,?,?,?,?)",
                         (r["qid"], uid, r.get("status"), r.get("note"), r.get("updated")))
        db().execute(f"DELETE FROM {prefix}exam_history WHERE {uid_col}=?", (uid,))
        for r in (data.get("exam_history") or []):
            db().execute(f"""INSERT OR REPLACE INTO {prefix}exam_history
                (id,title,total,correct,score,duration,ts,{uid_col}) VALUES(?,?,?,?,?,?,?,?)""",
                (r.get("id"), r.get("title"), r.get("total"), r.get("correct"),
                 r.get("score"), r.get("duration"), r.get("ts"), uid))
        db().commit()


def export_data(uid):
    """导出本地某账号（u1..u3）的全部学习数据。"""
    return _read_ns("", "user_id", uid)


def import_data(uid, data):
    """用 data 覆盖本地某账号的全部学习数据。"""
    _write_ns("", "user_id", uid, data)


def export_cloud(uid):
    """导出云端某账号的全部学习数据。"""
    return _read_ns("cloud_", "uid", uid)


def import_cloud(uid, data):
    """用 data 覆盖云端某账号的全部学习数据。"""
    _write_ns("cloud_", "uid", uid, data)


def ensure_tables():
    migrate_v2.ensure(db())   # v2：多用户隔离 / 错题本字段 / 超时标记（幂等）
    db().executescript("""
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS notes(
        qid TEXT PRIMARY KEY, text TEXT, updated TEXT);
    CREATE TABLE IF NOT EXISTS favorites(
        qid TEXT PRIMARY KEY, added TEXT);
    CREATE TABLE IF NOT EXISTS doubt_status(
        qid TEXT PRIMARY KEY, status TEXT, note TEXT, updated TEXT);
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS exam_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, total INT, correct INT, score REAL, duration INT, ts TEXT);
    """)
    if not q_one("SELECT value FROM settings WHERE key='daily_goal'"):
        db().execute("INSERT INTO settings(key,value) VALUES('daily_goal','20')")
    db().commit()
    # 游客(匿名)账号标记：anon=1 表示免注册账号，退出时一并清除数据
    try:
        db().execute("ALTER TABLE users ADD COLUMN anon INTEGER DEFAULT 0")
        db().commit()
    except Exception:
        pass
    # ---------- 云端账号体系（与本地纯昵称账号并存，互不影响） ----------
    db().executescript("""
    CREATE TABLE IF NOT EXISTS cloud_accounts(
        id TEXT PRIMARY KEY, username TEXT UNIQUE,
        pw_hash TEXT, token TEXT, created TEXT);
    CREATE TABLE IF NOT EXISTS cloud_meta(uid TEXT PRIMARY KEY, last_sync TEXT);
    CREATE TABLE IF NOT EXISTS cloud_srs(
        qid TEXT NOT NULL, uid TEXT NOT NULL,
        status TEXT DEFAULT 'new', ease REAL DEFAULT 2.5,
        interval INTEGER DEFAULT 0, repetitions INTEGER DEFAULT 0,
        lapses INTEGER DEFAULT 0, due TEXT, last_review TEXT,
        correct INTEGER DEFAULT 0, wrong INTEGER DEFAULT 0,
        wb_status TEXT DEFAULT 'active', wb_streak INTEGER DEFAULT 0,
        last_choice TEXT, PRIMARY KEY(qid, uid));
    CREATE TABLE IF NOT EXISTS cloud_reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT, qid TEXT, uid TEXT NOT NULL,
        grade INT, ts TEXT, interval_before INT, interval_after INT, ease REAL,
        timeout INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS cloud_notes(
        qid TEXT NOT NULL, uid TEXT NOT NULL, text TEXT, updated TEXT,
        PRIMARY KEY(qid, uid));
    CREATE TABLE IF NOT EXISTS cloud_favorites(
        qid TEXT NOT NULL, uid TEXT NOT NULL, added TEXT, PRIMARY KEY(qid, uid));
    CREATE TABLE IF NOT EXISTS cloud_doubt_status(
        qid TEXT NOT NULL, uid TEXT NOT NULL, status TEXT, note TEXT, updated TEXT,
        PRIMARY KEY(qid, uid));
    CREATE TABLE IF NOT EXISTS cloud_exam_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, total INT, correct INT,
        score REAL, duration INT, ts TEXT, uid TEXT);
    CREATE INDEX IF NOT EXISTS idx_cloud_srs ON cloud_srs(uid);
    CREATE INDEX IF NOT EXISTS idx_cloud_rev ON cloud_reviews(uid);
    """)

def get_setting(key, default=None):
    r = q_one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default

def resolve_qid(qid_int):
    r = q_one("SELECT qid FROM questions WHERE id=?", (qid_int,))
    return r["qid"] if r else None

# 练习阶段对题干/材料做答案剧透清洗，避免直接泄露正确选项字母
_ANS_LEAK_PATTERNS = [
    # 负向断言(?!\s*项)：避免误伤题干设问，如"正确答案为D项的题目有多少道？"
    re.compile(r"正确答案[是为：: ]*[A-F](?!\s*项)"),
    re.compile(r"本题(的)?答案[是为：: ]*[A-F]"),
    re.compile(r"答案选[是为：: ]*[A-F]"),
    re.compile(r"应选[择：: ]*[A-F]"),
    re.compile(r"应当?选[择：: ]*[A-F]"),
    re.compile(r"因此?选[是为：: ]*[A-F]"),
    re.compile(r"故(而)?选[是为：: ]*[A-F]"),
]


def mask_answer_leak(text):
    """把题干/材料里直接点出正确选项的句式替换为占位，避免练习时剧透。
    仅作用于做题阶段(reveal=False)；作答后看解析(reveal=True)保留原文。"""
    if not text:
        return text
    for p in _ANS_LEAK_PATTERNS:
        text = p.sub("【答案已隐藏】", text)
    return text


# ---- 整块泄露剥离：材料里混入的「疑点/官方解析」备注会直接写出"答案X" ----
# Obsidian callout 块：'> [!warning] 标题' 及其后续连续的 '>' 行
_LEAK_CALLOUT = re.compile(r"^[ \t]*>[^\n]*(?:\n[ \t]*>[^\n]*)*", re.M)
# HTML 引用块
_LEAK_BLOCKQUOTE = re.compile(r"<blockquote\b[\s\S]*?</blockquote>", re.I)
# 行内强解析信号（用于兜底清理不带 callout 标记的裸解析段落）
_LEAK_LINE = re.compile(
    r"(官方解析|疑点|待人工复核|人工复核"
    r"|不影响\s*(正确)?答案|不影响结论|不影响本题"
    r"|与答案\s*[：:]?\s*[A-F]|答案\s*[：:]?\s*[A-F]\s*(无误|一致|不受影响)"
    r"|以\s*[A-F]\s*作答为准|解析笔误|应为笔误|结论\s*无误)")


def strip_leak_blocks(text):
    """剥离材料里混入的解析/疑点备注块（做题阶段调用）。
    这些块形如 '> [!warning] 疑点（官方解析可能有误…）' 并在正文中直接写明"答案B"，
    属于解析范畴，作答前展示等于剧透。看解析(reveal=True)时保留原文。"""
    if not text:
        return text
    s = _LEAK_BLOCKQUOTE.sub("", text)
    s = _LEAK_CALLOUT.sub("", s)          # 覆盖 190 条 callout 材料
    # 兜底：整行含强解析信号的（且不含图片标签）直接剔除，避免裸解析段落残留
    out = []
    for line in s.split("\n"):
        plain = re.sub(r"<[^>]+>", "", line)
        if plain.strip() and not re.search(r"<img|<p>|</p>", line, re.I) \
                and _LEAK_LINE.search(plain):
            continue
        out.append(line)
    s = "\n".join(out)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def safe_material(text, mask=True):
    """做题阶段展示材料：先剥离泄露块，再打码残留句式。"""
    if not text:
        return text
    s = strip_leak_blocks(text)
    return mask_answer_leak(s) if mask else s


def clean_card(row, reveal=False, uid=DEFAULT_UID):
    d = {
        "id": row["id"], "qid": row["qid"], "module": row["module"],
        "category": row["category"], "kadian": row["kadian"],
        "region": row["region"], "year": row["year"], "paper": row["paper"],
        "ask_model": row["ask_model"],
        # 做题阶段(reveal=False)才清洗；看解析时还原原题原文
        "stem": mask_answer_leak(row["stem"]) if not reveal else row["stem"],
        "options": json.loads(row["options"] or "[]"),
        "material": safe_material(row["material"]) if not reveal else row["material"],
        "material_ref": row.get("material_ref", ""),
    }
    if not reveal:
        for o in d["options"]:
            o["is_answer"] = False
    else:
        d.update({
            "answer": row["answer"], "reasoning": row["reasoning"],
            "fastest": row["fastest"], "pitfalls": row["pitfalls"],
            "mother": row["mother"], "analysis": row["analysis"],
            "doubt": row["doubt"], "related": row["related"],
        })
        eqid = row["qid"]
        nt = q_one("SELECT text,updated FROM notes WHERE qid=? AND user_id=?", (eqid, uid))
        d["note"] = nt["text"] if nt else ""
        d["note_updated"] = nt["updated"] if nt else ""
        d["fav"] = 1 if q_one("SELECT qid FROM favorites WHERE qid=? AND user_id=?", (eqid, uid)) else 0
        ds = q_one("SELECT status,note FROM doubt_status WHERE qid=? AND user_id=?", (eqid, uid))
        d["doubt_status"] = ds["status"] if ds else ""
        d["srs_wrong"] = (q_one("SELECT wrong FROM srs WHERE qid=? AND user_id=?", (eqid, uid)) or {}).get("wrong", 0)
    vr = q_one("SELECT * FROM review_verified WHERE qid=?", (row["qid"],))
    if vr:
        # 做题阶段仅给"已复核"标记，不剧透答案；作答后(reveal)才给完整核实详情
        d["verified"] = {"status": vr["status"]} if not reveal else {
            "status": vr["status"], "answer": vr["verified_answer"],
            "supplement": vr["supplement"], "source_name": vr["source_name"],
            "source_url": vr["source_url"], "reasoning": vr["reasoning"],
        }
    else:
        d["verified"] = None
    return d

def plain_text(html, n=None):
    """题干/材料去 HTML 标签，取纯文本（导出与搜索摘要用）。"""
    s = re.sub(r"<[^>]+>", " ", html or "")
    s = re.sub(r"&(nbsp|amp|lt|gt|quot);?", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:n] if n else s


def build_where(module, category, kadian):
    cond, args = [], []
    if module:
        cond.append("q.module=?"); args.append(module)
    if category:
        cond.append("q.category=?"); args.append(category)
    if kadian:
        cond.append("q.kadian LIKE ?"); args.append("%" + kadian + "%")
    return (" AND " + " AND ".join(cond)) if cond else "", args


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 跨域：云端服务器常与本机应用不同源（不同主机/端口），需放行 CORS
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        # 默认禁用缓存；但若 extra 已提供 Cache-Control（如静态资源/图片长缓存），
        # 则不再叠加 no-store，避免两个 Cache-Control 头互相冲突导致缓存失效。
        if not extra or "Cache-Control" not in extra:
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _file(self, path, cache=True, max_age=86400, revalidate=False):
        """发送磁盘文件；静态资源/图片默认长缓存(强缓存+ETag+304)，
        避免资料分析材料展开时几十张公式图反复向服务端拉取导致卡顿。
        revalidate=True → 只发 `no-cache`（仍带 ETag，未变更返回 304），
        用于 index.html / app.js / style.css，保证改版后浏览器立即拿到新文件。"""
        try:
            st = os.stat(path)
        except Exception:
            return self._send(404, {"error": "not found"})
        with open(path, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        extra = {}
        if cache:
            etag = f'"{st.st_size:x}-{int(st.st_mtime):x}"'
            extra["Cache-Control"] = "no-cache" if revalidate else f"public, max-age={max_age}"
            extra["Last-Modified"] = email.utils.formatdate(st.st_mtime, usegmt=True)
            extra["ETag"] = etag
            inm = self.headers.get("If-None-Match")
            ims = self.headers.get("If-Modified-Since")
            if inm and inm.strip('"') == etag.strip('"'):
                return self._send(304, b"", extra=extra)
            if ims and not inm:
                try:
                    its = email.utils.parsedate_to_datetime(ims)
                    if its.tzinfo is None:
                        its = its.replace(tzinfo=datetime.timezone.utc)
                    if st.st_mtime <= its.timestamp() + 1:
                        return self._send(304, b"", extra=extra)
                except Exception:
                    pass
        self._send(200, data, ctype, extra=extra)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)
        g = lambda k, d="": (qs.get(k) or [d])[0]

        if path == "/" or path == "/index.html":
            return self._file(os.path.join(STATIC, "index.html"), cache=True, revalidate=True)
        if path == "/favicon.ico":
            return self._send(200, b"", "image/x-icon")
        if path == "/sw.js":
            # Service Worker 必须从根路径发出，作用域才能覆盖整站；
            # 同时给 Service-Worker-Allowed 兜底，并禁止缓存以便随时更新策略。
            try:
                with open(os.path.join(STATIC, "sw.js"), "rb") as f:
                    data = f.read()
            except Exception:
                return self._send(404, {"error": "not found"})
            return self._send(200, data, "application/javascript; charset=utf-8",
                              extra={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})
        if path.startswith("/static/"):
            fp = os.path.normpath(os.path.join(STATIC, path[len("/static/"):]))
            if not fp.startswith(STATIC):
                return self._send(403, {"error": "forbidden"})
            # 页面脚本/样式：改版后必须立即可见 → no-cache + ETag 校验
            if fp.endswith(".js") or fp.endswith(".css"):
                return self._file(fp, cache=True, revalidate=True)
            return self._file(fp, cache=True, max_age=600)
        if path.startswith("/90-图片/"):
            rel = path[len("/90-图片/"):]
            fp = os.path.normpath(os.path.join(IMG_ROOT, rel))
            if not fp.startswith(IMG_ROOT):
                return self._send(403, {"error": "forbidden"})
            if not os.path.exists(fp):  # 缺图兜底：返回"图片暂缺"占位 WebP，避免破图图标
                fp = os.path.join(IMG_ROOT, "__missing__.webp")
            return self._file(fp, cache=True, max_age=86400)
        if path.startswith("/api/"):
            try:
                return self.api(path, g)
            except Unauthorized:
                return self._send(401, {"error": "未登录或登录已失效，请重新登录"})
            except Exception as e:
                import traceback; traceback.print_exc()
                return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "no route"})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        routes = {
            "/api/grade": self.api_grade,
            "/api/note": self.api_note,
            "/api/fav": self.api_fav,
            "/api/goal": self.api_goal,
            "/api/doubt": self.api_doubt,
            "/api/exam/submit": self.api_exam_submit,
            "/api/users/rename": self.api_users_rename,
            "/api/users/delete": self.api_users_delete,
            # 账号：注册 / 登录 / 退出 / 改密码 / 老账号补设密码
            "/api/auth/register": self.api_auth_register,
            "/api/auth/login": self.api_auth_login,
            "/api/auth/logout": self.api_auth_logout,
            "/api/auth/guest": self.api_auth_guest,
            "/api/auth/upgrade": self.api_auth_upgrade,
            "/api/auth/password": self.api_auth_password,
            "/api/auth/claim": self.api_auth_claim,
        }
        fn = routes.get(path)
        if fn:
            try:
                return fn(payload)
            except Unauthorized:
                return self._send(401, {"error": "未登录或登录已失效，请重新登录"})
            except Exception as e:
                import traceback; traceback.print_exc()
                return self._send(500, {"error": str(e)})
        return self._send(404, {"error": "no route"})

    def do_OPTIONS(self):
        """CORS 预检：云端接口跨源调用时浏览器会先发 OPTIONS。"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- GET API ----------
    def api(self, path, g):
        if path == "/api/bootstrap":
            mods = q_all("SELECT module, COUNT(*) n FROM questions GROUP BY module ORDER BY n DESC")
            cats = q_all("SELECT module, category, COUNT(*) n FROM questions GROUP BY module,category ORDER BY n DESC")
            total = q_one("SELECT COUNT(*) n FROM questions")["n"]
            return self._send(200, {"total": total, "modules": mods, "categories": cats, "today": today()})

        if path == "/api/stats":
            t = today(); uid = need_uid(g("token"))
            due_new = q_one("SELECT COUNT(*) n FROM questions q JOIN srs s ON s.qid=q.qid WHERE s.user_id=? AND s.status='new'", (uid,))["n"]
            due_rev = q_one("SELECT COUNT(*) n FROM srs WHERE user_id=? AND status!='new' AND due<=?", (uid, t))["n"]
            learned = q_one("SELECT COUNT(*) n FROM srs WHERE user_id=? AND status!='new'", (uid,))["n"]
            studied_today = q_one("SELECT COUNT(DISTINCT qid) n FROM reviews WHERE user_id=? AND date(ts)=?", (uid, t))["n"]
            wrong_n = q_one("SELECT COUNT(*) n FROM srs WHERE user_id=? AND wrong>0", (uid,))["n"]
            fav_n = q_one("SELECT COUNT(*) n FROM favorites WHERE user_id=?", (uid,))["n"]
            by_mod = q_all("""SELECT q.module m, COUNT(*) total,
                              SUM(CASE WHEN s.status!='new' THEN 1 ELSE 0 END) learned
                              FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                              GROUP BY q.module""", (uid,))
            due_by_mod = q_all("""SELECT q.module m, COUNT(*) n FROM srs s JOIN questions q ON q.qid=s.qid
                                  WHERE s.user_id=? AND s.status!='new' AND s.due<=? GROUP BY q.module""", (uid, t))
            mastery = q_all("SELECT interval FROM srs WHERE user_id=? AND status!='new'", (uid,))
            buckets = {"学习中": 0, "初记": 0, "短期": 0, "长期": 0, "稳固": 0}
            for r in mastery:
                buckets[sm2.mastery_bucket(r["interval"])] += 1
            return self._send(200, {
                "new": due_new, "due_review": due_rev, "learned": learned,
                "studied_today": studied_today, "by_module": by_mod,
                "due_by_module": due_by_mod, "mastery": buckets,
                "wrong": wrong_n, "fav": fav_n,
                "total": sum(buckets.values()) + due_new,
            })

        if path == "/api/next":
            mode = g("mode", "mix")
            size = max(1, min(60, int(g("size", "10") or 10)))
            uid = need_uid(g("token"))
            cond, args = build_where(g("module"), g("category"), g("kadian"))
            ref = g("ref")
            # 跨次去重：排除近期已练习过的 qid（由前端传入，限额 500，仅作用于新题补充分支）
            excl = g("exclude", "")
            excl_ids = [x for x in excl.split(",") if re.fullmatch(r"\d+", x.strip())][:500] if excl else []
            excl_clause = (" AND q.qid NOT IN (" + ",".join("?" * len(excl_ids)) + ")") if excl_ids else ""
            ea = args + excl_ids
            if mode == "ids":
                # 按指定 id 取题（临时标记题复盘），保持传入顺序
                ids = [int(x) for x in g("ids", "").split(",") if re.fullmatch(r"\d+", x.strip())][:200]
                if not ids:
                    rows = []
                else:
                    ph = ",".join("?" * len(ids))
                    rows = q_all(f"SELECT * FROM questions WHERE id IN ({ph})", ids)
                    order = {int(x): i for i, x in enumerate(ids)}
                    rows.sort(key=lambda r: order.get(r["id"], 9999))
            elif mode == "material":
                rows = q_all("SELECT * FROM questions WHERE material_ref=? ORDER BY id LIMIT ?", (ref, size))
            elif mode == "fav":
                rows = q_all(f"""SELECT q.* FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                                WHERE q.qid IN (SELECT qid FROM favorites WHERE user_id=?){cond}{excl_clause}
                                ORDER BY RANDOM() LIMIT ?""", (uid, uid, *ea, size))
            elif mode == "wrongredo":
                # 错题专项重练：只抽「待重练」的错题，随机打散
                rows = q_all(f"""SELECT q.* FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                                WHERE s.wrong>0 AND IFNULL(s.wb_status,'active')='active'{cond}{excl_clause}
                                ORDER BY RANDOM() LIMIT ?""", (uid, *ea, size))
            elif mode == "wrong":
                rows = q_all(f"""SELECT q.* FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                                WHERE s.wrong>0{cond}{excl_clause} ORDER BY s.wrong DESC, s.ease ASC LIMIT ?""",
                             (uid, *ea, size))
            elif mode == "review":
                rows = q_all(f"""SELECT q.* FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                                WHERE s.status!='new' AND s.due<=?{cond}{excl_clause}
                                ORDER BY s.due ASC, s.ease ASC LIMIT ?""", (uid, today(), *ea, size))
            elif mode == "mix":
                # 重复练习题优先：答错 > 遗忘(lapses) > 到期复习（此池不受 exclude 限制，刚做错的也能立刻重练）；
                # 新题仅作补充，且排除近期已练过的，避免无意义重复。
                rows = q_all(f"""SELECT q.* FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                                WHERE 1=1{cond} AND (s.wrong>0 OR s.lapses>0 OR (s.status!='new' AND s.due<=?))
                                ORDER BY (CASE WHEN s.wrong>0 THEN 0 WHEN s.lapses>0 THEN 1 ELSE 2 END),
                                         s.due ASC, RANDOM() LIMIT ?""", (uid, today(), *args, size))
                if len(rows) < size:
                    fill = q_all(f"""SELECT q.* FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                                    WHERE 1=1{cond} AND s.status='new'{excl_clause} ORDER BY RANDOM() LIMIT ?""",
                                 (uid, *ea, size - len(rows)))
                    rows += fill
            else:  # new / study / exam
                rows = q_all(f"""SELECT q.* FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                                WHERE s.status='new'{cond}{excl_clause} ORDER BY RANDOM() LIMIT ?""", (uid, *ea, size))
            return self._send(200, {"mode": mode, "count": len(rows),
                                    "cards": [clean_card(r, reveal=False) for r in rows]})

        if path == "/api/question":
            uid = need_uid(g("token"))
            r = q_one("SELECT * FROM questions WHERE id=?", (int(g("id", "0")),))
            if not r:
                return self._send(404, {"error": "no such question"})
            srs = q_one("SELECT * FROM srs WHERE qid=? AND user_id=?", (r["qid"], uid))
            return self._send(200, {"card": clean_card(r, reveal=True, uid=uid), "srs": srs})

        if path == "/api/browse":
            page = max(1, int(g("page", "1") or 1)); size = max(1, min(100, int(g("size", "25") or 25)))
            uid = need_uid(g("token"))
            cond, args = build_where(g("module"), g("category"), g("kadian"))
            region = g("region"); year = g("year"); kw = g("q")
            only = g("only")  # wrong | fav | mastered
            a2, b2 = list(args), list(args)
            if only == "wrong":
                cond += " AND s.wrong>0 AND IFNULL(s.wb_status,'active')='active'"
            elif only == "mastered":
                cond += " AND s.wrong>0 AND s.wb_status='cleared'"
            elif only == "fav":
                cond += " AND q.qid IN (SELECT qid FROM favorites WHERE user_id=?)"
                a2.append(uid); b2.append(uid)
            if region:
                cond += " AND q.region=?"; a2.append(region); b2.append(region)
            if year:
                cond += " AND q.year=?"; a2.append(year); b2.append(year)
            if kw:
                cond += " AND (q.stem LIKE ? OR q.kadian LIKE ?)"; a2 += ["%" + kw + "%"] * 2; b2 += ["%" + kw + "%"] * 2
            total = q_one(f"""SELECT COUNT(*) n FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                              WHERE 1=1{cond}""", (uid, *b2))["n"]
            rows = q_all(f"""SELECT q.id,q.qid,q.module,q.category,q.kadian,q.region,q.year,q.answer,q.has_image,q.ask_model,
                            s.status,s.due,s.interval,s.correct,s.wrong,s.wb_status,
                            CASE WHEN f.qid IS NOT NULL THEN 1 ELSE 0 END fav
                            FROM questions q
                            JOIN srs s ON s.qid=q.qid AND s.user_id=?
                            LEFT JOIN favorites f ON f.qid=q.qid AND f.user_id=?
                            WHERE 1=1{cond}
                            ORDER BY q.module,q.category,q.year DESC LIMIT ? OFFSET ?""",
                         (uid, uid, *a2, size, (page - 1) * size))
            return self._send(200, {"total": total, "page": page, "size": size, "rows": rows})

        if path == "/api/kadian":
            cond, args = build_where(g("module"), g("category"), "")
            rows = q_all(f"""SELECT q.kadian kd, q.module m, q.category c, COUNT(*) n,
                            MIN(q.mother) mother
                            FROM questions q WHERE 1=1{cond}
                            GROUP BY q.kadian ORDER BY n DESC LIMIT ?""", (*args, int(g("size", "500") or 500)))
            return self._send(200, {"rows": rows})

        if path == "/api/facets":
            return self._send(200, {
                "regions": [r["region"] for r in q_all("SELECT DISTINCT region FROM questions WHERE region!='' ORDER BY region")],
                "years": [r["year"] for r in q_all("SELECT DISTINCT year FROM questions WHERE year!='' ORDER BY year DESC")],
            })

        if path == "/api/favs":
            uid = need_uid(g("token"))
            ids = [r["qid"] for r in q_all("SELECT qid FROM favorites WHERE user_id=?", (uid,))]
            return self._send(200, {"ids": ids})

        if path == "/api/heatmap":
            days = max(30, min(365, int(g("days", "120") or 120)))
            uid = need_uid(g("token"))
            rows = q_all("""SELECT date(ts) d, COUNT(*) n FROM reviews WHERE user_id=?
                            GROUP BY date(ts) ORDER BY d DESC LIMIT ?""", (uid, days))
            return self._send(200, {"days": [{"d": r["d"], "n": r["n"]} for r in reversed(rows)]})

        if path == "/api/streak":
            uid = need_uid(g("token"))
            dates = [r["d"] for r in q_all("""SELECT DISTINCT date(ts) d FROM reviews
                                              WHERE user_id=? ORDER BY d DESC""", (uid,))]
            dset = set(dates)
            cur = 0
            probe = datetime.date.today()
            # 今天没学也不断签：从昨天开始数
            if probe.isoformat() not in dset and dates:
                probe = probe - datetime.timedelta(days=1)
            while probe.isoformat() in dset:
                cur += 1
                probe -= datetime.timedelta(days=1)
            # best streak
            best = 0; run = 0; prev = None
            for d in sorted(dset):
                dt = datetime.date.fromisoformat(d)
                if prev and (dt - prev).days == 1:
                    run += 1
                else:
                    run = 1
                best = max(best, run); prev = dt
            goal = int(get_setting("daily_goal", "20") or 20)
            today_n = q_one("SELECT COUNT(DISTINCT qid) n FROM reviews WHERE user_id=? AND date(ts)=?",
                            (uid, today()))["n"]
            return self._send(200, {"current": cur, "best": best, "goal": goal, "today": today_n})

        if path == "/api/accuracy":
            uid = need_uid(g("token"))
            def agg(col):
                return q_all(f"""SELECT q.{col} k, COUNT(*) n, SUM(s.correct) c, SUM(s.wrong) w
                                FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                                WHERE s.status!='new' GROUP BY q.{col} HAVING n>=1 ORDER BY n DESC LIMIT 40""", (uid,))
            return self._send(200, {
                "module": agg("module"), "category": agg("category"),
                "region": agg("region"), "year": agg("year"),
            })

        if path == "/api/weak":
            uid = need_uid(g("token"))
            rows = q_all("""SELECT q.kadian kd, q.module m, q.category cat, COUNT(*) seen,
                            SUM(s.correct) c, SUM(s.wrong) w, SUM(s.lapses) lp, MIN(s.ease) ease
                            FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                            WHERE s.status!='new' GROUP BY q.kadian
                            HAVING seen>=1 AND (w>0 OR lp>0)
                            ORDER BY (CAST(w AS REAL)+lp)/seen DESC, ease ASC LIMIT 30""", (uid,))
            for r in rows:
                tot = (r["c"] or 0) + (r["w"] or 0)
                r["acc"] = round((r["c"] or 0) / tot * 100, 1) if tot else 0
            return self._send(200, {"rows": rows})

        if path == "/api/materials":
            size = max(1, min(100, int(g("size", "50") or 50)))
            rows = q_all("""SELECT g.ref, g.n, q.id rep_id, q.material, q.kadian
                            FROM (SELECT material_ref ref, COUNT(*) n, MIN(id) rep
                                  FROM questions WHERE module='资料分析' AND material_ref!=''
                                  GROUP BY material_ref HAVING n>=2) g
                            JOIN questions q ON q.id=g.rep
                            ORDER BY g.n DESC LIMIT ?""", (size,))
            out = []
            for r in rows:
                # 先剥离疑点/解析块，避免材料组列表页剧透答案
                snippet = re.sub(r"<[^>]+>", " ", strip_leak_blocks(r["material"] or ""))
                snippet = re.sub(r"\s+", " ", snippet).strip()[:70]
                out.append({"ref": r["ref"], "n": r["n"], "rep_id": r["rep_id"], "snippet": snippet})
            return self._send(200, {"rows": out})

        if path == "/api/doubts":
            page = max(1, int(g("page", "1") or 1)); size = max(1, min(50, int(g("size", "20") or 20)))
            only = g("only")  # pending
            cond = "q.doubt!=''"; args = []
            if only == "pending":
                cond += " AND (ds.status IS NULL OR ds.status='')"
            total = q_one(f"""SELECT COUNT(*) n FROM questions q LEFT JOIN doubt_status ds ON ds.qid=q.qid
                             WHERE {cond}""", args)["n"]
            rows = q_all(f"""SELECT q.id,q.qid,q.module,q.category,q.kadian,q.region,q.year,q.doubt,
                            ds.status ds_status, ds.note ds_note
                            FROM questions q LEFT JOIN doubt_status ds ON ds.qid=q.qid
                            WHERE {cond} ORDER BY q.module,q.category LIMIT ? OFFSET ?""",
                         args + [size, (page - 1) * size])
            return self._send(200, {"total": total, "page": page, "size": size, "rows": rows})

        if path == "/api/exam/history":
            uid = need_uid(g("token"))
            rows = q_all("SELECT * FROM exam_history WHERE user_id=? ORDER BY id DESC LIMIT 30", (uid,))
            return self._send(200, {"rows": rows})

        # ---------- 账号 ----------
        if path == "/api/users":
            # 仅返回「当前登录者自己」的信息；不再列出全部账号，避免泄露他人昵称
            uid = need_uid(g("token"))
            u = q_one("SELECT id,name,username,anon,created FROM users WHERE id=?", (uid,))
            return self._send(200, {"users": [u] if u else [], "me": u, "max": 0})
        if path == "/api/auth/me":
            uid = auth_uid(g("token"))
            if not uid:
                return self._send(200, {"ok": False})
            u = q_one("SELECT id,name,username,anon FROM users WHERE id=?", (uid,))
            return self._send(200, {"ok": True, "user": u})

        # ---------- 功能1：错题本统计 ----------
        if path == "/api/wrongbook/stats":
            uid = need_uid(g("token"))
            total = q_one("SELECT COUNT(*) n FROM srs WHERE user_id=? AND wrong>0", (uid,))["n"]
            mastered = q_one("""SELECT COUNT(*) n FROM srs WHERE user_id=? AND wrong>0
                                AND wb_status='cleared'""", (uid,))["n"]
            return self._send(200, {"total": total, "mastered": mastered, "pending": total - mastered})

        # ---------- 功能4：题干关键词搜题 ----------
        if path == "/api/search":
            uid = need_uid(g("token"))
            kw = (g("q") or "").strip()
            size = max(1, min(60, int(g("size", "30") or 30)))
            if not kw:
                return self._send(200, {"total": 0, "rows": []})
            cond, args = build_where(g("module"), g("category"), "")
            like, pre = "%" + kw + "%", kw + "%"
            total = q_one(f"""SELECT COUNT(*) n FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                              WHERE (q.stem LIKE ? OR q.kadian LIKE ?){cond}""",
                          (uid, like, like, *args))["n"]
            rows = q_all(f"""SELECT q.id,q.qid,q.module,q.category,q.kadian,q.region,q.year,q.stem,
                                    q.answer,q.ask_model,s.wrong,s.status,
                                    (SELECT 1 FROM favorites f WHERE f.qid=q.qid AND f.user_id=?) fav
                             FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                             WHERE (q.stem LIKE ? OR q.kadian LIKE ?){cond}
                             ORDER BY (CASE WHEN q.stem LIKE ? THEN 0 ELSE 1 END), q.id LIMIT ?""",
                         (uid, uid, like, like, *args, pre, size))
            out = []
            for r in rows:
                d = {k: r[k] for k in ("id", "qid", "module", "category", "kadian",
                                       "region", "year", "answer", "wrong", "status", "fav")}
                d["snippet"] = plain_text(r["stem"], 20)      # 题干前 20 字摘要
                out.append(d)
            return self._send(200, {"total": total, "rows": out})

        # ---------- 功能5：Markdown 导出 ----------
        if path == "/api/export/wrongbook":
            uid = need_uid(g("token"))
            rows = q_all("""SELECT q.id,q.qid,q.module,q.category,q.kadian,q.stem,q.options,q.answer,
                                   q.analysis,q.reasoning,q.fastest,q.pitfalls,
                                   s.wrong,s.correct,s.last_choice,s.wb_status
                            FROM questions q JOIN srs s ON s.qid=q.qid AND s.user_id=?
                            WHERE s.wrong>0 ORDER BY q.module, s.wrong DESC, q.id""", (uid,))
            bymod = {}
            for r in rows:
                bymod.setdefault(r["module"] or "未分类", []).append(r)
            L = [f"# 错题本 · {today()}", "",
                 f"> 共 {len(rows)} 题 · 待重练 {sum(1 for r in rows if (r['wb_status'] or 'active') != 'cleared')} "
                 f"· 已掌握 {sum(1 for r in rows if r['wb_status'] == 'cleared')}", ""]
            for mod, items in bymod.items():
                L.append(f"## {mod}（{len(items)} 题）")
                for i, r in enumerate(items, 1):
                    L.append(f"\n### {i}. {plain_text(r['kadian'], 40) or '未命名考点'}")
                    L.append(f"- **题干**：{plain_text(r['stem'], 300)}")
                    try:
                        opts = json.loads(r["options"] or "[]")
                    except Exception:
                        opts = []
                    if opts:
                        L.append("- **选项**：" + " / ".join(
                            f"{o.get('key','')}. {plain_text(o.get('html') or o.get('label',''), 60)}" for o in opts))
                    L.append(f"- **你的答案**：{r['last_choice'] or '—'}")
                    L.append(f"- **正确答案**：{r['answer'] or '—'}")
                    L.append(f"- **错误次数**：{r['wrong'] or 0}　**答对次数**：{r['correct'] or 0}")
                    L.append(f"- **状态**：{'✅ 已掌握（已移出待重练）' if r['wb_status']=='cleared' else '🔁 待重练'}")
                    if r["analysis"]:
                        L.append(f"- **官方解析**：{plain_text(r['analysis'], 500)}")
                    elif r["reasoning"]:
                        L.append(f"- **推理链**：{plain_text(r['reasoning'], 500)}")
                    if r["pitfalls"]:
                        L.append(f"- **易错点**：{plain_text(r['pitfalls'], 300)}")
                    L.append(f"- [查看详情](#/detail?id={r['id']})")
            return self._send(200, {"filename": f"错题本_{today().replace('-','')}.md",
                                    "count": len(rows), "text": "\n".join(L) + "\n"})

        if path == "/api/export/notes":
            uid = need_uid(g("token"))
            rows = q_all("""SELECT q.id,q.qid,q.module,q.kadian,q.stem,n.text,n.updated
                            FROM notes n JOIN questions q ON q.qid=n.qid
                            WHERE n.user_id=? AND TRIM(IFNULL(n.text,''))!=''
                            ORDER BY q.module, n.updated DESC""", (uid,))
            bymod = {}
            for r in rows:
                bymod.setdefault(r["module"] or "未分类", []).append(r)
            L = [f"# 我的笔记 · {today()}", "", f"> 共 {len(rows)} 条笔记", ""]
            for mod, items in bymod.items():
                L.append(f"## {mod}（{len(items)} 条）")
                for i, r in enumerate(items, 1):
                    L.append(f"\n### {i}. {plain_text(r['kadian'], 40) or '未命名考点'}")
                    L.append(f"- **题干摘要**：{plain_text(r['stem'], 80)}")
                    L.append(f"- **更新时间**：{r['updated'] or '—'}")
                    L.append(f"\n{r['text']}\n")
            return self._send(200, {"filename": f"我的笔记_{today().replace('-','')}.md",
                                    "count": len(rows), "text": "\n".join(L) + "\n"})

        return self._send(404, {"error": "no api"})

    # ---------- POST API ----------
    def _apply_grade(self, eqid, grade, uid=DEFAULT_UID, timeout=0, choice=None):
        """写入一次评分。含错题本状态机：连续 2 次自评「一般/熟悉」自动移出错题本。"""
        st = q_one("SELECT * FROM srs WHERE qid=? AND user_id=?", (eqid, uid))
        if not st:
            return None
        before = st["interval"]
        new = sm2.grade_state(st, grade)
        correct = 1 if grade >= 3 else 0
        wrong_now = (st["wrong"] or 0) + (0 if correct else 1)
        streak = st["wb_streak"] or 0
        wb = st["wb_status"] or "active"
        if wrong_now > 0:                      # 只有错过题的题才在错题本里
            if correct and grade >= 4:         # 一般(4) / 熟悉(5)
                streak += 1
                if streak >= 2:
                    wb = "cleared"             # 连续两次掌握 → 移出错题本
            else:
                streak = 0
                wb = "active"                  # 又答错/遗忘 → 回到待重练
        db().execute("""UPDATE srs SET status=?,ease=?,interval=?,repetitions=?,lapses=?,due=?,
                        last_review=?, correct=correct+?, wrong=wrong+?, wb_status=?, wb_streak=?,
                        last_choice=COALESCE(?,last_choice)
                        WHERE qid=? AND user_id=?""",
                     (new["status"], new["ease"], new["interval"], new["repetitions"], new["lapses"],
                      new["due"], now(), correct, 0 if correct else 1, wb, streak, choice, eqid, uid))
        db().execute("""INSERT INTO reviews(qid,user_id,grade,ts,interval_before,interval_after,ease,timeout)
                        VALUES(?,?,?,?,?,?,?,?)""",
                     (eqid, uid, grade, now(), before, new["interval"], new["ease"], 1 if timeout else 0))
        return new

    def api_grade(self, p):
        try:
            qid_int = int(p.get("id")); grade = int(p.get("grade"))
        except Exception:
            return self._send(400, {"error": "id/grade required"})
        uid = need_uid(p.get("token"))
        timeout = 1 if p.get("timeout") else 0
        choice = (p.get("choice") or "").strip()[:4] or None
        eqid = resolve_qid(qid_int)
        if not eqid:
            return self._send(404, {"error": "no such question"})
        st = q_one("SELECT * FROM srs WHERE qid=? AND user_id=?", (eqid, uid))
        if not st:
            return self._send(404, {"error": "no srs row"})
        with _LOCK:
            new = self._apply_grade(eqid, grade, uid, timeout, choice)
            db().commit()
        return self._send(200, {"ok": True, "eqid": eqid, **(new or {})})

    def api_note(self, p):
        eqid = resolve_qid(int(p.get("id", 0)))
        if not eqid:
            return self._send(404, {"error": "no question"})
        uid = need_uid(p.get("token"))
        text = (p.get("text") or "").strip()
        with _LOCK:
            if text:
                db().execute("INSERT OR REPLACE INTO notes(qid,user_id,text,updated) VALUES(?,?,?,?)",
                             (eqid, uid, text, now()))
            else:
                db().execute("DELETE FROM notes WHERE qid=? AND user_id=?", (eqid, uid))
            db().commit()
        return self._send(200, {"ok": True, "text": text})

    def api_fav(self, p):
        eqid = resolve_qid(int(p.get("id", 0)))
        if not eqid:
            return self._send(404, {"error": "no question"})
        uid = need_uid(p.get("token"))
        on = bool(p.get("on"))
        with _LOCK:
            if on:
                db().execute("INSERT OR IGNORE INTO favorites(qid,user_id,added) VALUES(?,?,?)", (eqid, uid, now()))
            else:
                db().execute("DELETE FROM favorites WHERE qid=? AND user_id=?", (eqid, uid))
            db().commit()
        return self._send(200, {"ok": True, "on": on})

    def api_goal(self, p):
        v = max(1, min(500, int(p.get("value", 20) or 20)))
        with _LOCK:
            db().execute("INSERT OR REPLACE INTO settings(key,value) VALUES('daily_goal',?)", (str(v),))
            db().commit()
        return self._send(200, {"ok": True, "goal": v})

    def api_doubt(self, p):
        eqid = resolve_qid(int(p.get("id", 0)))
        if not eqid:
            return self._send(404, {"error": "no question"})
        uid = need_uid(p.get("token"))
        status = (p.get("status") or "").strip()
        note = (p.get("note") or "").strip()
        with _LOCK:
            if status or note:
                db().execute("INSERT OR REPLACE INTO doubt_status(qid,user_id,status,note,updated) VALUES(?,?,?,?,?)",
                             (eqid, uid, status, note, now()))
            else:
                db().execute("DELETE FROM doubt_status WHERE qid=? AND user_id=?", (eqid, uid))
            db().commit()
        return self._send(200, {"ok": True, "status": status})

    def api_exam_submit(self, p):
        items = p.get("items") or []
        title = (p.get("title") or "模拟考试").strip()[:40]
        duration = int(p.get("duration") or 0)
        uid = need_uid(p.get("token"))
        total = len(items); correct = 0
        with _LOCK:
            for it in items:
                eqid = resolve_qid(int(it.get("id", 0)))
                if not eqid:
                    continue
                ok = bool(it.get("correct"))
                if ok:
                    correct += 1
                self._apply_grade(eqid, 4 if ok else 1, uid, 1 if it.get("timeout") else 0)
            score = round(correct / total * 100, 1) if total else 0
            db().execute("""INSERT INTO exam_history(title,total,correct,score,duration,ts,user_id)
                            VALUES(?,?,?,?,?,?,?)""", (title, total, correct, score, duration, now(), uid))
            db().commit()
        return self._send(200, {"ok": True, "total": total, "correct": correct, "score": score})


    # ---------- 账号：注册 / 登录 / 退出 / 密码 ----------
    def _new_uid(self):
        """生成新账号 id（不限制账号数量）。"""
        return "u" + secrets.token_hex(8)

    def _issue(self, uid):
        """签发新 token 并返回账号公开信息。"""
        tok = new_token()
        with _LOCK:
            db().execute("UPDATE users SET token=? WHERE id=?", (tok, uid))
            db().commit()
        r = q_one("SELECT id,name,username,anon FROM users WHERE id=?", (uid,))
        return {"ok": True, "token": tok, "user": {"id": r["id"], "name": r["name"], "username": r["username"], "anon": bool(r["anon"])}}

    def api_auth_register(self, p):
        username = (p.get("username") or "").strip()
        name = (p.get("name") or username).strip()[:12]
        pw = p.get("password") or ""
        if len(username) < 2 or len(username) > 20:
            return self._send(400, {"error": "用户名需 2-20 个字符"})
        if not re.fullmatch(r"[\w一-龥\-]+", username):
            return self._send(400, {"error": "用户名仅支持中文、字母、数字、下划线、连字符"})
        if len(pw) < 6:
            return self._send(400, {"error": "密码至少 6 位"})
        if q_one("SELECT 1 FROM users WHERE username=?", (username,)):
            return self._send(400, {"error": "该用户名已被注册，换一个试试"})
        uid = self._new_uid()
        with _LOCK:
            db().execute("INSERT INTO users(id,name,username,pw_hash,token,created) VALUES(?,?,?,?,?,?)",
                         (uid, name or username, username, pw_hash(pw), "", now()))
            init_user_srs(uid)      # 新账号初始化一套空白进度
            db().commit()
        return self._send(200, self._issue(uid))

    def api_auth_login(self, p):
        username = (p.get("username") or "").strip()
        pw = p.get("password") or ""
        r = q_one("SELECT * FROM users WHERE username=?", (username,))
        if not r:
            return self._send(401, {"error": "用户名或密码错误"})
        if not r["pw_hash"]:
            # 老账号尚未设置密码：提示前端进入「补设密码」流程
            return self._send(200, {"ok": False, "need_password": True, "username": username})
        if not pw_verify(pw, r["pw_hash"]):
            return self._send(401, {"error": "用户名或密码错误"})
        return self._send(200, self._issue(r["id"]))

    def api_auth_logout(self, p):
        tok = (p.get("token") or "").strip()
        if tok:
            with _LOCK:
                u = q_one("SELECT id,anon FROM users WHERE token=?", (tok,))
                if u and u["anon"]:
                    # 游客账号：退出即彻底清除，不留痕迹
                    for t in ("srs", "reviews", "notes", "favorites", "doubt_status", "exam_history"):
                        db().execute(f"DELETE FROM {t} WHERE user_id=?", (u["id"],))
                    db().execute("DELETE FROM users WHERE id=?", (u["id"],))
                else:
                    db().execute("UPDATE users SET token='' WHERE token=?", (tok,))
                db().commit()
        return self._send(200, {"ok": True})

    def api_auth_guest(self, p):
        """游客模式（匿名）：免注册，自动生成仅本浏览器可见的云端身份。
        数据落服务端但与该 token 绑定；换浏览器/清缓存即新身份，彼此隔离。"""
        uid = self._new_uid()
        suffix = ''.join(secrets.choice("0123456789") for _ in range(4))
        username = "游客" + suffix
        while q_one("SELECT 1 FROM users WHERE username=?", (username,)):
            suffix = ''.join(secrets.choice("0123456789") for _ in range(4))
            username = "游客" + suffix
        with _LOCK:
            db().execute("INSERT INTO users(id,name,username,pw_hash,token,created,anon) VALUES(?,?,?,?,?,?,1)",
                         (uid, username, username, None, "", now()))
            init_user_srs(uid)
            db().commit()
        return self._send(200, self._issue(uid))

    def api_auth_upgrade(self, p):
        """游客账号升级为正式账号：设定用户名 + 密码，解除匿名。"""
        uid = need_uid(p.get("token"))
        r = q_one("SELECT * FROM users WHERE id=?", (uid,))
        if not r["anon"]:
            return self._send(400, {"error": "该账号已是正式账号"})
        username = (p.get("username") or "").strip()
        pw = p.get("password") or ""
        if len(username) < 2 or len(username) > 20:
            return self._send(400, {"error": "用户名需 2-20 个字符"})
        if not re.fullmatch(r"[\w一-龥\-]+", username):
            return self._send(400, {"error": "用户名仅支持中文、字母、数字、下划线、连字符"})
        if len(pw) < 6:
            return self._send(400, {"error": "密码至少 6 位"})
        if q_one("SELECT 1 FROM users WHERE username=?", (username,)):
            return self._send(400, {"error": "该用户名已被占用，换一个试试"})
        with _LOCK:
            db().execute("UPDATE users SET username=?, name=?, pw_hash=?, anon=0 WHERE id=?",
                         (username, username, pw_hash(pw), uid))
            db().commit()
        return self._send(200, self._issue(uid))

    def api_auth_password(self, p):
        """修改密码（需已登录）；也用于老账号首次补设密码。"""
        uid = auth_uid(p.get("token"))
        old = p.get("old_password")
        newpw = p.get("password") or ""
        if len(newpw) < 6:
            return self._send(400, {"error": "新密码至少 6 位"})
        if not uid:
            # 未登录 → 走老账号补设：凭用户名 + 该账号当前无密码
            username = (p.get("username") or "").strip()
            r = q_one("SELECT * FROM users WHERE username=?", (username,))
            if not r or r["pw_hash"]:
                return self._send(401, {"error": "请重新登录后再修改密码"})
            uid = r["id"]
        else:
            r = q_one("SELECT * FROM users WHERE id=?", (uid,))
            if r["pw_hash"] and not pw_verify(old or "", r["pw_hash"]):
                return self._send(400, {"error": "原密码不正确"})
        with _LOCK:
            db().execute("UPDATE users SET pw_hash=? WHERE id=?", (pw_hash(newpw), uid))
            db().commit()
        return self._send(200, self._issue(uid))

    def api_auth_claim(self, p):
        """老账号首次补设密码：仅当该账号尚无密码时可用（一次性）。"""
        username = (p.get("username") or "").strip()
        pw = p.get("password") or ""
        legacy_id = (p.get("legacy_id") or "").strip()
        if len(pw) < 6:
            return self._send(400, {"error": "密码至少 6 位"})
        r = q_one("SELECT * FROM users WHERE username=?", (username,))
        if not r:
            return self._send(400, {"error": "账号不存在"})
        if r["pw_hash"]:
            return self._send(400, {"error": "该账号已设置密码，请直接登录"})
        # 必须携带本机此前保存的账号标识，避免陌生人抢注他人老账号
        if legacy_id and legacy_id != r["id"]:
            return self._send(400, {"error": "账号标识不匹配"})
        with _LOCK:
            db().execute("UPDATE users SET pw_hash=? WHERE id=?", (pw_hash(pw), r["id"]))
            db().commit()
        return self._send(200, self._issue(r["id"]))

    def api_users_rename(self, p):
        uid = need_uid(p.get("token"))
        name = (p.get("name") or "").strip()[:12]
        if not name:
            return self._send(400, {"error": "昵称不能为空"})
        with _LOCK:
            db().execute("UPDATE users SET name=? WHERE id=?", (name, uid))
            db().commit()
        return self._send(200, {"ok": True, "name": name})

    def api_users_delete(self, p):
        """删除当前登录账号及其全部学习数据（需再次输入密码确认）。"""
        uid = need_uid(p.get("token"))
        r = q_one("SELECT * FROM users WHERE id=?", (uid,))
        if not pw_verify(p.get("password") or "", r["pw_hash"] or ""):
            return self._send(400, {"error": "密码不正确，删除已取消"})
        with _LOCK:
            for t in ("srs", "reviews", "notes", "favorites", "doubt_status", "exam_history"):
                db().execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
            db().execute("DELETE FROM users WHERE id=?", (uid,))
            db().commit()
        return self._send(200, {"ok": True})


def main():
    if not os.path.exists(DB_PATH):
        print("[ERR] 数据库不存在，请先运行: python parse.py")
        sys.exit(1)
    ensure_tables()
    print(f"[INFO] vault  = {VAULT_ROOT}")
    print(f"[INFO] images = {IMG_ROOT}  exists={os.path.isdir(IMG_ROOT)}")
    print(f"[INFO] 打开浏览器访问:  http://127.0.0.1:{PORT}")
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] 已停止")


if __name__ == "__main__":
    main()
