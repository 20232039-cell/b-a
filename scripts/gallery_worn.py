"""갤러리 · 상세 착장 사진에서 「이 옷의 첫 모델샷」 한 장 — 컬렉션 표지 · 지면(앱 세션 부탁 · 사람 결정 2026-11-11).

왜: 대표 사진이 상품컷인 옷이 판매중의 56%인데, 그중 약 2/3 는 갤러리 · 상세에 모델샷이 있다(코덱스 표본 200벌,
2026-11-11 — 갤러리 1번째 49% · 2번째 30%). 대표 사진만 보는 wn 으로는 그 옷들을 착용샷 표지로 못 쓴다.

어떻게: 대표 사진이 이미 착용컷인 상품(data/worn_photo 의 분할 넓이가 착용 규칙을 넘음)은 건너뛴다. 나머지는 후보를
갤러리 순서대로(대표와 같은 파일 · 견본 칩 빼고) → look_photos(상세 본문에서 공용물 · 안내물 · 글 박힌 그림을 뺀 것) 순으로
최대 8장 보고, worn_photo 와 같은 옷 분할 모델 · 같은 착용 규칙(export_app_data._worn_seg)으로 첫 착용컷에서 멈춘다.

결과: data/gallery_worn/<i>.jsonl — {"u": source_url, "img": 그때 대표 사진, "mw": 첫 모델샷 주소 | null, "n": 본 장 수,
"v": 2(mw_ok 규칙), "fr": mw 의 분할 넓이}. 편집숍 · 오픈마켓 서버 사진(MARKET_RX)은 후보에서 뺀다.
같은 (상품, 대표 사진)은 건너뛴다. 사용: python scripts/gallery_worn.py --shard 0/20 [--limit N] [--hours 5]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTDIR = DATA / "gallery_worn"
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"}
KEEP = (2, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15)
GARMENT = {"Outerwear", "Tops", "Shirts", "Knitwear", "Pants", "Denim", "Skirts", "Dresses"}
MAXC = 8
# 편집숍 · 오픈마켓 이미지 서버 — 공식몰 상세 본문에 붙여 둔 무신사 · 29CM 등의 사진은 모델샷으로 쓰지 않는다(사람 결정 2026-11-11).
# export_app_data.MARKET_RX 와 같게 유지. 예전에 이런 사진을 mw 로 적은 상품은 「이미 봄」에서 빼 다시 본다.
MARKET_RX = re.compile(r"(?:^|[./])(?:msscdn\.net|musinsa\.com|29cm\.co\.kr|wconcept\.co\.kr|zigzag\.kr|a-bly\.com|kream\.co\.kr"
                       r"|coupangcdn\.com|ssfshop\.com|ssg\.com|lotteon\.com|sivillage\.com|halfclub\.com|brandi\.co\.kr|hiver\.co\.kr"
                       r"|hfashionmall\.com|thehyundai\.com|lotteimall\.com|cjonstyle\.com|elandmall\.co\.kr|fashionplus\.co\.kr)(?:[:/]|$)")


def market_img(u: str) -> bool:
    return bool(MARKET_RX.search(u.split("//", 1)[-1].split("/", 1)[0] + "/"))


def worn_seg(fr: dict, category: str | None) -> bool:
    """export_app_data._worn_seg 와 같은 규칙(2026-10-11 표본 157장 정밀도 100%)."""
    g = lambda *ks: sum(fr.get(str(k), 0) for k in ks)  # noqa: E731
    hf, arm, leg, shoe = g(2, 11), g(14, 15), g(12, 13), g(9, 10)
    if category == "Shoes":
        return hf >= 0.013 or arm >= 0.008 or leg >= 0.06
    if category not in GARMENT and g(4, 5, 6, 7) < 0.03:
        return False
    return (arm + leg >= 0.008 or hf >= 0.013
            or (g(4) >= 0.03 and g(5, 6) >= 0.03 and (hf >= 0.004 or (shoe >= 0.005 and category in GARMENT))))


def mw_ok(fr: dict, category: str | None) -> bool:
    """모델샷 규칙(v2) — worn_seg 에 더해 머리 · 얼굴(2 · 11)이 조금이라도 있거나 팔다리가 2% 넘어야 한다.
    코덱스 검수 593장(2026-11-11, 두 번 판정 98.8% 일치): worn_seg 만으로는 쓸 만한 것 85% — 원단 · 주머니 · 프린트 확대를
    착용컷으로 봤다. 이 규칙으로 89%, 쓸 만한 것의 94% 를 남긴다(머리 없는 사진은 63% 만 쓸 만했다)."""
    g = lambda *ks: sum(fr.get(str(k), 0) for k in ks)  # noqa: E731
    return worn_seg(fr, category) and (g(2, 11) >= 0.005 or g(12, 13, 14, 15) >= 0.02)


def fname(u: str) -> str:
    return u.rsplit("/", 1)[-1].split("?")[0]


def fetch(u: str):
    try:
        im = Image.open(io.BytesIO(requests.get(u, headers=UA, timeout=25).content)).convert("RGB")
        if im.height > im.width * 1.8:      # export_app_data.TALL_MAX 와 같게(2.6 → 1.8, 2026-11-12)      # 이어 붙인 긴 상세 그림 — 모델샷 한 장으로 못 쓴다
            return None
        im.thumbnail((512, 512))
        return im
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--hours", type=float, default=5.0)
    ap.add_argument("--thin", action="store_true", help="갤러리가 얇은 상품의 상세 본문 착용 사진 모으기(아래 thin_main)")
    args = ap.parse_args()
    if args.thin:
        return thin_main(args)
    si, sn = (int(x) for x in args.shard.split("/"))
    OUTDIR.mkdir(parents=True, exist_ok=True)
    done = set()
    for p in OUTDIR.glob("*.jsonl"):
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                o = json.loads(l)
                # v2(mw_ok) 이전 줄 · 편집숍 서버 사진을 mw 로 적은 줄은 다시 본다
                if o.get("v") == 2 and not (o.get("mw") and market_img(o["mw"])):
                    done.add((o["u"], o["img"]))
    seg = {}
    for p in (DATA / "worn_photo").glob("*.jsonl"):
        for l in p.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(l)
            except ValueError:
                continue
            if o.get("fr") is not None:
                seg[o["u"]] = (o["img"], o["fr"])
    look = json.loads((DATA / "look_photos.json").read_text(encoding="utf-8")) if (DATA / "look_photos.json").exists() else {}
    rows = [r for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))
            if r.get("status") == "ON_SALE" and r.get("image_url")
            and zlib.crc32(r["source_url"].encode()) % sn == si and (r["source_url"], r["image_url"]) not in done]
    # 대표가 이미 착용컷이면 볼 것 없음
    todo = []
    for r in rows:
        s = seg.get(r["source_url"])
        if s and s[0] == r["image_url"] and worn_seg(s[1], r.get("category")):
            continue
        todo.append(r)
    if args.limit:
        todo = todo[: args.limit]
    print(f"조각 {si}/{sn}: 대상 {len(todo)}벌 (대표 착용컷이라 건너뜀 {len(rows) - len(todo)} · 이미 봄 {len(done)})", flush=True)
    if not todo:
        return
    crawl = {}
    for slug in sorted({r["brand_slug"] for r in todo}):
        p = DATA / "crawl" / f"{slug}.jsonl"
        if p.exists():
            for l in p.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    d = json.loads(l)
                    crawl[d.get("source_url")] = d

    def cands(r) -> list[str]:
        d = crawl.get(r["source_url"]) or {}
        rej = set(d.get("gallery_excluded") or [])
        main = fname(r["image_url"])
        out = []
        for u in (d.get("gallery") or []) + ((look.get(r["source_url"]) or {}).get("photos") or []):
            if u in rej or u in out or fname(u) == main or market_img(u) or re.search(r"roughside\.co\.kr/web/product/small/", u):
                continue
            out.append(u)
        return out[:MAXC]

    import torch
    from transformers import AutoModelForSemanticSegmentation, SegformerImageProcessor
    torch.set_num_threads(4)
    proc = SegformerImageProcessor.from_pretrained("mattmdjaga/segformer_b2_clothes")
    model = AutoModelForSemanticSegmentation.from_pretrained("mattmdjaga/segformer_b2_clothes").eval()

    def fr_of(im):
        with torch.no_grad():
            lg = model(**proc(images=[im], return_tensors="pt")).logits[0]
        lab = torch.nn.functional.interpolate(lg[None], size=im.size[::-1], mode="bilinear").argmax(1)[0].numpy()
        cnt = np.bincount(lab.ravel(), minlength=18)
        return {str(k): float(cnt[k]) / lab.size for k in KEEP if cnt[k]}

    t0 = time.time()
    with open(OUTDIR / f"{si}.jsonl", "a", encoding="utf-8") as fh, ThreadPoolExecutor(8) as ex:
        for k, r in enumerate(todo):
            if time.time() - t0 > args.hours * 3600:
                print(f"시간 다 됨 — {k}벌에서 멈춤", flush=True)
                break
            cs = cands(r)
            mw, n, mfr = None, 0, None
            # 두 장씩 미리 받아 두고 차례로 본다(첫 모델샷은 대개 1~2번째)
            for b in range(0, len(cs), 2):
                ims = list(ex.map(fetch, cs[b:b + 2]))
                for u, im in zip(cs[b:b + 2], ims):
                    n += 1
                    if im is None:
                        continue
                    fr = fr_of(im)
                    if mw_ok(fr, r.get("category")):
                        mw, mfr = u, {k: round(v, 4) for k, v in fr.items()}
                        break
                if mw:
                    break
            fh.write(json.dumps({"u": r["source_url"], "img": r["image_url"], "mw": mw, "n": n, "v": 2, "fr": mfr}, ensure_ascii=False) + "\n")
            if k % 50 == 49:
                fh.flush()
                print(f"  {k + 1}/{len(todo)} · {time.time() - t0:.0f}초", flush=True)


LOOKDIR = DATA / "gallery_look"
THIN_MAX = 2      # 수집본 갤러리(대표와 같은 파일 · 견본 · 편집숍 사진 뺀 것)가 이 장수 이하인 상품만
LOOK_KEEP = 4     # 상품마다 붙일 상세 착용 사진 수


def thin_main(args) -> None:
    """갤러리가 얇은 상품(0 ~ 2장)의 상세 본문 착용 사진 — 앱 갤러리 보강용(2026-11-12).

    왜: 첫 모델샷(mw)만 덧붙이게 했더니 엘무드는 그대로 1장이었다 — 그 한 장(목록용 작은 착용컷)이 바로 mw 였다.
    판매중 옷의 26%(약 2만 벌 · 112개 매장)가 갤러리 0 ~ 1장이고, 모델 사진은 상세 본문 그림에 있다.
    어떻게: look_photos(상세 본문에서 공용물 · 안내물을 뺀 것)를 앞에서부터 MAXC 장 보고, mw_ok(사람이 입은 사진)인 것을
    LOOK_KEEP 장까지 모은다. 이어 붙인 긴 그림 · 가로 배너 · 작은 그림 · 편집숍 서버 사진은 뺀다. 대표가 착용컷이어도 본다.
    결과: data/gallery_look/<i>.jsonl — {"u", "img": 그때 대표 사진, "ws": [착용 사진 주소…], "n": 본 장 수}.
    export_app_data.gallery_plus_look 이 앱 갤러리가 1장 이하일 때 덧붙인다."""
    si, sn = (int(x) for x in args.shard.split("/"))
    LOOKDIR.mkdir(parents=True, exist_ok=True)
    done = set()
    for p in LOOKDIR.glob("*.jsonl"):
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                o = json.loads(l)
                done.add((o["u"], o["img"]))
    look = json.loads((DATA / "look_photos.json").read_text(encoding="utf-8")) if (DATA / "look_photos.json").exists() else {}
    rows = [r for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))
            if r.get("status") == "ON_SALE" and r.get("image_url") and (look.get(r["source_url"]) or {}).get("photos")
            and zlib.crc32(r["source_url"].encode()) % sn == si and (r["source_url"], r["image_url"]) not in done]
    crawl = {}
    for slug in sorted({r["brand_slug"] for r in rows}):
        p = DATA / "crawl" / f"{slug}.jsonl"
        if p.exists():
            for l in p.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    d = json.loads(l)
                    crawl[d.get("source_url")] = d
    todo = []
    for r in rows:
        d = crawl.get(r["source_url"]) or {}
        rej, main = set(d.get("gallery_excluded") or []), fname(r["image_url"])
        g = [u for u in dict.fromkeys(d.get("gallery") or [])
             if u not in rej and fname(u) != main and not market_img(u) and not re.search(r"roughside\.co\.kr/web/product/small/", u)]
        if len(g) > THIN_MAX:
            continue
        cs = [u for u in dict.fromkeys(look[r["source_url"]]["photos"])
              if u not in g and u not in rej and fname(u) != main and not market_img(u)][:MAXC]
        if cs:
            todo.append((r, cs))
    total = len(todo)
    if args.limit:
        todo = todo[: args.limit]
    print(f"조각 {si}/{sn}: 대상 {len(todo)}벌 (갤러리 얇은 상품 남은 {total} · 이미 봄 {len(done)})", flush=True)
    if not todo:
        return
    import torch
    from transformers import AutoModelForSemanticSegmentation, SegformerImageProcessor
    torch.set_num_threads(4)
    proc = SegformerImageProcessor.from_pretrained("mattmdjaga/segformer_b2_clothes")
    model = AutoModelForSemanticSegmentation.from_pretrained("mattmdjaga/segformer_b2_clothes").eval()

    def fr_of(im):
        im = im.copy()
        im.thumbnail((768, 768))      # 상세 본문 그림은 수천 px 인 것이 많다 — 넓이 비율만 보므로 줄여서 잰다
        with torch.no_grad():
            lg = model(**proc(images=[im], return_tensors="pt")).logits[0]
        lab = torch.nn.functional.interpolate(lg[None], size=im.size[::-1], mode="bilinear").argmax(1)[0].numpy()
        cnt = np.bincount(lab.ravel(), minlength=18)
        return {str(k): float(cnt[k]) / lab.size for k in KEEP if cnt[k]}

    t0 = time.time()
    with open(LOOKDIR / f"{si}.jsonl", "a", encoding="utf-8") as fh, ThreadPoolExecutor(8) as ex:
        for k, (r, cs) in enumerate(todo):
            if time.time() - t0 > args.hours * 3600:
                print(f"시간 다 됨 — {k}벌에서 멈춤", flush=True)
                break
            ws, n = [], 0
            for b in range(0, len(cs), 4):
                for u, im in zip(cs[b:b + 4], ex.map(fetch, cs[b:b + 4])):
                    n += 1
                    if im is None or im.width > im.height * 1.6 or min(im.size) < 300:
                        continue
                    if mw_ok(fr_of(im), r.get("category")):
                        ws.append(u)
                    if len(ws) >= LOOK_KEEP:
                        break
                if len(ws) >= LOOK_KEEP:
                    break
            fh.write(json.dumps({"u": r["source_url"], "img": r["image_url"], "ws": ws, "n": n}, ensure_ascii=False) + "\n")
            if k % 50 == 49:
                fh.flush()
                print(f"  {k + 1}/{len(todo)} · {time.time() - t0:.0f}초", flush=True)


if __name__ == "__main__":
    main()
