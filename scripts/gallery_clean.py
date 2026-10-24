"""갤러리 사진 모양 재기 — 앱 상세 사진 넘기기에서 깨진 사진 · 배너 · 긴 상세 그림을 빼려고(사람 결정 2026-11-11).

왜: 코덱스 갤러리 검수 600벌에서 앱이 보여 주는 갤러리에 문제 사진이 있는 상품이 3.8%(깨짐 · 빈 칸 · 가로로 넓은 배너 ·
이어 붙인 긴 그림 · 안내 배너 · 다른 상품). 그중 깨짐 · 빈 칸 · 넓은 배너 · 긴 그림은 사진 크기와 밝기 퍼짐만 보면 가려진다
(표본 갤러리 3,500장에 대 보니 깨짐 9장 모두 잡고 멀쩡한 사진은 2장만 뺌 — 문턱은 export_app_data.gallery_ok). 다른 상품 · 글 배너는 여기서 못 가린다.

재는 것: 사진마다 가로 · 세로 · 회색조 표준편차(128px 로 줄여서). 못 받으면 HTTP 상태.
결과: data/gallery_clean/<i>.jsonl — {"img": 사진 주소, "w": , "h": , "sd": , "st": HTTP 상태(못 받았을 때만)} 한 줄씩. 잰 주소는 건너뛴다.
판정은 export_app_data.gallery_ok 에 둔다(문턱을 바꿔도 다시 잴 필요 없게).

사용: python scripts/gallery_clean.py --shard 0/8 [--limit N] [--hours 5]
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

import requests
from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTDIR = DATA / "gallery_clean"
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"}


def measure(u: str) -> dict:
    try:
        res = requests.get(u, headers=UA, timeout=25)
        if res.status_code != 200:
            return {"img": u, "st": res.status_code}
        im = Image.open(io.BytesIO(res.content))
        w, h = im.size
        g = im.convert("L")
        g.thumbnail((128, 128))
        return {"img": u, "w": w, "h": h, "sd": round(ImageStat.Stat(g).stddev[0], 1)}
    except Exception:
        return {"img": u, "st": 0}


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
                done.add(json.loads(l)["img"])
    # 판매중만 재다가 품절 상품도 잰다(2026-11-12) — 앱은 품절 옷도 보여 주는데 그 갤러리 11만 장이 안 재져서
    # 이어 붙인 긴 그림이 그대로 나갔다(대표님이 앱에서 발견).
    on_sale = {r["source_url"] for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))}
    imgs = []
    seen = set()
    for p in sorted((DATA / "crawl").glob("*.jsonl")):
        for l in p.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            d = json.loads(l)
            if d.get("source_url") not in on_sale:
                continue
            rej = set(d.get("gallery_excluded") or [])
            for u in d.get("gallery") or []:
                if u in rej or u in seen or u in done or zlib.crc32(u.encode()) % sn != si:
                    continue
                seen.add(u)
                imgs.append(u)
    # 대표 사진 · 첫 모델샷(gallery_worn) · 상세 착용 사진(gallery_look)도 잰다(2026-11-12) — 갤러리 밖에서 앱에 나가는 사진들.
    # 상세 본문에서 온 사진에는 두 컷을 세로로 이어 붙인 그림(세로가 가로의 1.8 ~ 2.6배)이 있어 판독 때의 2.6 문턱만으로는 남는다.
    extra = [r["image_url"] for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig")) if r.get("image_url")]
    for name, key in (("gallery_worn", "mw"), ("gallery_look", "ws")):
        for p in sorted((DATA / name).glob("*.jsonl")):
            for l in p.read_text(encoding="utf-8").splitlines():
                try:
                    v = json.loads(l).get(key)
                except ValueError:
                    continue
                extra += v if isinstance(v, list) else [v] if v else []
    for u in extra:
        if u in seen or u in done or zlib.crc32(u.encode()) % sn != si:
            continue
        seen.add(u)
        imgs.append(u)
    if args.limit:
        imgs = imgs[: args.limit]
    print(f"조각 {si}/{sn}: 대상 {len(imgs)}장 (이미 잼 {len(done)})", flush=True)
    t0 = time.time()
    with open(OUTDIR / f"{si}.jsonl", "a", encoding="utf-8") as fh, ThreadPoolExecutor(12) as ex:
        for s in range(0, len(imgs), 300):
            if time.time() - t0 > args.hours * 3600:
                print(f"시간 다 됨 — {s}장에서 멈춤", flush=True)
                break
            for o in ex.map(measure, imgs[s: s + 300]):
                fh.write(json.dumps(o, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {min(s + 300, len(imgs))}/{len(imgs)} · {time.time() - t0:.0f}초", flush=True)


if __name__ == "__main__":
    main()
