#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""迁移到 v2：多用户隔离 + 错题本状态 + 限时标记
- users 表（最多 3 个本地账号）
- srs / reviews / notes / favorites / doubt_status / exam_history 增加 user_id
- srs 增加错题本字段 wb_status(active|cleared) / wb_streak(连续自评良好次数)
- reviews 增加 timeout 字段（限时模式超时答错标记）
- 现有数据一律归入默认账号 u1，保证已积累的进度/收藏/笔记不丢失
幂等：可重复执行。
"""
import sqlite3, os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "kaogong.db")
DEFAULT_UID = "u1"


def has_col(c, table, col):
    return any(r[1] == col for r in c.execute(f"PRAGMA table_info({table})"))


def ensure(c):
    """对给定连接执行 v2 迁移（幂等，缺什么补什么）。服务启动时也会调用。"""
    c.row_factory = sqlite3.Row

    # ---------- users ----------
    # username/pw_hash/token 三列历史上只存在于手动演进的旧库、无 DDL，全新部署会崩，此处补全
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id TEXT PRIMARY KEY,
        name TEXT,
        created TEXT,
        username TEXT UNIQUE,
        pw_hash TEXT,
        token TEXT
    );
    """)

    # ---------- 缺表兜底：拆库后 parse.py 只产纯题库，用户表可能整张不存在 ----------
    # 按目标 v2 结构补建空表（IF NOT EXISTS 对 v1/v2 既有表均无副作用），
    # 保证下方"v1→v2 升级"分支只在表真实存在时触发。
    c.executescript("""
    CREATE TABLE IF NOT EXISTS srs(
        qid TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'u1',
        status TEXT DEFAULT 'new', ease REAL DEFAULT 2.5, interval INTEGER DEFAULT 0,
        repetitions INTEGER DEFAULT 0, lapses INTEGER DEFAULT 0, due TEXT, last_review TEXT,
        correct INTEGER DEFAULT 0, wrong INTEGER DEFAULT 0, wb_status TEXT DEFAULT 'active',
        wb_streak INTEGER DEFAULT 0, last_choice TEXT, PRIMARY KEY(qid, user_id));
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT, qid TEXT, user_id TEXT NOT NULL DEFAULT 'u1',
        grade INT, ts TEXT, interval_before INT, interval_after INT, ease REAL,
        timeout INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS notes(
        qid TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'u1', text TEXT, updated TEXT,
        PRIMARY KEY(qid, user_id));
    CREATE TABLE IF NOT EXISTS favorites(
        qid TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'u1', added TEXT,
        PRIMARY KEY(qid, user_id));
    CREATE TABLE IF NOT EXISTS doubt_status(
        qid TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'u1',
        status TEXT, note TEXT, updated TEXT, PRIMARY KEY(qid, user_id));
    CREATE TABLE IF NOT EXISTS exam_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, total INT, correct INT,
        score REAL, duration INT, ts TEXT, user_id TEXT NOT NULL DEFAULT 'u1');
    """)

    # ---------- srs：重建为 (qid,user_id) 复合主键 ----------
    if not has_col(c, "srs", "user_id"):
        print("[migrate] srs → 增加 user_id / wb_status / wb_streak")
        n = c.execute("SELECT COUNT(*) FROM srs").fetchone()[0]
        c.executescript("""
        CREATE TABLE srs_v2(
            qid TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'u1',
            status TEXT DEFAULT 'new',
            ease REAL DEFAULT 2.5,
            interval INTEGER DEFAULT 0,
            repetitions INTEGER DEFAULT 0,
            lapses INTEGER DEFAULT 0,
            due TEXT,
            last_review TEXT,
            correct INTEGER DEFAULT 0,
            wrong INTEGER DEFAULT 0,
            wb_status TEXT DEFAULT 'active',
            wb_streak INTEGER DEFAULT 0,
            PRIMARY KEY(qid, user_id)
        );
        """)
        c.execute(f"""INSERT INTO srs_v2(qid,user_id,status,ease,interval,repetitions,lapses,due,
                        last_review,correct,wrong,wb_status,wb_streak)
                      SELECT qid,'{DEFAULT_UID}',status,ease,interval,repetitions,lapses,due,
                        last_review,correct,wrong,'active',0 FROM srs""")
        c.execute("DROP TABLE srs")
        c.execute("ALTER TABLE srs_v2 RENAME TO srs")
        c.execute("CREATE INDEX IF NOT EXISTS idx_srs_user ON srs(user_id)")
        print(f"          已迁移 {n} 行 → 默认账号 {DEFAULT_UID}")
    else:
        # 已迁移过：确保错题本字段存在
        if not has_col(c, "srs", "wb_status"):
            c.execute("ALTER TABLE srs ADD COLUMN wb_status TEXT DEFAULT 'active'")
        if not has_col(c, "srs", "wb_streak"):
            c.execute("ALTER TABLE srs ADD COLUMN wb_streak INTEGER DEFAULT 0")
    # 最近一次作答选项（导出错题本时展示「你的答案」）
    if not has_col(c, "srs", "last_choice"):
        c.execute("ALTER TABLE srs ADD COLUMN last_choice TEXT")

    # ---------- reviews：加 user_id / timeout ----------
    if not has_col(c, "reviews", "user_id"):
        print("[migrate] reviews → 增加 user_id / timeout")
        c.executescript("""
        CREATE TABLE reviews_v2(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qid TEXT, user_id TEXT NOT NULL DEFAULT 'u1',
            grade INT, ts TEXT,
            interval_before INT, interval_after INT, ease REAL,
            timeout INTEGER DEFAULT 0
        );
        """)
        c.execute(f"""INSERT INTO reviews_v2(id,qid,user_id,grade,ts,interval_before,interval_after,ease,timeout)
                      SELECT id,qid,'{DEFAULT_UID}',grade,ts,interval_before,interval_after,ease,0 FROM reviews""")
        c.execute("DROP TABLE reviews")
        c.execute("ALTER TABLE reviews_v2 RENAME TO reviews")
        c.execute("CREATE INDEX IF NOT EXISTS idx_rev_user ON reviews(user_id)")
    elif not has_col(c, "reviews", "timeout"):
        c.execute("ALTER TABLE reviews ADD COLUMN timeout INTEGER DEFAULT 0")

    # ---------- notes / favorites / doubt_status：重建为 (qid,user_id) ----------
    for t, cols in [
        ("notes", "qid TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'u1', text TEXT, updated TEXT"),
        ("favorites", "qid TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'u1', added TEXT"),
        ("doubt_status", "qid TEXT NOT NULL, user_id TEXT NOT NULL DEFAULT 'u1', status TEXT, note TEXT, updated TEXT"),
    ]:
        if not has_col(c, t, "user_id"):
            print(f"[migrate] {t} → 增加 user_id")
            old = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
            sel = ",".join([x for x in old if x != "user_id"])
            c.execute(f"CREATE TABLE {t}_v2({cols}, PRIMARY KEY(qid,user_id))")
            c.execute(f"INSERT INTO {t}_v2({sel},user_id) SELECT {sel},'{DEFAULT_UID}' FROM {t}")
            c.execute(f"DROP TABLE {t}")
            c.execute(f"ALTER TABLE {t}_v2 RENAME TO {t}")

    # ---------- exam_history：加 user_id ----------
    if not has_col(c, "exam_history", "user_id"):
        print("[migrate] exam_history → 增加 user_id")
        c.execute("ALTER TABLE exam_history ADD COLUMN user_id TEXT NOT NULL DEFAULT 'u1'")
    c.execute("CREATE INDEX IF NOT EXISTS idx_exam_user ON exam_history(user_id)")

    c.commit()
    return True


def main():
    """命令行入口：执行一次完整迁移并打印校验结果。"""
    if not os.path.exists(DB):
        print("[ERR] 数据库不存在"); sys.exit(1)
    c = sqlite3.connect(DB)
    ensure(c)
    print("\n=== 迁移后校验 ===")
    for t in ["users", "srs", "reviews", "notes", "favorites", "doubt_status", "exam_history"]:
        try:
            n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<14} {n} 行")
        except Exception as e:
            print(f"  {t:<14} ERR {e}")
    st = c.execute("SELECT COUNT(*) n FROM srs WHERE status!='new'").fetchone()["n"]
    print(f"  srs 中已有进度(非new)的题: {st}  ← 迁移前积累的复习进度，必须保持")
    c.close()
    print("\n[OK] 迁移完成")


if __name__ == "__main__":
    main()
