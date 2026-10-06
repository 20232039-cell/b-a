"""옷만 잘라 낸 사진 임베딩 — 사람 PC(GPU)에서 돌리는 판. 「비슷한 옷」 사진 쪽을 더 정확하게 만들 수 있나 재는 실험용.

지금 임베딩(embed_photos.py)은 대표 사진 한 장을 통째로 본다. 그래서 모델이 같이 입은 다른 옷 · 배경까지 섞인다
(2026-10-06: 바지 상품인데 위에 입은 체크 셔츠를 보고 「체크」, 사람 평가 5차 메모 「옆 실루엣 드레이핑이 다른 곳엔 안 보여」).
이 스크립트는 사진마다 옷 영역을 찾아(SegFormer 옷 분할) 세 가지 벡터를 만든다.

    full  사진 통째(지금과 같은 방식, 상세 사진도 같은 기준으로 비교하려고)
    box   그 상품 품목의 옷 영역을 상자로 잘라 흰 바탕 정사각형에 놓은 것
    mask  상자 안에서도 옷이 아닌 픽셀(사람 · 다른 옷 · 배경)을 흰색으로 지운 것

대표 사진에 더해 상세 사진(갤러리)도 최대 3장 본다. 평균내지 않고 장마다 따로 저장한다 — 정면 · 옆면 · 디테일 컷을
나중에 용도별로 고르기 위해서다(코덱스 검토 2026-10-06).

    python scripts/embed_crops.py                    # data/emb/crop_jobs.json 의 일감(평가 옷 약 1,500벌 + 비교용 4,000벌, 사진 8,905장)
    python scripts/embed_crops.py --limit 200        # 시험 삼아 조금만
    python scripts/embed_crops.py --all              # 판매중 옷 전부의 대표 사진(약 9만 장) → data/emb/siglip_crop — 비슷한 옷이 이걸 쓴다
                                                     # 평가(2026-10-06): 사진 항만 바꿔도 사람 답 쌍 정확도 68.7 → 73.0%, 처음 본 옷 62.5 → 69.6%

결과: data/emb/crop_eval/part_NNNN.npz — E_full · E_box · E_mask(float16, 행 순서 = meta) ·
meta(JSON: [{u, k(0=대표 · 1~3=상세), area(옷 영역 비율), person(사람이 찍힌 사진인가), ok(옷 영역을 찾았나)}]).
끝나면:  git add data/emb/crop_eval && git commit -m "옷만 자른 사진 임베딩(평가용)" && git push
조각마다 저장하므로 끊겨도 같은 명령으로 이어서 돈다. 사진은 data/emb/_imgs/crop/ 에 긴 변 640px 로 캐시한다(저장소에는 안 올라감).
매장 예의는 embed_photos.py 와 같다(robots.txt · 한 호스트 동시 1 · 줄마다 0.5초 · 무신사 · 29CM 안 엶).

필요한 것: torch · open_clip_torch · transformers · pillow · numpy · requests (embed_photos.py 와 같은 환경 + transformers)
분할 모델: mattmdjaga/segformer_b2_clothes (ATR 18갈래 — 윗옷 4 · 치마 5 · 바지 6 · 원피스 7, 사람 부위 2 · 11~15)
임베딩 모델: Marqo/marqo-fashionSigLIP (지금 쓰는 것과 같은 모델 — 벡터를 바로 견줄 수 있게)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from embed_photos import CDN_HOSTS, Polite

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EMB = DATA / "emb"
IMG = EMB / "_imgs" / "crop"
OUT = EMB / "crop_eval"
ALL_OUT = EMB / "siglip_crop"
JOBS = EMB / "crop_jobs.json"
SEG_REPO = "mattmdjaga/segformer_b2_clothes"
CLIP_REPO = "hf-hub:Marqo/marqo-fashionSigLIP"

# 상품 갈래(category_code) → 그 상품이 차지하는 분할 갈래. 겉옷은 안에 입은 윗옷과 한 덩어리로 「윗옷」이 된다.
TARGET = {"tops": {4}, "outer": {4}, "bottoms": {6, 5}, "skirt": {5, 6}, "dress": {7}, "suiting": {4, 6}}
CLOTHES = {4, 5, 6, 7}
PERSON = {2, 11, 12, 13, 14, 15}    # 머리카락 · 얼굴 · 다리 · 팔
MIN_AREA = 0.03                      # 옷 영역이 사진의 3% 미만이면 못 찾은 것으로 본다
MARGIN = 0.06


def img_path(url: str) -> Path:
    return IMG / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".jpg")


def fetch(polite: Polite, url: str, lane: int = 0) -> bool:
    p = img_path(url)
    if p.exists():
        return True
    raw = polite.get(url, lane)
    if not raw:
        return False
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((640, 640))
        im.save(p, quality=88)
        return True
    except Exception:
        return False


def square(im: Image.Image) -> Image.Image:
    """흰 바탕 정사각형 가운데에 놓는다 — 모델 전처리의 가운데 자르기가 긴 옷의 위아래를 자르지 않게."""
    w, h = im.size
    s = max(w, h)
    bg = Image.new("RGB", (s, s), (255, 255, 255))
    bg.paste(im, ((s - w) // 2, (s - h) // 2))
    return bg


def crops(im: Image.Image, lab: np.ndarray, cat: str) -> tuple[Image.Image, Image.Image, float, bool, bool]:
    """(box, mask, 옷 영역 비율, 사람이 찍혔나, 찾았나)."""
    n = lab.size
    person = bool(np.isin(lab, list(PERSON)).sum() / n > 0.01)
    m = np.isin(lab, list(TARGET.get(cat, CLOTHES)))
    if m.sum() / n < MIN_AREA:
        m = np.isin(lab, list(CLOTHES))          # 품목 갈래로 못 찾으면 옷 전체로 한 번 더(원피스를 윗옷+치마로 본 경우 등)
    area = float(m.sum() / n)
    if area < MIN_AREA:
        return square(im), square(im), area, person, False
    ys, xs = np.where(m)
    h, w = lab.shape
    dy, dx = int((ys.max() - ys.min()) * MARGIN), int((xs.max() - xs.min()) * MARGIN)
    y0, y1 = max(0, ys.min() - dy), min(h, ys.max() + dy + 1)
    x0, x1 = max(0, xs.min() - dx), min(w, xs.max() + dx + 1)
    arr = np.asarray(im)
    box = Image.fromarray(arr[y0:y1, x0:x1])
    masked = arr.copy()
    masked[~m] = 255
    msk = Image.fromarray(masked[y0:y1, x0:x1])
    return square(box), square(msk), area, person, True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", default=str(JOBS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch", type=int, default=0, help="기본: GPU 8 · CPU 8 — 옷 분할 모델이 배치 32 에서 8GB GPU(RTX 3050)를 넘었다(2026-10-06)")
    ap.add_argument("--threads", type=int, default=64)
    ap.add_argument("--out", default="", help="결과 폴더(시험할 때 저장소 밖으로)")
    ap.add_argument("--all", action="store_true",
                    help="판매중 · 씨앗 브랜드 옷 전부의 대표 사진 → data/emb/siglip_crop (similar_items 가 90% 이상 덮이면 이걸 쓴다)")
    args = ap.parse_args()
    out = Path(args.out or (ALL_OUT if args.all else OUT))

    import open_clip
    import torch
    from transformers import AutoModelForSemanticSegmentation, SegformerImageProcessor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    batch = args.batch or 8
    print(f"장치 {device} · 배치 {batch}" + (f" · {torch.cuda.get_device_name(0)}" if device == "cuda" else ""), flush=True)
    out.mkdir(parents=True, exist_ok=True)
    IMG.mkdir(parents=True, exist_ok=True)

    if args.all:
        from embed_photos import APPAREL, all_jobs
        items = [{"u": j["u"], "c": j["c"], "k": 0, "img": j["img"], "b": j["b"], "t": j["t"]}
                 for j in all_jobs() if j["c"] in APPAREL]
    else:
        jobs = json.load(open(args.jobs, encoding="utf-8"))
        items = [{"u": j["u"], "c": j["c"], "k": k, "img": im} for j in jobs for k, im in enumerate(j["imgs"])]
    items = items[:args.limit] if args.limit else items
    parts = sorted(out.glob("part_*.npz"))
    done = {(m["u"], m.get("k", 0)) for p in parts for m in json.loads(str(np.load(p, allow_pickle=False)["meta"]))}
    todo = [x for x in items if (x["u"], x["k"]) not in done]
    print(f"사진 {len(items)} · 이미 함 {len(done)} · 남음 {len(todo)}", flush=True)
    if not todo:
        return 0

    seg_pr = SegformerImageProcessor.from_pretrained(SEG_REPO)
    seg = AutoModelForSemanticSegmentation.from_pretrained(SEG_REPO).to(device).eval()
    clip, _, pre = open_clip.create_model_and_transforms(CLIP_REPO)
    clip = clip.to(device).eval()

    def embed(ims):
        with torch.no_grad():
            e = clip.encode_image(torch.stack([pre(x) for x in ims]).to(device))
            e = e / e.norm(dim=-1, keepdim=True)
        return e.float().cpu().numpy().astype(np.float16)

    polite = Polite()
    fails = defaultdict(int)
    # 호스트를 번갈아 섞는다 — --all 은 브랜드 순이라 그대로면 조각마다 한 호스트만 일한다(embed_photos 와 같은 처리).
    turn = defaultdict(int)

    def rr(x):
        turn[polite.host(x["img"])] += 1
        return turn[polite.host(x["img"])]

    todo.sort(key=rr)
    t0 = time.time()
    CH = 1500

    def download(chunk):
        by_host = defaultdict(list)
        n = defaultdict(int)
        for x in chunk:
            h = polite.host(x["img"])
            n[h] += 1
            by_host[h, n[h] % 2 if any(c in h for c in CDN_HOSTS) else 0].append(x)

        def fetch_host(item):
            (h, lane), xs = item
            for x in xs:
                if fails[h] >= 5:
                    break
                fails[h] = 0 if fetch(polite, x["img"], lane) else fails[h] + 1

        with ThreadPoolExecutor(min(args.threads, len(by_host))) as ex:
            list(ex.map(fetch_host, by_host.items()))
        return [x for x in chunk if img_path(x["img"]).exists()]

    # 다음 조각을 받는 동안 이번 조각을 계산한다 — 내려받기와 GPU 시간이 더해지지 않고 겹치게.
    chunks = [todo[c0:c0 + CH] for c0 in range(0, len(todo), CH)]
    ahead = ThreadPoolExecutor(1)
    nxt = ahead.submit(download, chunks[0])
    for ci in range(len(chunks)):
        chunk = nxt.result()
        if ci + 1 < len(chunks):
            nxt = ahead.submit(download, chunks[ci + 1])
        F, B, M, metas = [], [], [], []
        for i in range(0, len(chunk), batch):
            ims, keep = [], []
            for x in chunk[i:i + batch]:
                try:
                    ims.append(Image.open(img_path(x["img"])).convert("RGB"))
                    keep.append(x)
                except Exception:
                    pass
            if not ims:
                continue
            with torch.no_grad():
                logits = seg(**seg_pr(images=ims, return_tensors="pt").to(device)).logits
            boxes, masks = [], []
            for im, lg, x in zip(ims, logits, keep):
                lab = torch.nn.functional.interpolate(lg[None], size=im.size[::-1], mode="bilinear",
                                                      align_corners=False).argmax(1)[0].cpu().numpy()
                bx, mk, area, person, ok = crops(im, lab, x["c"])
                boxes.append(bx)
                masks.append(mk)
                metas.append({"u": x["u"], "k": x["k"], "area": round(area, 3), "person": person, "ok": ok})
            if not args.all:
                F.append(embed(ims))
            B.append(embed(boxes))
            M.append(embed(masks))
        if not metas:
            continue
        k = len(parts) + 1
        if args.all:
            # 상자로 자른 것 + 옷 아닌 픽셀을 지운 것의 평균(bm) — 평가에서 가장 나았다. siglip 조각과 같은 꼴(E + meta u·b·c·t)
            bm = np.concatenate(B).astype(np.float32) + np.concatenate(M).astype(np.float32)
            bm /= np.linalg.norm(bm, axis=1, keepdims=True) + 1e-9
            by_u = {x["u"]: x for x in chunk}
            metas = [{"u": m["u"], "b": by_u[m["u"]]["b"], "c": by_u[m["u"]]["c"], "t": by_u[m["u"]]["t"], "ok": m["ok"]} for m in metas]
            np.savez(out / f"part_{k:04d}.npz", E=bm.astype(np.float16), meta=np.array(json.dumps(metas, ensure_ascii=False)))
        else:
            np.savez(out / f"part_{k:04d}.npz", E_full=np.concatenate(F), E_box=np.concatenate(B), E_mask=np.concatenate(M),
                     meta=np.array(json.dumps(metas, ensure_ascii=False)))
        parts.append(out / f"part_{k:04d}.npz")
        done |= {(m["u"], m.get("k", 0)) for m in metas}
        okr = sum(m["ok"] for m in metas) / len(metas)
        print(f"저장 {len(done)} / {len(items)} · 이번 조각 옷 찾음 {okr:.0%} · {time.time() - t0:.0f}s", flush=True)
    rel = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"끝 — {len(done)}장 · {out}\n올리기:  git add {rel} && git commit -m \"옷만 자른 사진 임베딩\" && git push", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
