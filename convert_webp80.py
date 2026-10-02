#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 90-图片 下全部图片统一重编码为 WebP(质量 80)。
- 已为 .webp 的：解码后按 q80 重写（统一质量、压缩体积）
- .gif（静态）：转 .webp(q80)，删除原 gif，返回 old->new 映射供改库
- 其它格式(png/jpg)：转 .webp(q80)
- 以 __ 开头的文件(占位图)跳过；原子写入(temp+os.replace)；可重复运行。
"""
import os, sys, json, multiprocessing as mp
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, "90-图片")
QUALITY = 80
METHOD = 4
Image.MAX_IMAGE_PIXELS = None

gif_map = {}  # rel_old -> rel_new （供改库）


def has_alpha(im):
    if im.mode in ("RGBA", "LA", "PA"):
        return True
    if im.mode == "P" and "transparency" in im.info:
        return True
    return False


def convert(rel):
    sp = os.path.join(DST, rel)
    if rel.startswith("__"):
        return ("skip", rel, 0, 0)
    low = rel.lower()
    try:
        if low.endswith(".gif"):
            im = Image.open(sp)
            im = im.convert("RGBA") if has_alpha(im) else im.convert("RGB")
            newrel = os.path.splitext(rel)[0] + ".webp"
            dp = os.path.join(DST, newrel)
            im.save(dp, "WEBP", quality=QUALITY, method=METHOD)
            sz_new = os.path.getsize(dp)
            os.remove(sp)
            gif_map[rel] = newrel
            return ("gif2webp", rel, os.path.getsize(sp), sz_new)
        if low.endswith(".webp"):
            im = Image.open(sp)
            im = im.convert("RGBA") if has_alpha(im) else im.convert("RGB")
            dp = sp + ".tmp"
            im.save(dp, "WEBP", quality=QUALITY, method=METHOD)
            sz_new = os.path.getsize(dp)
            os.replace(dp, sp)
            return ("rewebp", rel, os.path.getsize(sp), sz_new)
        # png/jpg/jpeg/bmp
        stem = os.path.splitext(rel)[0]
        dp = os.path.join(DST, stem + ".webp")
        im = Image.open(sp)
        im = im.convert("RGBA") if has_alpha(im) else im.convert("RGB")
        im.save(dp, "WEBP", quality=QUALITY, method=METHOD)
        sz_new = os.path.getsize(dp)
        os.remove(sp)
        return ("oth2webp", rel, os.path.getsize(sp), sz_new)
    except Exception as e:
        return ("err", rel + " :: " + str(e), 0, 0)


def main():
    if not os.path.isdir(DST):
        print("[ERR] 目录不存在:", DST); sys.exit(1)
    rels = []
    for root, _, fs in os.walk(DST):
        for f in fs:
            rels.append(os.path.relpath(os.path.join(root, f), DST))
    total = len(rels)
    print(f"[INFO] 目录={DST}\n[INFO] 共 {total} 个文件, 目标 WebP q{QUALITY}, 进程 {mp.cpu_count()-1}", flush=True)
    agg = {}
    src_b = dst_b = 0
    done = 0
    with mp.Pool(max(1, mp.cpu_count() - 1)) as pool:
        for status, rel, sb, db_ in pool.imap_unordered(convert, rels, chunksize=32):
            done += 1
            agg[status] = agg.get(status, 0) + 1
            src_b += sb; dst_b += db_
            if status == "err" and agg["err"] <= 15:
                print("[ERR]", rel, flush=True)
            if done % 2000 == 0 or done == total:
                print(f"  进度 {done}/{total} {agg}", flush=True)
    print("[DONE]", agg, flush=True)
    print(f"[SIZE] 原 {src_b/1024/1024:.1f}MB → WebP {dst_b/1024/1024:.1f}MB "
          f"(省 {(1-dst_b/src_b)*100:.1f}%)", flush=True)
    # 写出 gif->webp 映射
    if gif_map:
        with open(os.path.join(HERE, "gif_webp_map.json"), "w", encoding="utf-8") as fh:
            json.dump(gif_map, fh, ensure_ascii=False, indent=2)
        print(f"[GIF] 已转 {len(gif_map)} 个 gif->webp，映射写入 gif_webp_map.json", flush=True)


if __name__ == "__main__":
    main()
