#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
考公真题库 -> SQLite 导入器
读取 Obsidian vault 的 10-真题 / 15-材料，把每道真题的结构化标注解析进数据库。
仅用标准库。运行:  python parse.py
"""
import os, re, sys, json, sqlite3, datetime, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ---- 配置：vault 根目录（不含 90-图片 前缀的上一级） ----
VAULT_ROOT = os.environ.get("KAOGONG_VAULT")
if not VAULT_ROOT:
    # 默认: 本项目旁边的 kaogongzhentizhengliu-main
    VAULT_ROOT = os.path.abspath(os.path.join(HERE, "..", "kaogongzhentizhengliu-main"))
DB_PATH = os.path.join(HERE, "kaogong.db")
# 设 KAOGONG_FRESH=1 或传 --fresh 可完全重建（清空进度）；默认增量导入并保留复习进度
FRESH = (os.environ.get("KAOGONG_FRESH") == "1") or ("--fresh" in sys.argv)

Q_DIR = os.path.join(VAULT_ROOT, "10-真题")
M_DIR = os.path.join(VAULT_ROOT, "15-材料")

# 图片路径归一化：把 (../)+90-图片/ -> /90-图片/
IMG_RE = re.compile(r"(?:\.\./)+90-图片/")
# 图片扩展名 -> .webp（本地图库已转 WebP；gif 保持不动）
WEBP_EXT_RE = re.compile(r"(/90-图片/[^\"' )<>]+?)\.(?:png|jpe?g)", re.IGNORECASE)

def norm_html(text):
    if not text:
        return ""
    text = IMG_RE.sub("/90-图片/", text)
    text = WEBP_EXT_RE.sub(r"\1.webp", text)
    return text

FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

def parse_frontmatter(raw):
    m = FRONT_RE.match(raw)
    fm = {}
    body = raw
    if m:
        block = m.group(1)
        body = raw[m.end():]
        for line in block.splitlines():
            line = line.rstrip()
            if not line or line.startswith(" "):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip(); v = v.strip()
                if v.startswith("[") and v.endswith("]"):
                    v = v[1:-1]
                    items = re.findall(r'"([^"]*)"|\'([^\']*)\'|([^,\'"]+)', v)
                    lst = []
                    for t in items:
                        s = (t[0] or t[1] or t[2]).strip()
                        if s:
                            lst.append(s)
                    fm[k] = lst
                else:
                    fm[k] = v.strip('"').strip("'")
    return fm, body

# 小节抽取：以 "## X" 或 "### X" 标题切分
def extract_section(body, title, level):
    """level: '##' 或 '###'。返回标题下、到下一个同级或更高级标题前的内容"""
    if level == "##":
        pat = re.compile(r"^## " + re.escape(title) + r"\s*$", re.M)
        nxt = re.compile(r"^(##|###) ", re.M)
    else:
        pat = re.compile(r"^### " + re.escape(title) + r"\s*$", re.M)
        nxt = re.compile(r"^###? ", re.M)
    m = pat.search(body)
    if not m:
        return None
    start = m.end()
    rest = body[start:]
    nm = nxt.search(rest)
    seg = rest[:nm.start()] if nm else rest
    return seg.strip()

def parse_bold_field(body, label):
    """形如 **问法模型**：xxx  或 **最快解法**：⚡ xxx"""
    pat = re.compile(r"\*\*" + re.escape(label) + r"\*\*\s*[：:]\s*(.+)")
    m = pat.search(body)
    if m:
        return m.group(1).strip()
    return None

OPT_LINE_RE = re.compile(r"^-\s*([A-E])[.、．]\s*(.*)$")

def parse_options(opt_text):
    """返回 [{key,label,html,is_answer}]，label 已去图片；解析 ✅ 标记"""
    opts = []
    if not opt_text:
        return opts
    for line in opt_text.splitlines():
        line = line.rstrip()
        m = OPT_LINE_RE.match(line.strip())
        if not m:
            continue
        key = m.group(1)
        raw = m.group(2).strip()
        is_ans = "✅" in raw
        html = raw.replace("✅", "").replace("　", "").strip()
        plain = re.sub(r"<[^>]+>", "", html)
        plain = re.sub(r"\s+", " ", plain).strip()
        opts.append({
            "key": key,
            "label": plain,
            "html": norm_html(html),
            "is_answer": bool(is_ans),
        })
    return opts

ANS_RE = re.compile(r"正确答案\s*[为是]?\s*([A-E])")
ANS_RE2 = re.compile(r"故选?\s*([A-E])")

def detect_answer(opts, analysis, reasoning):
    for o in opts:
        if o["is_answer"]:
            return o["key"]
    for src in (analysis, reasoning):
        if src:
            m = ANS_RE.search(src) or ANS_RE2.search(src)
            if m:
                return m.group(1)
    return ""

def doubt_block(body):
    m = re.search(r">\s*\[!warning\][^\n]*疑点.*", body, re.S)
    if not m:
        return ""
    seg = body[m.start():]
    # 到下一个 --- 或 结尾
    end = seg.find("\n### 题干")
    if end > -1:
        seg = seg[:end]
    lines = [re.sub(r"^>\s?", "", l).strip() for l in seg.splitlines() if l.startswith(">")]
    txt = " ".join(lines)
    txt = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", txt)
    txt = re.sub(r"\[\[([^\]]+)\]\]", r"\1", txt)
    return txt.strip()[:1500]

def parse_related(body):
    seg = extract_section(body, "相关题", "##")
    if not seg:
        return ""
    links = re.findall(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", seg)
    out = []
    for path, disp in links:
        qid = re.search(r"(\d+)", os.path.basename(path))
        out.append(qid.group(1) if qid else path.split("/")[-1])
    return ",".join(out[:8])

def extract_imgs(text):
    if not text:
        return ""
    refs = re.findall(r"/90-图片/[^\"' )>]+", norm_html(text))
    return ",".join(dict.fromkeys(refs))[:2000]

def build_schema(conn):
    if FRESH:
        cur = conn.cursor()
        for t in ("questions", "materials", "srs", "reviews"):
            cur.execute("DROP TABLE IF EXISTS " + t)
        conn.commit()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS questions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        qid TEXT UNIQUE,
        module TEXT, category TEXT, kadian TEXT,
        region TEXT, year TEXT, paper TEXT,
        ask_model TEXT, reasoning TEXT, fastest TEXT, pitfalls TEXT, mother TEXT,
        stem TEXT, options TEXT, answer TEXT, analysis TEXT, material TEXT,
        material_ref TEXT, doubt TEXT, related TEXT, img_refs TEXT, has_image INT,
        path TEXT
    );
    CREATE TABLE IF NOT EXISTS materials(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mid TEXT UNIQUE, title TEXT, body TEXT, path TEXT
    );
    -- SRS 以题号 qid(文本) 为主键：题库更新/重导入也能保留复习进度
    CREATE TABLE IF NOT EXISTS srs(
        qid TEXT PRIMARY KEY,
        status TEXT DEFAULT 'new',
        ease REAL DEFAULT 2.5,
        interval INTEGER DEFAULT 0,
        repetitions INTEGER DEFAULT 0,
        lapses INTEGER DEFAULT 0,
        due TEXT,
        last_review TEXT,
        correct INTEGER DEFAULT 0,
        wrong INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        qid TEXT, grade INT, ts TEXT,
        interval_before INT, interval_after INT, ease REAL
    );
    """)
    conn.commit()

def main():
    if not os.path.isdir(Q_DIR):
        print("[ERR] 找不到真题目录:", Q_DIR)
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    build_schema(conn)
    cur = conn.cursor()

    today = datetime.date.today().isoformat()
    files = []
    for root, _, names in os.walk(Q_DIR):
        for n in names:
            if n.endswith(".md"):
                files.append(os.path.join(root, n))
    files.sort()
    print("[INFO] 待解析真题文件:", len(files))

    n_ok = n_skip = 0
    t0 = time.time()
    rows = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
        except Exception as e:
            n_skip += 1
            continue
        fm, body = parse_frontmatter(raw)
        if fm.get("类型") != "真题" and "类型" in fm:
            n_skip += 1
            continue
        # 相对路径: 10-真题/<模块>/<大类>/<file>
        rel = os.path.relpath(fp, VAULT_ROOT).replace("\\", "/")
        parts = rel.split("/")
        module = parts[1] if len(parts) > 2 else ""
        category = parts[2] if len(parts) > 3 else ""
        # kadian from frontmatter
        kadian = fm.get("考点", "")
        if isinstance(kadian, list):
            kadian = " ".join(kadian)
        tags = fm.get("tags", [])
        if not category and tags:
            for t in tags:
                if t != "真题" and "/" in t:
                    category = t.split("/")[-1]
                    module = module or t.split("/")[0]
        # 标题：# 后第一行
        hm = re.search(r"^#\s+(.+)$", body, re.M)
        title = hm.group(1).strip() if hm else os.path.basename(fp)[:-3]

        reasoning = extract_section(body, "推理链", "##") or ""
        analysis = extract_section(body, "官方解析", "###") or ""
        stem = extract_section(body, "题干", "###") or ""
        opt_text = extract_section(body, "选项", "###") or ""
        material = extract_section(body, "给定材料", "###") or ""
        opts = parse_options(opt_text)
        answer = detect_answer(opts, analysis, reasoning)

        # 材料链接
        mm = re.search(r"材料：\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", body)
        mref = mm.group(2) if (mm and mm.group(2)) else (os.path.basename(mm.group(1)).split(" ")[0] if mm and mm.group(1) else "")

        imgs = extract_imgs(stem + "\n" + material + "\n" + analysis + "\n" + opt_text)

        def clean_md(seg):
            if not seg:
                return ""
            seg = norm_html(seg)
            seg = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", seg)
            seg = re.sub(r"\[\[([^\]]+)\]\]", r"\1", seg)
            return seg.strip()

        rec = (
            str(fm.get("qid", "") or os.path.basename(fp).split(" ")[0]),
            module, category, kadian or title,
            fm.get("地区", ""), str(fm.get("年份", "")), fm.get("试卷", ""),
            clean_md(parse_bold_field(body, "问法模型") or ""),
            clean_md(reasoning),
            clean_md(parse_bold_field(body, "最快解法") or ""),
            clean_md(extract_section(body, "易错点", "##")),
            clean_md(extract_section(body, "母题抽象", "##")),
            norm_html(stem),
            json.dumps(opts, ensure_ascii=False),
            answer,
            norm_html(analysis),
            norm_html(material),
            mref,
            doubt_block(body),
            parse_related(body),
            imgs,
            1 if imgs else 0,
            rel,
        )
        rows.append(rec)
        n_ok += 1
        if (n_ok % 2000) == 0:
            print(f"  ... {n_ok} parsed, {time.time()-t0:.1f}s")

    cur.executemany("""INSERT OR REPLACE INTO questions
        (qid,module,category,kadian,region,year,paper,ask_model,reasoning,fastest,
         pitfalls,mother,stem,options,answer,analysis,material,material_ref,doubt,related,
         img_refs,has_image,path)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()

    # 调和 SRS：以 qid 文本为键，保留已有复习进度，只为新题补 new 记录，删除已消失题
    cur.execute("DELETE FROM srs WHERE qid NOT IN (SELECT qid FROM questions);")
    cur.execute("""INSERT OR IGNORE INTO srs(qid,status,ease,interval,repetitions,lapses,due,correct,wrong)
        SELECT qid,'new',2.5,0,0,0,?,0,0 FROM questions""", (today,))
    if FRESH:
        cur.execute("DELETE FROM reviews;")
    conn.commit()

    total = cur.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    noans = cur.execute("SELECT COUNT(*) FROM questions WHERE answer=''").fetchone()[0]
    print(f"[OK] 导入完成: {total} 题  (跳过 {n_skip})，无答案 {noans}，用时 {time.time()-t0:.1f}s")
    # 索引
    cur.executescript("""
        CREATE INDEX IF NOT EXISTS ix_mod ON questions(module,category);
        CREATE INDEX IF NOT EXISTS ix_kd ON questions(kadian);
        CREATE INDEX IF NOT EXISTS ix_due ON srs(due,status);
        CREATE INDEX IF NOT EXISTS ix_ans ON srs(qid);
    """)
    conn.commit()
    conn.close()
    print("[FILE] 数据库:", DB_PATH)

if __name__ == "__main__":
    main()
