#!/usr/bin/env python3
"""매장이 모든 상품에 똑같이 붙인 안내문에서 태그가 나오는 자리를 찾는다.

boilerplate_scan.py 는 「태그 → 문맥이 몰리는가」를 보았다. 이 도구는 반대로 간다:
「매장이 되풀이하는 문장 → 그 문장만으로 어떤 태그가 붙는가」.

되풀이 문장은 그 매장 상품의 상당수에 글자 그대로 같이 들어 있는 문장이다. 옷을
설명하는 말이 아니라 세탁·보관 안내문, 배송 안내, 반품 규정이다. 거기서 태그가
나오면 그 태그는 상품이 아니라 안내문의 것이다 — 마르디의 영문 세탁 안내에 있는
'ban the use of bleach' 가 블리치를, 'wool unraveling' 이 울을 만든 식이다.

    python scripts/careblock_scan.py                 # 전 브랜드
    python scripts/careblock_scan.py --brands mardi-mercredi
    python scripts/careblock_scan.py --min-share 0.5 --min-count 20

지어내지 않는다 — 무엇도 지우지 않고 「여기를 보라」고만 한다.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tag_items as T  # noqa: E402

SPLIT = __import__("re").compile(r"[\n·•*]+|(?<=[.!?])\s+|\s-\s|\s~\s|\s=\s")
SKIP_AXES = {"color"}


def sentences(text: str) -> set[str]:
    out = set()
    for s in SPLIT.split(text or ""):
        s = " ".join(s.split())
        if 12 <= len(s) <= 160:
            out.add(s)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*")
    ap.add_argument("--min-share", type=float, default=0.4, help="브랜드 상품의 이 비율 이상에 나오면 안내문")
    ap.add_argument("--min-count", type=int, default=15, help="적어도 이만큼의 상품에 나와야 한다")
    ap.add_argument("--top", type=int, default=200)
    args = ap.parse_args()

    tagger = T.Tagger(json.loads(T.VOCAB.read_text(encoding="utf-8")))
    rows = list(csv.DictReader(open(T.DATA / "products_full.csv", encoding="utf-8-sig")))
    if args.brands:
        rows = [r for r in rows if r["brand_slug"] in set(args.brands)]
    by_brand = collections.defaultdict(list)
    for r in rows:
        by_brand[r["brand_slug"]].append(r)

    found = []
    for slug, items in sorted(by_brand.items()):
        crawl = T.load_latest(T.CRAWL / f"{slug}.jsonl")
        ocr = T.load_latest(T.OCR / f"{slug}.jsonl")
        brw = {}
        bp = T.BROWSER / f"{slug}.jsonl"
        if bp.exists():
            for l in bp.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    b = json.loads(l)
                    t = (b.get("description") or "").strip()
                    if b.get("source_url") and len(t) > len(brw.get(b["source_url"], "")):
                        brw[b["source_url"]] = t
        seen = collections.Counter()
        n = 0
        for r in items:
            d = crawl.get(str(r["product_no"]), {})
            o = ocr.get(str(r["product_no"]), {})
            body = "\n".join(t for t in (
                d.get("description") or "", d.get("detail_text") or "",
                T.denoise_ocr(o.get("ocr_text") or ""), brw.get(r["source_url"], "")) if t)
            if not body.strip():
                continue
            n += 1
            seen.update(sentences(body))
        if n < args.min_count:
            continue
        for sent, cnt in seen.items():
            if cnt < args.min_count or cnt / n < args.min_share:
                continue
            hits = tagger.tag("", "", sent, "", "ok")
            got = [(ax, v) for ax, vs in hits.items() if ax not in SKIP_AXES for v in (vs or [])]
            # 「단색」은 매칭이 아니라 추론이라 안내문과 상관없다
            got = [(ax, v) for ax, v in got if not (ax == "pattern" and v == "단색")]
            if got:
                found.append((cnt, n, slug, sent, got))

    found.sort(key=lambda x: -x[0])
    print(f"안내문에서 태그가 나오는 자리 {len(found)}군데")
    for cnt, n, slug, sent, got in found[:args.top]:
        print(f"\n{slug} — {cnt}/{n}벌 · {', '.join(f'{a}/{v}' for a, v in got)}")
        print(f"    {sent[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
