"""품목 다듬기 — 이름으로 못 가른 소매 길이를 실측 · 태그로 가른다.

이름 분류기(crawl_cafe24 --build-csv)는 「플레인 라운드 피티드 티셔츠」처럼 소매 말이 없는 이름을 티셔츠로 둔다.
사람 품목 확인 60벌(2026-10-05)에서 틀림 10 중 3이 「티셔츠 → 롱슬리브」였고, 사람이 「반팔 긴팔은 소매 길이로 더 확실하게
판정 필요」라고 했다. 실측이 가장 확실하고(소매 말이 있는 이름 4,429벌로 재 보니 반팔은 95%가 36cm 이하, 롱슬리브는 95%가 55.5cm
이상), 다음이 소매 태그(tag_items 가 글 · 실측에서 뽑은 값)다.

  티셔츠 · 반팔 · 롱슬리브 : 실측 ≤38 → 반팔, ≥50 → 롱슬리브, 그 사이(칠부쯤)는 태그가 하나로 말할 때만
  셔츠 · 하프셔츠           : 같은 기준으로 하프셔츠 ↔ 셔츠. 블라우스는 소매와 무관하니 두지 않는다

products_full.csv 의 item_type · subtype 을 고쳐 쓴다. 워크플로에서 size_from_ocr 다음에 돈다(태그 · 실측이 둘 다 최신일 때).
  python scripts/refine_items.py            # 고쳐 쓴다
  python scripts/refine_items.py --dry-run  # 숫자만
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
CSV = DATA / "products_full.csv"
TAGS = DATA / "product_tags_full.json"
SIZES = DATA / "product_sizes.json"

SHORT_CM, LONG_CM = 38.0, 50.0
TEES = {"티셔츠", "반팔", "롱슬리브"}
SHIRTS = {"셔츠", "하프셔츠"}


def sleeve_cm(sizes: dict, url: str) -> float | None:
    v = [x for x in ((sizes.get(url) or {}).get("sizes") or {}).get("소매길이", []) if x is not None]
    return min(v) if v else None


def sleeve_tag(tags: dict, url: str) -> str | None:
    v = ((tags.get(url) or {}).get("tags") or {}).get("sleeve_length") or []
    v = [x for x in v if x in ("반팔", "롱슬리브")]
    return v[0] if len(v) == 1 else None   # 반팔 · 롱슬리브가 같이 적힌 것(코디 문장)은 안 믿는다


def decide(item: str, cm: float | None, tag: str | None) -> str:
    # 이름이 이미 소매를 말한 것(반팔 · 롱슬리브 · 하프셔츠)은 두고, 뭉뚱그린 것(티셔츠 · 셔츠)만 가른다 —
    # 이름과 실측이 어긋난 31벌은 실측표를 잘못 읽은 쪽이 더 많아 보였다(2026-10-05 시험).
    if item == "티셔츠":
        short, long_ = "반팔", "롱슬리브"
    elif item == "셔츠":
        short, long_ = "하프셔츠", "셔츠"
    else:
        return item
    if cm is not None:
        if cm <= SHORT_CM:
            return short
        if cm >= LONG_CM:
            return long_
    if tag == "반팔":
        return short
    if tag == "롱슬리브":
        return long_
    return item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raw = CSV.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))
    fields = list(rows[0].keys())
    tags = json.loads(TAGS.read_text(encoding="utf-8")) if TAGS.exists() else {}
    sizes = json.loads(SIZES.read_text(encoding="utf-8")) if SIZES.exists() else {}
    moved: Counter = Counter()
    by: Counter = Counter()
    for r in rows:
        it = r.get("item_type") or ""
        if it not in TEES | SHIRTS:
            continue
        u = r["source_url"]
        cm, tag = sleeve_cm(sizes, u), sleeve_tag(tags, u)
        new = decide(it, cm, tag)
        if new != it:
            moved[(it, new)] += 1
            by["실측" if cm is not None and (cm <= SHORT_CM or cm >= LONG_CM) else "태그"] += 1
            r["item_type"] = new
            if r.get("subtype") in (it, ""):
                r["subtype"] = new
    print(f"소매 길이로 다듬음 {sum(moved.values()):,}벌 (실측 {by['실측']:,} · 태그 {by['태그']:,})")
    for (a, b), n in moved.most_common():
        print(f"  {a} → {b} {n:,}")
    if args.dry_run or not moved:
        return 0
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    CSV.write_text(out.getvalue(), encoding="utf-8-sig")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
