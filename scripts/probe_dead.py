"""열리지 않는 상품 페이지를 찾아 적어 둔다 — 목록에서 뺄 것들.

왜 필요한가: 매장이 상품을 내리거나 회원 전용으로 돌려도 목록 페이지에는 남는다.
그런 상품은 우리가 상세를 못 받으니 사이즈·소재가 영원히 비고, 사람이 링크를 열어도
경고창만 본다(2026-09-05 사람이 네 브랜드에서 짚어 줬다).

  insilence  「회원만 접근권한이 있습니다」
  dunst      「고객님께서는 해당 상품에 접근이 불가능 합니다」
  haleine    「회원만 접근권한이 있습니다」
  matin-kim  404

브랜드마다 규칙을 적지 않는다 — 매장은 바뀐다. 실제로 페이지를 열어 보고 판정한다.

  → data/dead_products.csv   (브랜드, 상품번호, 왜, 링크)

사용: py scripts/probe_dead.py [--all]
      기본은 「사이즈 없는 옷」과 「상세가 통째로 빈 상품」만 본다.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
GARMENTS = {"tops", "outer", "bottoms", "dress", "skirt", "suiting"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# 카페24가 경고창으로 띄우는 말. 「로그인」만 보면 안 된다 — 머리글에도 있다.
BLOCKED = re.compile(r"회원만\s*접근권한|접근이\s*불가능|회원\s*전용\s*상품|"
                     r"등급의?\s*회원만|members\s*only", re.I)
_last: dict[str, float] = {}
_lock = threading.Lock()


def polite(url: str, delay: float = 0.4):
    host = url.split("/")[2] if "//" in url else url
    with _lock:
        wait = delay - (time.monotonic() - _last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.monotonic()
    return requests.get(url, headers={"User-Agent": UA}, timeout=25, allow_redirects=True)


def verdict(url: str) -> str | None:
    """열리지 않으면 까닭을, 멀쩡하면 None."""
    try:
        r = polite(url)
    except requests.RequestException as e:
        return f"받기 실패({type(e).__name__})"
    if r.status_code == 404:
        return "404 — 페이지가 없다"
    if r.status_code >= 500:
        return None                      # 매장 쪽 일시 오류 — 지웠다고 볼 수 없다
    if BLOCKED.search(r.text):
        return "회원 전용 — 상세를 볼 수 없다"
    # 상세로 갔는데 상품이 아닌 곳으로 튕겼다
    if "product_no=" not in r.url and "/product/" not in r.url:
        return "상품 페이지가 아닌 곳으로 넘어감"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="판매중 상품 전부 (기본은 의심스러운 것만)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    rows = {(r["brand_slug"], r["product_no"]): r
            for r in csv.DictReader((DATA / "products_full.csv").open(encoding="utf-8-sig"))}

    cand = []
    for path in sorted(DATA.glob("crawl/*.jsonl")):
        slug = path.stem
        if slug.startswith("_"):
            continue
        latest: dict = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            latest[str(d["product_no"])] = d
        for no, d in latest.items():
            r = rows.get((slug, no))
            if not r or r["status"] != "ON_SALE":
                continue
            empty = (not (d.get("detail_text") or "").strip()
                     and not (d.get("detail_images") or [])
                     and not (d.get("spec") or {}))
            nosize = (r["category_code"] in GARMENTS
                      and not (sizes.get(r["source_url"]) or {}).get("sizes"))
            if args.all or empty or nosize:
                cand.append((slug, no, r["source_url"], (d.get("name") or "")[:60]))

    print(f"열어 볼 상품 {len(cand):,}", file=sys.stderr)
    out, done = [], [0]

    def one(item):
        slug, no, url, name = item
        why = verdict(url)
        done[0] += 1
        if done[0] % 100 == 0:
            print(f"  {done[0]:,}/{len(cand):,}", file=sys.stderr)
        if why:
            out.append({"브랜드": slug, "상품번호": no, "왜": why, "상품명": name, "링크": url})

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(one, cand))

    out.sort(key=lambda x: (x["왜"], x["브랜드"], x["상품명"]))
    p = DATA / "dead_products.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["왜", "브랜드", "상품명", "링크", "상품번호"])
        w.writeheader()
        w.writerows(out)
    import collections
    print(f"\n열리지 않는 상품 {len(out)} → {p}")
    for k, v in collections.Counter(x["왜"] for x in out).most_common():
        b = collections.Counter(x["브랜드"] for x in out if x["왜"] == k)
        print(f"   {k:28} {v:5}  {', '.join(f'{s} {n}' for s, n in b.most_common(6))}")


if __name__ == "__main__":
    main()
