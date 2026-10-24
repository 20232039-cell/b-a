"""대표 사진 크기 점검 — 짧은 변 300px 미만(색상 칩 · 스와치)이면 갤러리에서 300px 넘는 첫 사진으로 바꾼다.

결과: data/rep_image_fix.json {source_url: 새 사진 주소}. crawl_cafe24 가 CSV 를 만들 때 쓴다.
"""
from __future__ import annotations

import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image

DATA = Path(__file__).resolve().parent.parent / "data"
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2)", "Range": "bytes=0-65535"}
MIN = 300


def size(u: str):
    try:
        r = requests.get(u, headers=UA, timeout=20)
        return Image.open(io.BytesIO(r.content)).size
    except Exception:
        return None


def main() -> None:
    rows = [r for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))
            if r.get("status") == "ON_SALE" and r.get("image_url")]
    with ThreadPoolExecutor(48) as ex:
        sz = list(ex.map(size, [r["image_url"] for r in rows]))
    small = [r for r, s in zip(rows, sz) if s and min(s) < MIN]
    print(f"대표 사진 {len(rows)} · 못 읽음 {sum(1 for s in sz if not s)} · {MIN}px 미만 {len(small)}", flush=True)
    gal: dict[str, list[str]] = {}
    need = {r["brand_slug"] for r in small}
    for b in need:
        p = DATA / "crawl" / f"{b}.jsonl"
        for l in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
            if l.strip():
                d = json.loads(l)
                gal[d.get("source_url", "")] = d.get("gallery") or []

    def fix(r):
        for u in gal.get(r["source_url"], [])[:6]:
            if u == r["image_url"]:
                continue
            s = size(u)
            if s and min(s) >= MIN:
                return r["source_url"], u
        return r["source_url"], None

    with ThreadPoolExecutor(32) as ex:
        res = dict(ex.map(fix, small))
    out = {u: v for u, v in res.items() if v}
    (DATA / "rep_image_fix.json").write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    by = {}
    for r in small:
        by.setdefault(r["brand_slug"], [0, 0])
        by[r["brand_slug"]][0] += 1
        by[r["brand_slug"]][1] += bool(out.get(r["source_url"]))
    print("바꿈", len(out), "· 못 바꿈", len(small) - len(out))
    for b, (n, k) in sorted(by.items(), key=lambda x: -x[1][0])[:30]:
        print(f"  {b}: 작음 {n} · 바꿈 {k}")


if __name__ == "__main__":
    main()
