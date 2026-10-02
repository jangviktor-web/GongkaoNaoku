#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""迁移 v3：为账号系统加上密码与 token（幂等，可重复执行）。

- users 新增 username / pw_hash / token 三列
- 已有账号（如 u1/JV）回填 username=原昵称，pw_hash 留空（首次进入时强制设置密码）
- 不删除任何学习数据（srs / reviews / notes / favorites / exam_history 原样保留）
"""
import sqlite3
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else "kaogong.db"

def has_col(c, table, col):
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    return col in cols

def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    # users 表不存在则创建
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        id TEXT PRIMARY KEY, name TEXT, created TEXT)""")

    for col, ddl in (("username", "TEXT"), ("pw_hash", "TEXT"), ("token", "TEXT")):
        if not has_col(c, "users", col):
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            print(f"+ 新增列 users.{col}")

    # 回填 username：用原昵称（并去掉空格）
    rows = c.execute("SELECT id, name FROM users").fetchall()
    for r in rows:
        cur = c.execute("SELECT username FROM users WHERE id=?", (r["id"],)).fetchone()
        if not cur or not cur["username"]:
            uname = (r["name"] or r["id"]).strip()
            # 保证唯一
            base, i = uname, 1
            while c.execute("SELECT 1 FROM users WHERE username=? AND id<>?", (uname, r["id"])).fetchone():
                i += 1
                uname = f"{base}{i}"
            c.execute("UPDATE users SET username=? WHERE id=?", (uname, r["id"]))
            print(f"  回填 {r['id']} 登录名 = {uname}")

    # 唯一索引（username 允许为空，SQLite 允许多个 NULL）
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    except Exception as e:
        print("  唯一索引跳过：", e)

    c.commit()

    print("\n迁移后账号状态：")
    for r in c.execute("SELECT id, name, username, pw_hash IS NOT NULL AS has_pw FROM users"):
        print(f"  {r['id']}  昵称={r['name']}  登录名={r['username']}  已设密码={'是' if r['has_pw'] else '否（需首次设置）'}")

    n = c.execute("SELECT COUNT(*) FROM srs").fetchone()[0]
    print(f"\n学习进度总行数（未改动）：{n}")
    c.close()
    print("迁移完成 ✓")

if __name__ == "__main__":
    main()
