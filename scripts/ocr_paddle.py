"""혼용률이 빈 옷의 상세 그림을 PaddleOCR(한글 모델)로 다시 읽는다 — tag_items.blend_of_brand 의 그림 글 출처를 보탠다.

왜 있는가(2026-10-11, 사람 「깃액션으로 가능해?」 → 시험 → 「일단 진행해 봐」): 판매중 옷 가운데 혼용률이 빈 7,849벌은 상세
그림을 tesseract 로 읽었는데 글자가 깨져 해석기가 못 읽는다. 시험(scripts/ocr_mat_pilot.py, data/ocr_mat_pilot.json)에서
till-i-die 40벌 중 31벌을 PaddleOCR + 깨진 글자 바로잡기(product_desc.ocr_fix)로 읽었다. 언어 모델 토큰 없이 Actions 러너에서 돈다.

무엇을 — 판매중 · 옷 · 혼용률 빈 상품(--shard i/N 로 나눔, source_url crc32). 상품마다 그림 --images 장: tesseract 글에
「소재 · fabric · 혼용 …」이 보인 그림 먼저, 다음은 글자가 많은 그림. 긴 그림은 너비 1000 으로 줄여 1200px 씩 잘라 읽고,
같은 높이의 칸을 왼쪽부터 한 줄로 잇는다(「소재」 「COTTON 100」이 다른 칸으로 나온다). 이미 읽은 상품은 건너뛴다.

결과: data/crawl/ocr_paddle/<i>.jsonl — {"source_url", "brand_slug", "product_no", "text", "at"} 한 줄씩(덧붙임).
tag_items 가 그림 글(ocr) 출처 뒤에 ocr_fix 를 거쳐 붙인다.

사용: python scripts/ocr_paddle.py --shard 0/16 [--brands a,b] [--limit N] [--hours 5]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTDIR = DATA / "crawl" / "ocr_paddle"
sys.path.insert(0, str(ROOT / "scripts"))
import tag_items as ti  # noqa: E402

GARMENT = {"Outerwear", "Tops", "Shirts", "Knitwear", "Pants", "Denim", "Skirts", "Dresses"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"}
LABEL = re.compile(r"(?i)소재|fabric|material|composition|혼용|섬유|겉감|cotton|polyester|nylon|wool|rayon|[가-힣]{0,2}\d{2,3}\s*[%％]")


def pick_images(o: dict, n: int) -> list[str]:
    imgs = [i for i in (o.get("images") or []) if i.get("url")]
    key = lambda i: (0 if LABEL.search(i.get("text") or "") else 1, -(i.get("chars") or 0))  # noqa: E731
    ranked = sorted(imgs, key=key)
    if not any((i.get("chars") or 0) > 0 for i in imgs):
        ranked = imgs                    # tesseract 가 아무것도 못 찾았으면 상세 순서대로
    return [i["url"] for i in ranked[:n]]


def read_lines(ocr, im: Image.Image) -> list[str]:
    import numpy as np
    # 조각을 늘 1000×1200 으로 맞춘다(흰 바탕에 붙임) — 크기가 제각각이면 Paddle 이 모양마다 메모리를 새로 잡아 불어나고,
    # 러너가 「shutdown signal」로 꺼졌다(2026-10-11, 12조각 중 8조각이 6~15분 만에). 너무 긴 그림(줄인 뒤 4만 px 넘게)은 앞만 읽는다.
    w, h = im.size
    im = im.resize((1000, max(1, int(h * 1000 / w))))
    w, h = im.size
    lines: list[str] = []
    for top in range(0, min(h, 40000), 1200):
        tile = Image.new("RGB", (1000, 1200), (255, 255, 255))
        tile.paste(im.crop((0, top, w, min(h, top + 1200))), (0, 0))
        res = ocr.ocr(np.array(tile), cls=False) or []
        boxes = []
        for page in res:
            for box in page or []:
                try:
                    ys = [p[1] for p in box[0]]
                    xs = [p[0] for p in box[0]]
                    boxes.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), str(box[1][0])))
                except Exception:
                    pass
        boxes.sort()
        row: list = []
        for b in boxes:
            if row and abs(b[0] - row[-1][0]) > max(8, 0.5 * max(b[1], row[-1][1])):
                lines.append(" ".join(x[3] for x in sorted(row, key=lambda x: x[2])))
                row = []
            row.append(b)
        if row:
            lines.append(" ".join(x[3] for x in sorted(row, key=lambda x: x[2])))
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--brands", default="", help="쉼표로 — 비우면 전부")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--images", type=int, default=4)
    ap.add_argument("--hours", type=float, default=5.0, help="이만큼 지나면 멈추고 읽은 데까지 남긴다")
    args = ap.parse_args()
    si, sn = (int(x) for x in args.shard.split("/"))
    brands = {b.strip() for b in args.brands.split(",") if b.strip()}
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"{si}.jsonl"
    done = set()
    for p in OUTDIR.glob("*.jsonl"):
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                done.add(json.loads(l)["source_url"])

    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    rows = [r for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))
            if r.get("status") == "ON_SALE" and r.get("category") in GARMENT
            and (not brands or r["brand_slug"] in brands)
            and zlib.crc32(r["source_url"].encode()) % sn == si
            and r["source_url"] not in done and not (tags.get(r["source_url"]) or {}).get("mat")]
    ocrs: dict[str, dict] = {}
    targets = []
    for r in sorted(rows, key=lambda r: r["brand_slug"]):
        b = r["brand_slug"]
        if b not in ocrs:
            ocrs = {b: ti.load_latest(ti.CRAWL / "ocr" / f"{b}.jsonl")}   # 매장 하나씩만 들고 있는다(기억 공간)
        urls = pick_images(ocrs[b].get(str(r["product_no"])) or {}, args.images)
        if urls:
            targets.append((r, urls))
    if args.limit:
        targets = targets[: args.limit]
    print(f"조각 {si}/{sn}: 대상 {len(targets)}벌 (이미 읽음 {len(done)})", flush=True)
    if not targets:
        return

    from paddleocr import PaddleOCR
    ocr = PaddleOCR(lang="korean", use_angle_cls=False, show_log=False)
    t0 = time.time()
    with open(out, "a", encoding="utf-8") as fh:
        for k, (r, urls) in enumerate(targets, 1):
            if time.time() - t0 > args.hours * 3600:
                print(f"시간 다 됨 — {k - 1}벌에서 멈춤", flush=True)
                break
            lines: list[str] = []
            got = 0
            for u in urls:
                try:
                    im = Image.open(io.BytesIO(requests.get(u, headers=UA, timeout=25).content)).convert("RGB")
                    got += 1
                    lines += read_lines(ocr, im)
                except Exception:
                    continue
            # 그림을 하나도 못 받았으면 글 없이 「fail」로 적는다 — 안 적으면 조각마다 다시 붙들고 맴돈다(워크플로가 조금씩 끊어 돈다).
            # 다시 읽히려면 그 줄을 지우면 된다.
            fh.write(json.dumps({"source_url": r["source_url"], "brand_slug": r["brand_slug"],
                                 "product_no": str(r["product_no"]), "text": "\n".join(lines), **({} if got else {"fail": True}),
                                 "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
            fh.flush()
            if k % 25 == 0:
                print(f"  {k}/{len(targets)} · {time.time() - t0:.0f}초", flush=True)


if __name__ == "__main__":
    main()
