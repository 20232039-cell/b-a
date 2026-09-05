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
# 옷 낱말. 이름에 이게 있으면 잡화 낱말이 섞여 있어도 옷으로 본다 —
# 「부츠컷 데님」·「CAP SLEEVE SWEATER」·「Deck jacket」·「패치드 니트」가
# 잡화로 빠지고 있었다(2026-09-05 감사: 1,011건).
GARMENT = re.compile(
    r"팬츠|바지|슬랙스|티셔츠|셔츠|자켓|재킷|점퍼|점퍼|코트|니트|가디건|후디|후드|맨투맨"
    r"|스웨트|스웨터|원피스|드레스|스커트|치마|블라우스|베스트|조끼|데님|청바지|슬리브"
    r"|블레이저|파카|점프수트|셋업|트랙탑|아노락|플리스|패딩|무스탕|レ"
    r"|pants|trouser|shirt|jacket|coat|knit|cardigan|hoodie|hood\b|sweat|sweater|dress"
    r"|skirt|blouse|vest|tee\b|t-shirt|denim|jeans|blazer|parka|jumpsuit|anorak|fleece"
    r"|puffer|shorts|top\b|tank\b|polo\b",
    re.I,
)

# 잡화의 「머리 낱말」 — 이름 어디에 있든 그 물건의 정체다. 옷 낱말이 섞여 있어도
# (「TIE-DYED VEST BACKPACK」·「shoulder bag _ sashiko denim」) 잡화로 본다.
ACC_HEAD = re.compile(
    r"(\bbag\b|가방|백팩|숄더백|크로스백|토트백|미니백|에코백|클러치|파우치|지갑|월렛|카드케이스"
    r"|볼캡|비니|버킷햇|모자|목걸이|팔찌|귀걸이|키링|키홀더|스카프|머플러|양말|삭스"
    r"|신발|스니커즈|슬리퍼|샌들|로퍼|스크런치|헤어밴드|선글라스|안경|우산|벨트"
    r"|backpack|handbag|tote\s*bag|shoulder\s*bag|cross\s*bag|mini\s*bag|clutch|pouch"
    r"|wallet|beanie|bucket\s*hat|ball\s*cap|necklace|bracelet|earring|keyring|scarf"
    r"|muffler|socks?|sneakers?|slippers?|sandals?|loafer|scrunchie|sunglass|umbrella|\bbelt\b)",
    re.I,
)

# 위 둘로 안 갈리는 것에만 쓰는 넓은 그물. 여기 낱말은 「부분 일치」라서
# ring(SHIRRING) · patch(PATCH SWEATSHIRT) · tie(TIE-DYED) 처럼 옷에 흔한 조각은 뺐다.
NON_APPAREL = re.compile(
    r"\bcap\b(?!\s*sleeve)|\bhat\b|beanie|\bbag\b|backpack|tote\b|pouch|wallet"
    r"|\bbelt\b|glove|necklace|bracelet|earring|key\s*ring|keyring|\bsock|scarf|muffler"
    r"|\bshoes?\b|sneaker|slipper|sandal|\bboots?\b|loafer|\bdeck\b(?!\s*jacket)"
    r"|towel|blanket|sticker|charm\b|scrunchie|eyewear|sunglass|umbrella|lanyard"
    r"|모자|볼캡|비니|버킷햇|가방|백팩|숄더백|크로스백|토트백|미니백|에코백|클러치|파우치"
    r"|지갑|월렛|카드케이스|벨트|장갑|목걸이|팔찌|귀걸이|키링|키홀더|양말|삭스|스카프"
    r"|머플러|신발|스니커|슬리퍼|샌들|부츠(?!컷)|로퍼|데크(?!\s*자켓)|타월|담요|스티커"
    r"|헤어밴드|스크런치|넥타이|선글라스|안경|우산",
    re.I,
)


def is_apparel(row: dict) -> bool:
    """옷인가. 순서가 중요하다 — 이름을 먼저, 카테고리는 마지막에 본다.

    카테고리를 같이 넣고 넓은 그물로 거르던 시절, 「Museum Shirt」가 매장의 SHOES
    카테고리에 얹혀 있다는 이유로 잡화가 됐다. 그래서 판단은 이름으로 한다.
    """
    # 밑줄·마침표는 공백으로 바꾼다. 정규식에서 _ 는 단어 문자라 「Drawstring Bag_Blue」의
    # \bbag\b 가 안 맞았고, 그 가방이 옷으로 세어졌다(2026-09-05).
    name = re.sub(r"[_./|]+", " ", row.get("name") or "")
    if ACC_HEAD.search(name):          # 이름에 「백팩·숄더백」이 있으면 가방이다
        return False
    if GARMENT.search(name):           # 옷 낱말이 있으면 옷이다
        return True
    if NON_APPAREL.search(name):
        return False
    cats = " ".join(row.get("category_names") or [])
    return not NON_APPAREL.search(cats)


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
