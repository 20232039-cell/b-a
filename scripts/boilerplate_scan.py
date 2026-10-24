#!/usr/bin/env python3
"""태그가 상품 설명이 아니라 매장 안내문에서 나온 자리를 자동으로 찾는다.

오늘(2026-09-06) 이 무늬로 버그 열둘을 손으로 찾았다 — 「카라/립 제외」가 카라,
「쿠폰 다운받기」가 오리털, 「폴리백」이 폴리에스터, 「washing tip」이 워싱.
전부 같은 모양이다: 한 낱말이 늘 같은 문장 안에서 나온다.

그래서 태그마다 「그 낱말이 나온 자리의 앞뒤 글」을 세어 본다. 상품마다 다른 말로 적혀
있으면 문맥이 흩어지고, 매장이 모든 상품에 같은 안내문을 붙였으면 한 문맥이 몰아친다.
한 문맥이 그 태그의 15% 넘게 차지하면 사람이 열어 볼 것으로 든다.

    python scripts/boilerplate_scan.py            # 전체
    python scripts/boilerplate_scan.py --min 100  # 100개 넘는 태그만

지어내지 않는다 — 무엇도 자동으로 지우지 않고 「여기를 보라」고만 한다.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tag_items as T  # noqa: E402

DATA = T.DATA
CRAWL = DATA / "crawl"
WS = re.compile(r"\s+")


def load_text() -> dict:
    """상품 주소 → 태거가 읽는 글(이름·설명·본문·그림글)."""
    src = {}
    for p in sorted(glob.glob(str(CRAWL / "*.jsonl"))):
        if os.path.basename(p).startswith("_"):
            continue
        for ln in open(p, encoding="utf-8"):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if d.get("source_url"):
                src[d["source_url"]] = ((d.get("description") or "") + " "
                                        + (d.get("detail_text") or ""))
    ocr = {}
    for p in sorted(glob.glob(str(CRAWL / "ocr" / "*.jsonl"))):
        slug = os.path.basename(p)[:-6]
        for ln in open(p, encoding="utf-8"):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            ocr[(d.get("brand_slug") or slug, str(d.get("product_no")))] = d.get("ocr_text") or ""
    return src, ocr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=200, help="이만큼 붙은 태그만 본다")
    ap.add_argument("--share", type=float, default=0.15, help="한 문맥이 이만큼 넘게 차지하면 든다")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    tg = T.Tagger(json.loads((DATA / "vocab_aliases.json").read_text(encoding="utf-8")))
    rows = {r["source_url"]: r for r in csv.DictReader(
        open(DATA / "products_full.csv", encoding="utf-8-sig"))}
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    src, ocr = load_text()

    holders = collections.defaultdict(list)          # (축, 값) → [주소]
    for u, e in tags.items():
        if u not in rows:
            continue
        for ax, vs in (e.get("tags") or {}).items():
            for v in vs:
                holders[(ax, v)].append(u)

    found = []
    for (ax, val), us in holders.items():
        if len(us) < args.min:
            continue
        al = [a.lower() for a in tg.alias_of(ax, val)]
        ctx = collections.Counter()
        for u in us:
            r = rows[u]
            body = ((r["name"] or "") + " " + src.get(u, "") + " "
                    + ocr.get((r["brand_slug"], str(r["product_no"])), "")).lower()
            for a in al:
                i = body.find(a)
                if i >= 0:
                    ctx[WS.sub(" ", body[max(0, i - 26):i + len(a) + 18]).strip()] += 1
                    break
        if not ctx:
            continue
        s, n = ctx.most_common(1)[0]
        share = n / len(us)
        if share >= args.share:
            owner = collections.Counter(rows[u]["brand_slug"] for u in us).most_common(1)[0]
            found.append((n, share, ax, val, len(us), owner, s))

    found.sort(reverse=True)
    print(f"태그 {len(holders)}가지 가운데 {args.min}개 넘는 것을 보고, "
          f"한 문맥이 {args.share*100:.0f}% 넘게 차지하는 것 {len(found)}가지")
    for n, share, ax, val, tot, owner, s in found[:args.top]:
        print(f"\n{ax}/{val} — {tot:,}개 · 한 문맥이 {n:,}개({share*100:.0f}%) · 최다 {owner[0]} {owner[1]:,}")
        print(f"    …{s}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
