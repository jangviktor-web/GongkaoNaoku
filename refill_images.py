#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 xingcezhenti-main.zip 补齐缺失的题库图片。
- 源: zip 内 xingcezhenti-main/90-图片/** (png/jpg/gif/jfif)
- 目标: kaogong-webapp/90-图片/**.webp  (WebP 质量 80)
- 内存中转码，不展开整个 zip；原子写入；可重复运行(已存在的跳过)。
"""
import os, sys, json, io, zipfile, multiprocessing as mp
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ZIP = os.environ.get("KG_SRC_ZIP", "/tmp/xingcezhenti-main.zip")
LIST = os.environ.get("KG_REFILL_LIST", "/tmp/refill_list.json")
QUALITY = 80
METHOD = 4
Image.MAX_IMAGE_PIXELS = None

_zf = None


def init():
    global _zf
    _zf = zipfile.ZipFile(ZIP)  # 每个 worker 打开自己的句柄


def has_alpha(im):
    if im.mode in ("RGBA", "LA", "PA"):
        return True
    if im.mode == "P" and "transparency" in im.info:
        return True
    return False


def one(item):
    target, entry = item
    dp = os.path.join(HERE, target)
    try:
        if os.path.exists(dp):
            return ("skip", target, 0)
        data = _zf.read(entry)
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGBA") if has_alpha(im) else im.convert("RGB")
        tmp = dp + ".tmp"
        im.save(tmp, "WEBP", quality=QUALITY, method=METHOD)
        os.replace(tmp, dp)
        return ("ok", target, os.path.getsize(dp))
    except Exception as e:
        return ("err", target + " :: " + str(e), 0)


def main():
    if not os.path.exists(ZIP):
        print("[ERR] zip 不存在:", ZIP); sys.exit(1)
    items = json.load(open(LIST, encoding="utf-8"))["match"]
    total = len(items)
    print(f"[INFO] 待补齐 {total} 张  源={ZIP}\n[INFO] 目标 WebP q{QUALITY}  进程 {mp.cpu_count()-1}", flush=True)
    agg = {}
    tot = 0
    done = 0
    with mp.Pool(max(1, mp.cpu_count() - 1), initializer=init) as pool:
        for status, t, sz in pool.imap_unordered(one, items, chunksize=32):
            done += 1
            agg[status] = agg.get(status, 0) + 1
            tot += sz
            if status == "err" and agg["err"] <= 15:
                print("[ERR]", t, flush=True)
            if done % 2000 == 0 or done == total:
                print(f"  进度 {done}/{total} {agg}  已写入 {tot/1024/1024:.1f}MB", flush=True)
    print("[DONE]", agg, flush=True)
    print(f"[SIZE] 新增图片合计 {tot/1024/1024:.1f}MB", flush=True)


if __name__ == "__main__":
    main()
