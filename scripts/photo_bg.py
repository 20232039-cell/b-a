"""대표 사진 가장자리 바탕색 — 상품컷 바탕을 흰색으로 맞춰 보이려고(앱 세션 부탁 · 사람 결정 2026-11-10, 컬렉션 표지 시안 A).

한 배율로 다 밝히면 흰 셔츠 · 흰 티가 날아가므로 사진마다 바탕색을 안다. 재는 법은 앱 캔버스 시안과 같다:
네 모서리 + 위 가운데 + 좌우 가운데 7점(가장자리에서 1px 안) 평균, 7점이 평균에서 채널당 30 넘게 벗어나지 않을 때만
「고른 단색」 — 그 평균을 6자리 hex 로. 그보다 퍼지면(그림자 · 무늬 · 배경 사진) null. 투명 PNG 는 흰 바탕에 얹어 잰다
(그냥 RGB 로 바꾸면 투명이 검정이 된다). 원본 크기 그대로 잰다 — 줄이면 가장자리가 섞인다.
착용컷 여부는 여기서 안 본다 — export_app_data 가 wn=1 이면 bg 를 싣지 않는다.

결과: data/photo_bg/<i>.jsonl — {"u": source_url, "img": 잰 사진 주소, "bg": "efefef" | null, "ok": false(못 받음)} 한 줄씩(덧붙임).
같은 사진을 이미 잰 상품은 건너뛴다(매장이 대표 사진을 바꾸면 다시 잰다).

사용: python scripts/photo_bg.py --shard 0/8 [--limit N] [--hours 5]
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
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTDIR = DATA / "photo_bg"
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"}
SPREAD = 30


def edge_bg(im: Image.Image) -> str | None:
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        base = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(base, im)
    im = im.convert("RGB")
    w, h = im.size
    if w < 8 or h < 8:
        return None
    pts = [(1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2), (w // 2, 1), (1, h // 2), (w - 2, h // 2)]
    px = [im.getpixel(p) for p in pts]
    avg = [sum(c[i] for c in px) / len(px) for i in range(3)]
    if max(abs(c[i] - avg[i]) for c in px for i in range(3)) > SPREAD:
        return None
    return "%02x%02x%02x" % tuple(min(255, round(a)) for a in avg)


def fetch(u: str):
    try:
        return edge_bg(Image.open(io.BytesIO(requests.get(u, headers=UA, timeout=25).content))), True
    except Exception:
        return None, False


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
    t0 = time.time()
    with open(OUTDIR / f"{si}.jsonl", "a", encoding="utf-8") as fh, ThreadPoolExecutor(12) as ex:
        for s in range(0, len(rows), 200):
            if time.time() - t0 > args.hours * 3600:
                print(f"시간 다 됨 — {s}장에서 멈춤", flush=True)
                break
            part = rows[s: s + 200]
            for (u, img), (bg, ok) in zip(part, ex.map(fetch, [img for _, img in part])):
                o = {"u": u, "img": img, "bg": bg}
                if not ok:
                    o["ok"] = False
                fh.write(json.dumps(o, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"  {min(s + 200, len(rows))}/{len(rows)} · {time.time() - t0:.0f}초", flush=True)


if __name__ == "__main__":
    main()
