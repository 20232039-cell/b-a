#!/usr/bin/env python3
"""수집 커버리지를 한 자리에서 잰다.

왜 따로 두나: 잴 때마다 「잡화를 뺄지」를 즉석에서 정하다 보니 같은 데이터가 76.6%
였다가 90.6% 였다가 했다. 보드 데크·모자·지갑은 사이즈 표가 없는 게 정상인데 그걸
「사이즈 못 모은 옷」으로 세면 남은 일이 실제보다 커 보인다. 기준을 파일 하나로 박는다.

기본 분모는 **판매중 · 잡화 아님**이다. 전체·판매중 분모도 같이 찍어서 어느 기준의
숫자인지 헷갈리지 않게 한다.

    py scripts/coverage.py            # 전체 요약 + 브랜드별 하위 15곳
    py scripts/coverage.py --brands kirsh diafvine
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CRAWL = DATA / "crawl"

# 옷이 아니라 실측 표가 없는 게 정상인 것들. 이름·카테고리 어디에 있어도 잡는다.
NON_APPAREL = re.compile(
    # 영문
    r"cap\b|hat\b|beanie|bag\b|backpack|tote|pouch|wallet|belt\b|glove|ring\b|necklace"
    r"|bracelet|earring|key\s*ring|keyring|sock|scarf|muffler|shoe|sneaker|slipper|sandal"
    r"|boot\b|boots\b|loafer|mule\b|deck\b|board\b|towel|blanket|case\b|sticker|patch"
    r"|charm|hairband|scrunchie|tie\b|eyewear|sunglass|umbrella|keychain|lanyard|strap\b"
    # 한글
    r"|모자|볼캡|비니|버킷햇|가방|백팩|숄더백|크로스백|토트백|미니백|에코백|클러치|파우치"
    r"|지갑|월렛|카드케이스|벨트|장갑|반지|목걸|팔찌|귀걸|키링|키홀더|양말|삭스|스카프"
    r"|머플러|신발|스니커|슬리퍼|샌들|부츠|로퍼|데크|보드|타월|담요|케이스|스티커|패치"
    r"|헤어밴드|스크런치|넥타이|선글라스|안경|우산|파우치|참\b",
    re.I,
)


def is_apparel(row: dict) -> bool:
    blob = " ".join(row.get("category_names") or []) + " " + (row.get("name") or "")
    return not NON_APPAREL.search(blob)


def load(name: str) -> dict:
    p = DATA / name
    return json.load(open(p, encoding="utf-8")) if p.exists() else {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*")
    ap.add_argument("--worst", type=int, default=15)
    args = ap.parse_args()

    sizes = load("product_sizes.json")
    tags = load("product_tags_full.json")

    tot = live = app = 0
    have_sz = have_mat = 0
    per = collections.defaultdict(lambda: [0, 0, 0])  # 의류 · 사이즈 · 소재
    gap_ocr = collections.Counter()   # 상세 이미지가 있어 OCR 로 가능한 것
    gap_browser = collections.Counter()  # 이미지도 없어 브라우저가 필요한 것

    for f in sorted(glob.glob(str(CRAWL / "*.jsonl"))):
        slug = os.path.basename(f)[:-6]
        if slug.startswith("_"):
            continue
        for line in open(f, encoding="utf-8"):
            r = json.loads(line)
            tot += 1
            if r.get("soldout"):
                continue
            live += 1
            if not is_apparel(r):
                continue
            app += 1
            u = r.get("source_url")
            s = u in sizes
            m = bool(((tags.get(u) or {}).get("tags") or {}).get("material"))
            have_sz += s
            have_mat += m
            p = per[slug]
            p[0] += 1
            p[1] += s
            p[2] += m
            if not s:
                (gap_ocr if r.get("detail_images") else gap_browser)[slug] += 1

    print(f"크롤 행 {tot} · 판매중 {live} · 판매중 의류 {app}")
    print(f"  사이즈 {have_sz}/{app} = {have_sz / app * 100:.1f}%")
    print(f"  소재   {have_mat}/{app} = {have_mat / app * 100:.1f}%")
    print(f"\n남은 것: OCR 로 가능 {sum(gap_ocr.values())} · 브라우저 필요 {sum(gap_browser.values())}")

    names = args.brands or [s for s, _ in sorted(per.items(), key=lambda kv: kv[1][1] / max(kv[1][0], 1))]
    print(f"\n{'브랜드':22s} {'의류':>6s} {'사이즈':>12s} {'소재':>12s}")
    for slug in names[: args.worst if not args.brands else len(names)]:
        n, s, m = per[slug]
        if not n:
            continue
        print(f"{slug:22s} {n:6d} {s:5d} {s/n*100:5.1f}% {m:5d} {m/n*100:5.1f}%")


if __name__ == "__main__":
    main()
