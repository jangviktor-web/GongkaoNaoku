#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库健康快检（离线脚本，不扫描全库、不持锁）

背景：完整的 PRAGMA integrity_check / quick_check 在 1.8GB 的 users.db 上要跑几分钟，
并且 journal_mode=delete 下会持共享锁，期间所有写请求（注册/登录/交卷）都会
database is locked —— 实测直接把游客登录打成 500、把面板拖到 180s 超时。

而本项目跨库原子性依赖回滚日志的 super-journal（不能开 WAL），
所以「全库扫描」注定无法和线上服务共存。

本脚本只做毫秒级、无锁风险的必要检查：
  - 库文件是否可打开、页数/页大小、可回收空间
  - 关键表是否存在、行数
  - 能否在 100ms 内取到写锁（写通路是否畅通）
结果写 kaogong-integrity.json，运营面板只读这个文件。

用法：
    python3 check_integrity.py             # 快检（默认，秒级）
    python3 check_integrity.py --full      # 全库扫描（**必须先停服务**）
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
USERS_DB = os.path.join(HERE, "users.db")
OUT = os.path.join(HERE, "kaogong-integrity.json")
TABLES = ("users", "srs", "reviews", "favorites")


def fast_check():
    """毫秒级检查：能开、能读、能拿写锁。不扫描全表。"""
    notes = []
    t0 = time.time()
    c = sqlite3.connect(USERS_DB, timeout=1)
    try:
        c.execute("PRAGMA busy_timeout=1000")
        pc = c.execute("PRAGMA page_count").fetchone()[0]
        ps = c.execute("PRAGMA page_size").fetchone()[0]
        freelist = c.execute("PRAGMA freelist_count").fetchone()[0]
        counts = {}
        for t in TABLES:
            try:
                counts[t] = c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except Exception:
                counts[t] = None
                notes.append("表 %s 不存在" % t)

        # 写通路探测：能拿到写锁说明并发写不会被长时间阻塞
        write_ms = None
        try:
            tw = time.time()
            c.execute("BEGIN IMMEDIATE")
            c.execute("ROLLBACK")
            write_ms = round((time.time() - tw) * 1000, 1)
        except Exception as e:
            notes.append("写锁获取失败: %s" % str(e)[:60])

        state = "ok"
        if write_ms is None:
            state = "warn"
        elif write_ms > 500:
            notes.append("写锁等待偏久（%.0fms），可能有长事务或备份在跑" % write_ms)
            state = "warn"
        if any(v is None for v in counts.values()):
            state = "warn"

        return {
            "state": state,
            "mode": "fast",
            "at": datetime.now().isoformat(timespec="seconds"),
            "took": round(time.time() - t0, 2),
            "size_mb": round(os.path.getsize(USERS_DB) / 1048576),
            "page_count": pc,
            "page_size": ps,
            "free_mb": round(freelist * ps / 1048576),
            "write_lock_ms": write_ms,
            "counts": counts,
            "detail": "；".join(notes),
        }
    finally:
        c.close()


def full_check():
    """全库扫描。会持共享锁数分钟，线上有服务时**不要跑**。"""
    t0 = time.time()
    c = sqlite3.connect(USERS_DB, timeout=10)
    try:
        c.execute("PRAGMA busy_timeout=10000")
        row = c.execute("PRAGMA quick_check(1)").fetchone()
        state = row[0] if row else "unknown"
    finally:
        c.close()
    return {
        "state": state,
        "mode": "full",
        "at": datetime.now().isoformat(timespec="seconds"),
        "took": round(time.time() - t0, 1),
        "detail": "",
    }


def main():
    full = "--full" in sys.argv
    if full:
        print("[警告] 开始全库扫描，期间写入会被阻塞。确认线上服务已停？(Ctrl-C 中止)")
        time.sleep(3)
        data = full_check()
    else:
        data = fast_check()

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("[%s] %s  耗时 %ss%s" % (data["at"], data["state"], data["took"],
                                   ("  " + data["detail"]) if data.get("detail") else ""))
    return 0 if data["state"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
