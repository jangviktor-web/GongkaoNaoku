#!/usr/bin/env python3
"""合并分卷题库：kaogong.db.part-00..NN -> kaogong.db（克隆后运行一次即可）"""
import glob, os, shutil, sys
here = os.path.dirname(os.path.abspath(__file__))
parts = sorted(glob.glob(os.path.join(here, "kaogong.db.part-*")))
if not parts:
    sys.exit("未找到分卷文件 kaogong.db.part-*，请确认在仓库目录内运行。")
out = parts[0].split(".part-")[0]
with open(out, "wb") as f:
    for p in parts:
        with open(p, "rb") as g:
            shutil.copyfileobj(g, f, 1024*1024*8)
print(f"已生成 {out}（{os.path.getsize(out)//1048576} MB，来自 {len(parts)} 个分卷）。现在可以运行 python3 server.py")
