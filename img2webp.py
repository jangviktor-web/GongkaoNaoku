#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把原题库 90-图片 批量转成 WebP(q88) 存入 kaogong-webapp/90-图片（不改动原库）。
可重复运行：已转换的会自动跳过。"""
import os, sys, shutil, multiprocessing as mp
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.environ.get("KAOGONG_VAULT") or os.path.abspath(os.path.join(HERE, "..", "kaogongzhentizhengliu-main"))
SRC = os.path.join(SRC, "90-图片")
DST = os.path.join(HERE, "90-图片")
QUALITY = int(os.environ.get("KG_WEBP_Q", "88"))
METHOD = 4  # 4=速度与体积折中

def has_alpha(im):
    if im.mode in ("RGBA", "LA", "PA"):
        return True
    if im.mode == "P" and "transparency" in im.info:
        return True
    return False

def one(rel):
    sp = os.path.join(SRC, rel)
    low = rel.lower()
    try:
        if low.endswith(".gif"):
            dp = os.path.join(DST, rel)
            if os.path.exists(dp):
                return ("skip", rel, os.path.getsize(sp))
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copy2(sp, dp)
            return ("copy", rel, os.path.getsize(sp))
        stem = os.path.splitext(rel)[0]
        dp = os.path.join(DST, stem + ".webp")
        if os.path.exists(dp):
            return ("skip", rel, os.path.getsize(sp))
        os.makedirs(os.path.dirname(dp), exist_ok=True)
        im = Image.open(sp)
        # 动图(多帧)保守处理：只取首帧转 webp
        im = im.convert("RGBA") if has_alpha(im) else im.convert("RGB")
        im.save(dp, "WEBP", quality=QUALITY, method=METHOD)
        return ("ok", rel, os.path.getsize(sp))
    except Exception as e:
        return ("err", rel + " :: " + str(e), 0)

def main():
    if not os.path.isdir(SRC):
        print("[ERR] 源图片目录不存在:", SRC); sys.exit(1)
    rels = []
    for root, _, fs in os.walk(SRC):
        for f in fs:
            rels.append(os.path.relpath(os.path.join(root, f), SRC))
    total = len(rels)
    print(f"[INFO] 源={SRC}\n[INFO] 目标={DST}\n[INFO] 共 {total} 张, 质量 q{QUALITY}, 进程 {mp.cpu_count()-1}", flush=True)
    os.makedirs(DST, exist_ok=True)
    ok = skip = copy = err = 0
    src_bytes = dst_bytes = 0
    done = 0
    with mp.Pool(max(1, mp.cpu_count() - 1)) as pool:
        for status, rel, sz in pool.imap_unordered(one, rels, chunksize=16):
            done += 1
            if status == "ok":
                ok += 1; src_bytes += sz
                dp = os.path.join(DST, os.path.splitext(rel)[0] + ".webp")
                try: dst_bytes += os.path.getsize(dp)
                except OSError: pass
            elif status == "copy":
                copy += 1; src_bytes += sz
                try: dst_bytes += os.path.getsize(os.path.join(DST, rel))
                except OSError: pass
            elif status == "skip":
                skip += 1
            else:
                err += 1
                if err <= 20:
                    print("[ERR]", rel, flush=True)
            if done % 1000 == 0 or done == total:
                print(f"  进度 {done}/{total}  转换{ok} 复制{copy} 跳过{skip} 失败{err}  "
                      f"{src_bytes/1024/1024:.0f}MB→{dst_bytes/1024/1024:.0f}MB", flush=True)
    print(f"[DONE] 转换{ok} 复制(gif){copy} 跳过{skip} 失败{err}", flush=True)
    if src_bytes:
        print(f"[SIZE] 本次处理原图 {src_bytes/1024/1024:.1f}MB → WebP {dst_bytes/1024/1024:.1f}MB "
              f"(省 {(1-dst_bytes/src_bytes)*100:.1f}%)", flush=True)

if __name__ == "__main__":
    main()
