#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
非破坏性合并脚本：把当前项目库的用户数据/人工复核并入网盘版 kaogong.db。
- 基底：/tmp/kaogong_netdrive.db（网盘版，完整 6 模块）
- 并入：项目库 /workspace/kaogong-webapp/kaogong.db 的
        review_verified(53) / reviews(7) / favorites / notes / doubt_status /
        exam_history / srs 复习进度
- 输出：/tmp/kaogong_merged.db（不覆盖任何现有文件）
验证通过后，再手动替换为 /workspace/kaogong-webapp/kaogong.db
"""
import sqlite3, shutil, os, sys

NET = "/tmp/kaogong_netdrive.db"
PROJ = "/workspace/kaogong-webapp/kaogong.db"
OUT = "/tmp/kaogong_merged.db"

def main():
    if not os.path.exists(NET):
        sys.exit(f"找不到网盘版库: {NET}")
    if not os.path.exists(PROJ):
        sys.exit(f"找不到项目库: {PROJ}")
    if os.path.exists(OUT):
        os.remove(OUT)

    # 1) 复制网盘版为基底
    shutil.copyfile(NET, OUT)
    out = sqlite3.connect(OUT)
    out.execute("PRAGMA foreign_keys=OFF")
    proj = sqlite3.connect(PROJ)

    # 1.5) 去重保险：确保 qid 唯一（同一题只保留一条）
    dup = out.execute("SELECT COUNT(*) FROM (SELECT qid FROM questions GROUP BY qid HAVING COUNT(*)>1)").fetchone()[0]
    if dup:
        out.execute("DELETE FROM questions WHERE id NOT IN (SELECT MIN(id) FROM questions GROUP BY qid)")
        out.execute("DELETE FROM srs WHERE qid NOT IN (SELECT qid FROM questions)")
        out.execute("DELETE FROM appearances WHERE qid NOT IN (SELECT qid FROM questions)")
        print(f"[1.5] 清理重复 qid: {dup} 组（保留每组 id 最小）")
    else:
        print("[1.5] qid 唯一性: OK (0 重复)")

    # 1.6) 内容级重复检测（stem+选项归一化后相同视为重题，仅报告不自动删，供人工确认）
    import re as _re, hashlib as _hl, json as _json
    def _norm(s):
        s = (s or "").replace("\u3000", " ").replace("\xa0", " ")
        s = _re.sub(r"\s+", "", s)
        return _re.sub(r"[，。．、,.\s]", "", s).lower()
    _seen, _dups = {}, []
    for r in out.execute("SELECT id,qid,stem,options FROM questions"):
        try:
            _o = _json.loads(r[3] or "[]")
        except Exception:
            _o = []
        _k = _norm(r[2]) + "||" + _norm("".join(str(x.get("label", "")) for x in _o))
        _h = _hl.md5(_k.encode("utf-8")).hexdigest()
        if _h in _seen:
            _dups.append((_seen[_h], r[1]))
        else:
            _seen[_h] = r[1]
    if _dups:
        print(f"[1.6] 内容级重复: {len(_dups)} 对（清单供确认，未删除）: {_dups[:10]}")
    else:
        print("[1.6] 内容级重复: 无")

    # 2) 建 review_verified 表并并入 53 行（关键：否则 server.py:96 会崩）
    out.execute("""
    CREATE TABLE IF NOT EXISTS review_verified (
        qid TEXT PRIMARY KEY,
        official_answer TEXT,
        verified_answer TEXT,
        status TEXT,
        reasoning TEXT,
        supplement TEXT,
        source_url TEXT,
        source_name TEXT,
        verified_at TEXT
    )""")
    cnt = 0
    for row in proj.execute("SELECT qid,official_answer,verified_answer,status,reasoning,supplement,source_url,source_name,verified_at FROM review_verified"):
        out.execute("INSERT OR REPLACE INTO review_verified VALUES (?,?,?,?,?,?,?,?,?)", row)
        cnt += 1
    print(f"[1] review_verified 并入: {cnt} 行")

    # 3) 并入其他用户表（基本为空，影响小，但保留以防万一）
    for tbl in ["reviews", "favorites", "notes", "doubt_status", "exam_history"]:
        cols = [c[1] for c in proj.execute(f"PRAGMA table_info({tbl})")]
        ph = ",".join("?" * len(cols))
        n = 0
        for row in proj.execute(f"SELECT {','.join(cols)} FROM {tbl}"):
            out.execute(f"INSERT OR IGNORE INTO {tbl} VALUES ({ph})", row)
            n += 1
        print(f"[2] {tbl} 并入: {n} 行")

    # 4) 回写 srs 复习进度（交集 qid，仅非 new 状态）
    # 注意：SELECT 列顺序必须与 UPDATE 占位顺序一致（status..wrong, qid）
    upd = 0
    for row in proj.execute(
        "SELECT status,ease,interval,repetitions,lapses,due,last_review,correct,wrong,qid "
        "FROM srs WHERE status!='new'"
    ):
        out.execute(
            "UPDATE srs SET status=?,ease=?,interval=?,repetitions=?,lapses=?,due=?,"
            "last_review=?,correct=?,wrong=? WHERE qid=?",
            row,
        )
        upd += 1
    print(f"[3] srs 进度回写(非new): {upd} 行")

    out.commit()

    # 5) 校验
    q_n = out.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    rv = out.execute("SELECT COUNT(*) FROM review_verified").fetchone()[0]
    ap = out.execute("SELECT COUNT(*) FROM appearances").fetchone()[0]
    srs_nonnew = dict(out.execute("SELECT status,COUNT(*) FROM srs WHERE status!='new' GROUP BY status"))
    print(f"[校验] questions={q_n}, review_verified={rv}, appearances={ap}, srs非new={srs_nonnew}")
    print(f"[完成] 合并库已生成: {OUT} ({os.path.getsize(OUT)/1024/1024:.1f} MB)")
    print("确认无误后执行:")
    print(f"  cp {OUT} /workspace/kaogong-webapp/kaogong.db")
    out.close(); proj.close()

if __name__ == "__main__":
    main()
