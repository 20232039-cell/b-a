"""대표 사진이 사람이 입은 컷(착용컷)인가 — 옷 분할 모델(SegFormer)로 사진마다 사람 부위 · 옷 갈래 넓이를 잰다.

왜 있는가(2026-10-11, 앱 세션 부탁 · 사람 결정): 브랜드 화면 명패 · 브랜드 목록 카드에 착용컷을 먼저 쓴다. 앱이 임시로 얼굴
검출로 브랜드마다 한 장씩 골랐는데 얼굴이 잘린 착용컷(다리 · 팔만 보이는 하의 컷)은 못 잡는다. 분할 모델은 머리카락 ·
얼굴 · 팔 · 다리를 따로 칠하므로 얼굴이 없어도 잡는다.

무엇을 — 판매중 상품의 대표 사진(products_full.image_url) 전부, --shard i/N(source_url crc32). 사진을 긴 변 512 로 줄여
mattmdjaga/segformer_b2_clothes(ATR 18갈래)로 칠하고, 갈래마다 넓이 비율을 적는다. 판정 규칙은 export_app_data.worn 에 둔다 —
문턱을 바꿔도 다시 돌릴 필요가 없게(표본 157장 사람 눈 대조, 2026-10-11). 같은 사진을 이미 잰 상품은 건너뛴다.

결과: data/worn_photo/<i>.jsonl — {"u": source_url, "img": 잰 사진 주소, "fr": {갈래 번호: 비율}} 한 줄씩(덧붙임).

사용: python scripts/worn_photo.py --shard 0/16 [--limit N] [--hours 5]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTDIR = DATA / "worn_photo"
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"}
KEEP = (2, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15)   # 머리카락 · 윗옷 · 치마 · 바지 · 원피스 · 신발 · 얼굴 · 다리 · 팔


def fetch(u: str) -> Image.Image | None:
    try:
        im = Image.open(io.BytesIO(requests.get(u, headers=UA, timeout=25).content)).convert("RGB")
        im.thumbnail((512, 512))
        return im
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--hours", type=float, default=5.0)
    args = ap.parse_args()
    si, sn = (int(x) for x in args.shard.split("/"))
    OUTDIR.mkdir(parents=True, exist_ok=True)
    done = set()
    for p in OUTDIR.glob("*.jsonl"):
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                o = json.loads(l)
                done.add((o["u"], o["img"]))
    rows = [(r["source_url"], r["image_url"]) for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))
            if r.get("status") == "ON_SALE" and r.get("image_url")
            and zlib.crc32(r["source_url"].encode()) % sn == si and (r["source_url"], r["image_url"]) not in done]
    if args.limit:
        rows = rows[: args.limit]
    print(f"조각 {si}/{sn}: 대상 {len(rows)}장 (이미 잼 {len(done)})", flush=True)
    if not rows:
        return

    import torch
    from transformers import AutoModelForSemanticSegmentation, SegformerImageProcessor
    torch.set_num_threads(4)
    proc = SegformerImageProcessor.from_pretrained("mattmdjaga/segformer_b2_clothes")
    model = AutoModelForSemanticSegmentation.from_pretrained("mattmdjaga/segformer_b2_clothes").eval()
    t0 = time.time()
    with open(OUTDIR / f"{si}.jsonl", "a", encoding="utf-8") as fh, ThreadPoolExecutor(16) as ex:
        for s in range(0, len(rows), 64):
            if time.time() - t0 > args.hours * 3600:
                print(f"시간 다 됨 — {s}장에서 멈춤", flush=True)
                break
            part = rows[s: s + 64]
            ims = list(ex.map(fetch, [img for _, img in part]))
            ok = [(x, im) for x, im in zip(part, ims) if im is not None]
            for (u, img), im in zip(part, ims):
                if im is None:           # 못 받은 사진은 fr 없이 적는다 — 안 적으면 조각마다 다시 붙들고 맴돈다(다시 재려면 그 줄을 지운다)
                    fh.write(json.dumps({"u": u, "img": img, "fr": None}, ensure_ascii=False) + "\n")
            for b in range(0, len(ok), 16):
                chunk = ok[b: b + 16]
                with torch.no_grad():
                    lgs = model(**proc(images=[im for _, im in chunk], return_tensors="pt")).logits
                for ((u, img), im), lg in zip(chunk, lgs):
                    lab = torch.nn.functional.interpolate(lg[None], size=im.size[::-1], mode="bilinear").argmax(1)[0].numpy()
                    cnt = np.bincount(lab.ravel(), minlength=18)
                    fr = {str(k): round(float(cnt[k]) / lab.size, 4) for k in KEEP if cnt[k]}
                    fh.write(json.dumps({"u": u, "img": img, "fr": fr}, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {min(s + 64, len(rows))}/{len(rows)} · {time.time() - t0:.0f}초", flush=True)


if __name__ == "__main__":
    main()
