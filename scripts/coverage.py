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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_cafe24 import HEAD_ACC, HEAD_MISC, classify_category   # 옷·잡화 판정을 크롤러와 한 잣대로

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
    r"|puffer|shorts|top\b|tank\b|polo\b|jumper|점퍼|windbreaker|바람막이|셔츠|봄버|bomber|slacks"
    # 속옷·수영복도 실측 표가 있는 옷이다 — 「ADSB BOXER BRIEFS」·「YY SCRUNCHED BIKINI」가
    # 매장 카테고리 때문에 잡화로 세어지고 있었다(2026-09-05).
    r"|팬티|드로즈|boxer|brief|언더웨어|underwear|비키니|bikini|수영복|swimsuit|swimwear"
    r"|래쉬가드|rashguard|레깅스|legging|캐미솔|camisole|브라렛|bralette",
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
    r"|muffler|socks?|sneakers?|slippers?|sandals?|loafer|scrunchie|sunglass|umbrella|\bbelt\b"
    # 카드홀더·명함집은 지갑이다 — 이름에 옷 낱말도 잡화 낱말도 없어 「other」로 빠져나갔다.
    r"|card\s*holder|카드홀더|카드지갑|business\s*card\s*case|명함|그립톡|grip\s*ring|그립링|charm\b|참\b"
    r"|쇼퍼|shopper|더플백|duffle\s*bag|보스턴백|boston\s*bag)",
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


APPAREL_CODES = {"tops", "outer", "bottoms", "dress", "skirt", "suiting"}
NON_APPAREL_CODES = {"shoes", "bags", "accessories", "headwear", "jewelry", "lifestyle"}


def _last(rx: re.Pattern, s: str) -> int | None:
    """정규식이 마지막으로 걸린 자리. 머리 낱말을 가리는 데 쓴다."""
    pos = None
    for m in rx.finditer(s):
        pos = m.start()
    return pos


def is_apparel(row: dict) -> bool:
    """옷인가. 판단은 크롤러의 classify_category 한 곳에서만 한다.

    예전엔 여기서 이름 정규식을 따로 굴렸다. 그러다 두 잣대가 어긋나서 「트러커 캡」이
    한쪽에선 모자, 다른 쪽에선 옷이 됐다(2026-09-05 검사에서 170건). 잣대는 하나여야 한다.
    「other」는 옷으로 둔다 — 옷을 분모에서 빼면 퍼센트가 저절로 오르고 그 숫자는 거짓이다.
    """
    code = (row.get("category_code") or "").strip().lower() or classify_category(
        row.get("name") or "", row.get("category_names") or [], row.get("description") or "")
    return code not in NON_APPAREL_CODES


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
