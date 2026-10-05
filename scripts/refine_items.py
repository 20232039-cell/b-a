"""품목 다듬기 — 이름으로 못 가른 소매 길이를 실측 · 태그로 가른다.

이름 분류기(crawl_cafe24 --build-csv)는 「플레인 라운드 피티드 티셔츠」처럼 소매 말이 없는 이름을 티셔츠로 둔다.
사람 품목 확인 60벌(2026-10-05)에서 틀림 10 중 3이 「티셔츠 → 롱슬리브」였고, 사람이 「반팔 긴팔은 소매 길이로 더 확실하게
판정 필요」라고 했다. 실측이 가장 확실하고(소매 말이 있는 이름 4,429벌로 재 보니 반팔은 95%가 36cm 이하, 롱슬리브는 95%가 55.5cm
이상), 다음이 소매 태그(tag_items 가 글 · 실측에서 뽑은 값)다.

  티셔츠 · 반팔 · 롱슬리브 : 실측 ≤38 → 반팔, ≥50 → 롱슬리브, 그 사이(칠부쯤)나 실측이 없으면 사진 판정(photo_fill, 확신 ≥0.8),
                           그것도 없으면 태그가 하나로 말할 때만. 사진이 슬리브리스라 하면 티셔츠는 슬리브리스로 보낸다.
  셔츠 · 하프셔츠           : 같은 기준으로 하프셔츠 ↔ 셔츠. 블라우스는 소매와 무관하니 두지 않는다
사진이 글 태그보다 앞인 까닭: 사진 판정은 브랜드 단위 검증에서 97%(확신 ≥0.8)인데, 글 태그의 소매 말은 코디 문장(「반팔 티와 함께」)에서
온 것이 섞인다(2026-10-05).

products_full.csv 의 item_type · subtype 을 고쳐 쓴다. 워크플로에서 size_from_ocr · photo_fill 다음에 돈다(태그 · 실측 · 사진이 다 최신일 때).
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
PHOTO = DATA / "photo_pred.json"     # photo_fill.py 가 만든다(확신 ≥0.8 만 들어 있다)

SHORT_CM, LONG_CM = 38.0, 50.0
TEES = {"티셔츠", "반팔", "롱슬리브"}
SHIRTS = {"셔츠", "하프셔츠"}
# 치마 · 원피스 기장(총장 실측, 가장 작은 사이즈) — 이름에 기장이 적힌 것으로 잰 분포(2026-10-05):
#   스커트 미니 95% ≤48 · 미디 48~82 · 롱 5% ≥71 → ≤46 미니 · ≥78 롱 · 사이 미디
#   원피스 미니 95% ≤90 · 롱 5% ≥98 → ≤90 미니 · ≥105 롱 · 사이 미디
SKIRT_CUT = (46.0, 78.0)
DRESS_CUT = (90.0, 105.0)
LEN_TAG = {"미니": "미니", "미디": "미디", "맥시": "롱", "롱기장": "롱"}


def sleeve_cm(sizes: dict, url: str) -> float | None:
    v = [x for x in ((sizes.get(url) or {}).get("sizes") or {}).get("소매길이", []) if x is not None]
    return min(v) if v else None


def sleeve_tag(tags: dict, url: str) -> str | None:
    v = ((tags.get(url) or {}).get("tags") or {}).get("sleeve_length") or []
    v = [x for x in v if x in ("반팔", "롱슬리브")]
    return v[0] if len(v) == 1 else None   # 반팔 · 롱슬리브가 같이 적힌 것(코디 문장)은 안 믿는다


def photo_sleeve(photo: dict, url: str) -> str | None:
    v = (photo.get(url) or {}).get("sleeve")
    return v[0] if v else None


def total_cm(sizes: dict, url: str) -> float | None:
    v = [x for x in ((sizes.get(url) or {}).get("sizes") or {}).get("총장", []) if x is not None]
    return min(v) if v else None


def length_tag(tags: dict, url: str) -> str | None:
    v = [LEN_TAG[x] for x in (((tags.get(url) or {}).get("tags") or {}).get("length") or []) if x in LEN_TAG]
    return v[0] if len(set(v)) == 1 else None


def decide_length(item: str, cm: float | None, tag: str | None) -> str:
    """뭉뚱그린 스커트 · 원피스를 기장으로 미니 · 미디 · 롱으로 가른다(이름이 이미 말한 것은 두고)."""
    if item == "스커트":
        lo, hi, suf = *SKIRT_CUT, "스커트"
    elif item == "원피스":
        lo, hi, suf = *DRESS_CUT, "원피스"
    else:
        return item
    if cm is not None:
        return ("미니" if cm <= lo else "롱" if cm >= hi else "미디") + suf
    if tag:
        return tag + suf
    return item


def decide(item: str, cm: float | None, tag: str | None, photo: str | None = None) -> tuple[str, str]:
    """(새 품목, 근거) — 근거는 실측 · 사진 · 태그 · 없음."""
    # 이름이 이미 소매를 말한 것(반팔 · 롱슬리브 · 하프셔츠)은 두고, 뭉뚱그린 것(티셔츠 · 셔츠)만 가른다 —
    # 이름과 실측이 어긋난 31벌은 실측표를 잘못 읽은 쪽이 더 많아 보였다(2026-10-05 시험).
    if item == "티셔츠":
        short, long_, none_ = "반팔", "롱슬리브", "슬리브리스"
    elif item == "셔츠":
        short, long_, none_ = "하프셔츠", "셔츠", "셔츠"     # 민소매 셔츠는 그냥 셔츠로 둔다
    else:
        return item, "없음"
    if cm is not None:
        if cm <= SHORT_CM:
            return short, "실측"
        if cm >= LONG_CM:
            return long_, "실측"
    if photo == "반팔":
        return short, "사진"
    if photo == "롱슬리브":
        return long_, "사진"
    if photo == "슬리브리스":
        return none_, "사진"
    if tag == "반팔":
        return short, "태그"
    if tag == "롱슬리브":
        return long_, "태그"
    return item, "없음"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raw = CSV.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(raw)))
    fields = list(rows[0].keys())
    tags = json.loads(TAGS.read_text(encoding="utf-8")) if TAGS.exists() else {}
    sizes = json.loads(SIZES.read_text(encoding="utf-8")) if SIZES.exists() else {}
    photo = json.loads(PHOTO.read_text(encoding="utf-8")) if PHOTO.exists() else {}
    moved: Counter = Counter()
    by: Counter = Counter()
    for r in rows:
        it = r.get("item_type") or ""
        u = r["source_url"]
        if it in ("스커트", "원피스"):
            cm, tag = total_cm(sizes, u), length_tag(tags, u)
            new = decide_length(it, cm, tag)
            if new != it:
                by["실측" if cm is not None else "태그"] += 1
        elif it in TEES | SHIRTS:
            new, why = decide(it, sleeve_cm(sizes, u), sleeve_tag(tags, u), photo_sleeve(photo, u))
            if new != it:
                by[why] += 1
        else:
            continue
        if new != it:
            moved[(it, new)] += 1
            r["item_type"] = new
            if r.get("subtype") in (it, ""):
                r["subtype"] = new
    print(f"소매 · 기장으로 다듬음 {sum(moved.values()):,}벌 (실측 {by['실측']:,} · 사진 {by['사진']:,} · 태그 {by['태그']:,})")
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
