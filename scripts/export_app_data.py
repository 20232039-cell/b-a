#!/usr/bin/env python3
"""앱이 읽을 모양으로 데이터를 내보낸다 — 얇은 목록 하나 + 브랜드 조각 여럿.

통째로 넣으면 38MB 다(상품 + 태그 + 사이즈, 사진 목록 제외). 지금 앱은 카탈로그를
정적 import 로 읽어(shoppableProducts.ts) 시작할 때 통째로 파싱하므로 그대로는 못 넣는다.
그래서 둘로 가른다.

  products_index.json   목록·검색·필터가 도는 데 필요한 최소 필드만. 37,811벌 9.1MB(gzip 1.6MB).
  brands/<slug>.json    그 매장 상품 전부 + 태그 + 사이즈. 평균 574KB, 제일 큰 곳 1.9MB.
  brands.json           매장 목록(이름·상품 수·대표 사진).

상세 화면은 그 상품의 브랜드 조각만 받으면 된다. 사진은 URL 만 넣는다 — 이미지는 매장
CDN 에서 온다(look_photos.json 33MB 는 앱에 넣지 않는다).

이 모양은 제안이다. 데이터를 받을 구조를 다른 세션에서 짜고 있으므로, 그쪽이 정한 이름과
그릇에 맞춰 바꾼다. 바꿀 자리는 thin() 과 full() 둘뿐이다.

    python scripts/export_app_data.py --out ../layer-web/src/app/data/catalog
    python scripts/export_app_data.py --out /tmp/app --dry     # 크기만 재 본다
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def thin(r: dict, tags: dict) -> dict:
    """목록 한 줄 — 격자에 그리고, 검색·필터·정렬에 쓰는 것까지만."""
    t = (tags.get(r["source_url"]) or {}).get("tags") or {}
    return {
        "id": f'{r["brand_slug"]}-{r["product_no"]}',
        "b": r["brand_slug"],
        "n": r["name"],
        "p": int(r["price"] or 0),
        "im": r.get("image_url") or "",
        "u": r["source_url"],
        "c": r.get("category") or "",
        "g": r.get("gender_target") or "",
        "s": r.get("status") or "",
        "co": (t.get("color") or [None])[0],
        "m": (t.get("material") or [None])[0],
        "se": r.get("season") or "",
    }


def full(r: dict, tags: dict, sizes: dict, crawl: dict) -> dict:
    """상세 한 벌 — 얇은 목록에 없는 것 전부."""
    d = crawl.get(r["source_url"]) or {}
    out = dict(thin(r, tags))
    out.update({
        "tags": (tags.get(r["source_url"]) or {}).get("tags") or {},
        "size": sizes.get(r["source_url"]),
        "gallery": d.get("gallery") or [],
        "desc": d.get("description") or "",
        "options": r.get("options") or "",
        "color_name": r.get("representative_color") or "",
        "price_log": d.get("price_log") or [],
        "stock_log": d.get("stock_log") or [],
        "price_seen_at": d.get("price_seen_at") or "",
    })
    return out


def write(path: Path, obj, dry: bool) -> int:
    b = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    if not dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b)
    return len(b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry", action="store_true", help="쓰지 않고 크기만 잰다")
    args = ap.parse_args()
    out = Path(args.out)

    rows = list(csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig")))
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    crawl = {}
    for p in sorted((DATA / "crawl").glob("*.jsonl")):
        if p.name.startswith("_"):
            continue
        for ln in p.open(encoding="utf-8"):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if d.get("source_url"):
                crawl[d["source_url"]] = d

    idx = [thin(r, tags) for r in rows]
    n = write(out / "products_index.json", idx, args.dry)
    gz = len(gzip.compress(json.dumps(idx, ensure_ascii=False, separators=(",", ":")).encode(), 6))
    print(f"얇은 목록 {len(idx)}벌 · {n/1048576:.1f} MB (gzip {gz/1048576:.1f} MB)")

    by = defaultdict(list)
    for r in rows:
        by[r["brand_slug"]].append(r)
    tot = 0
    big = []
    for slug, items in sorted(by.items()):
        size = write(out / "brands" / f"{slug}.json", [full(r, tags, sizes, crawl) for r in items], args.dry)
        tot += size
        big.append((size, slug, len(items)))
    big.sort(reverse=True)
    print(f"브랜드 조각 {len(by)}개 · 합계 {tot/1048576:.1f} MB · 평균 {tot/len(by)/1024:.0f} KB")
    for s, slug, k in big[:3]:
        print(f"   제일 큰 곳: {slug} {k}벌 {s/1024:.0f} KB")

    brands = [{"slug": s, "n": len(v), "im": next((r.get("image_url") for r in v if r.get("image_url")), "")}
              for s, v in sorted(by.items())]
    b = write(out / "brands.json", brands, args.dry)
    print(f"매장 목록 {len(brands)}곳 · {b/1024:.0f} KB")
    if args.dry:
        print("(--dry 라 아무것도 쓰지 않았다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
