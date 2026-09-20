# -*- coding: utf-8 -*-
"""사람에게 보여 줄 **상품 설명** 한 덩이 — 찌꺼기를 걷고, 없으면 읽어 둔 그림 글로 메운다.

매장의 `description` 칸은 상품 설명이 아니다. 상세 페이지를 통째로 긁은 것이라 후기·문의·
배송 안내·매장 푸터가 그대로 들어 있다. 판매중 의류 91,566벌을 전수로 재 보니:

    후기·문의 찌꺼기가 섞인 것             13,126벌 (14.3%)
    그 찌꺼기 덕에 「설명이 있다」로 세어진 것   4,564벌  ← 씻으면 옷 이야기가 안 남는다

    drawfit 959  「19세 미만의 미성년자는 출입을 금합니다. 만족 [1] 네이**** 2025-09-12
                 조회 2 추천 0 … 문의드립니다. 정의**** 22.10.19 조회 5 추천 0」

같은 상품의 상세 **그림**에는 멀쩡한 설명이 있고, 우리는 그걸 이미 읽어 뒀다:

    FABRIC  POLYESTER 63% RAYON 33% POLYURETHANE 4%
    ㆍ깊고 선명한 컬러감과 매끄럽고 고급스러운 텍스처
    ㆍ탄탄한 2*2 RIB 작업으로 잦은 착용에도 늘어남 없이 형태 유지

읽어만 두고 상품 줄로 안 옮기고 있었다 — 설명이 빈 29,083벌 가운데 **22,214벌(76%)** 이
`data/crawl/ocr/<slug>.jsonl` 안에 옷 이야기를 갖고 있다. 그림을 다시 읽을 일이 아니라
옮겨 놓을 일이다(OCR 한 판은 3~6시간이 든다).

## 어떻게 거르나

막을 낱말을 쫓지 않는다 — 안내문은 매장마다 다른 말로 쓰여 끝이 없다(2026-09-18 에 같은
길로 한 번 졌다). **옷 이야기를 하는 조각만 받는다.** 잣대는 detail_from_ocr 이 이미
쓰는 것을 그대로 가져다 쓴다 — 베껴 두면 한쪽이 낡아 어긋난다.

그 위에 사람이 「싹 지우라」고 한 갈래를 먼저 버린다(2026-09-20): 배송·반품·교환·환불,
세탁·취급, 후기·문의. 취급을 버리는 것은 앞선 결정과도 같다(「취급은 다 지워도 되잖아」).

## 조각내기가 이 일의 전부다 — 한 번 크게 틀렸다

첫판은 문장 끝(「…다」·「…요」)으로만 잘랐다. 그런데 매장 설명은 문장으로 안 쓴다 —
불릿이다. 그러면 설명 전체가 조각 **하나**가 되고, 그 안에 배송 한 줄만 섞여도 통째로
날아간다. 얻음 40벌·잃음 40벌을 눈으로 열어 보고서야 드러났다:

    grove 5908      「- 부드럽고 가벼운 하프슬리브 가디건 - 코튼 100% 소재와 적당한 크롭
                     기장의 핏으로 편안한 착용감 …」          ← 멀쩡한 설명인데 잃었다
    art-if-acts 1662「· 세미 와이드핏 · 자연스럽게 떨어지는 울팬츠 · 사이드 어드저스터 …」
    beslow 3042     「- 여유 있는 오버사이즈 핏 - 클래식한 짜임 디테일 …」

그래서 불릿(`-`·`·`·`*`)과 쉼표까지 자르고, **길다고 버리지 않는다**(잘라서 본다).
"""
from __future__ import annotations
import re

from detail_from_ocr import _HARD, _SOFT, _FIBER_PCT, _HANGUL, materials

# ─── 조각내기 ───
# HTML 에서 긁은 글은 줄바꿈이 없다 — 한 줄에 수천 자가 붙어 온다.
# 1차: 문장 끝·불릿·세로선. 한국어 문장은 「다/요/죠/함/음」으로 끝난다.
_SPLIT = re.compile(r"\n+|(?<=[다요죠함음])\s+|(?<=[.!?])\s+|"
                    r"\s*[·ㆍ•▪◦]\s*|\s+[-–—*]\s+|\s*\|\s*|\s{3,}")
# 2차: 그래도 긴 조각은 쉼표·두 칸으로 더 자른다. 버리지는 않는다.
_SPLIT2 = re.compile(r"\s*,\s*|\s{2,}|\s*/\s*(?=[가-힣A-Z])")

# ─── 버릴 갈래 ───
# 사람이 이름을 대어 버리라 한 셋(배송·세탁·후기/문의)과, 상품과 무관한 매장 안내.
_JUNK = re.compile(
    # 지나간 판매 안내 — 옷 이야기가 아니다. 99%IS- 38벌의 설명이 통째로
    # 「프리오더 마감: 2021/8/23」 한 줄이었다(앱 쪽 지적 2026-09-20).
    # **날짜가 붙은 꼴만** 막는다 — 「마감」을 통째로 막으면 「소매/밑단 시보리 마감」처럼
    # 옷을 말하는 줄이 날아간다(전수로 재니 그 꼴이 12건 있었다).
    r"(?:프리\s?오더|pre-?order)\s*(?:마감|종료|기간)\s*[:：]?\s*\d{2,4}\s*[/.\-]\s*\d{1,2}|"
    # 후기·문의 — 「만족 [1] 네이**** 2025-09-12 조회 2 추천 0」
    r"조회\s?\d+\s*추천\s?\d+|추천\s?\d+\s*$|\*{3,}|이전\s?페이지|다음\s?페이지|"
    r"문의드립니다|문의합니다|문의하기|상품\s?문의|사용\s?후기|후기\s?쓰기|후기\s?모두|"
    r"리뷰\s?작성|게시물이\s?없습니다|글읽기\s?권한|게시글\s?신고|신고\s?사유|신고해주신|"
    r"욕설|비방|개인정보\s?유출|광고\s?/?\s?홍보|답변\s?드립니다|"
    r"\bQ\s?&\s?A\b|\breview\b|\bwrite\b|view\s?all|모두보기|"
    # 배송·반품·교환·환불·결제
    r"배송|반품|교환|환불|택배|출고|영업일|주문\s?(취소|보류|확인)|결제|입금|무통장|카드사|"
    r"적립금|쿠폰|사은품|무이자|고액결제|수령\s?후|"
    r"\bshipping\b|\bdelivery\b|\breturn\b|\bexchange\b|\brefund\b|"
    # 세탁·취급 — 「기계 건조 및 온풍 건조 등을 금합니다」까지 잡는다
    r"세탁|드라이\s?클?리?닝|표백|다림질|건조|탈수|드라이어|취급|이염|다리미|찬물|단독\s?손|"
    r"부주의|변질|훼손|보상이?\s?불가|\bNAME\b|\bPRICE\b|"
    r"\bdry\s?clean|\bhand\s?wash|\bmachine\s?wash|\bbleach\b|\btumble\s?dry\b|"
    r"\bdo\s?not\s?\w+|\biron\b|\bwashing\s?method\b|\bhandling\b|"
    # 사이즈 재는 법·구매 안내 — 옷 이야기가 아니라 표 읽는 법이다
    r"기준입니다|측정\s?기준|측정\s?방법|뒷목부터|오차가|오차\s?범위|단면\s?기준|"
    r"구매해\s?주시|구매하시는|참고\s?하?시?어|참고해\s?주|"
    r"size\s?guide|size\s?chart|size\s?info|모델\s?(정보|착용|사이즈)|model\s?(info|size|is)|"
    # 「Daria is 177cm wearing FREE size」 — 모델 이름이 앞에 와서 model 로는 안 잡힌다
    r"\b[A-Z][a-z]+\s+is\s+\d{2,3}\s?cm|\bwearing\s+(?:a\s+)?(?:size\s+)?[A-Z0-9]|"
    r"\b\d{2,3}\s?cm\s*/\s*\d{2,3}\s?kg|착용\s?사이즈|"
    # 매장 안내·법적 고지·행사·푸터
    r"성인\s?인증|미성년자|고객센터|사업자|상호명|통신판매|개인정보|저작권|상표권|"
    r"무단\s?(전재|복제|도용)|제조원|제조국|제조연월|품질보증|"
    r"\bcompany\b|\bceo\b|\btel\b|mail-?order|permit\s?number|copyright|privacy|"
    r"agreement|about\s?us|shop\s?guide|follow\s?us|instagram|youtube|our\s?store|"
    r"신상품|재입고|품절|세일|할인|이벤트|회원\s?가입|소식|받아보세요|구독|팔로우|카카오|"
    r"모니터|해상도|실제\s?색상|컬러가\s?다르게|"
    r"관련\s?상품|related\s?items|추천\s?상품|함께\s?본|more\s?in\s?this",
    re.I)

# 옆 상품 목록이 값을 달고 들어온다 — 「… / Navy ₩286,000 ₩200,200」, 「189,000 won」
_PRICE = re.compile(r"(?:KRW|₩|won|원)\s?[\d,]{4,}|[\d,]{4,}\s?(?:원|KRW|₩|won)", re.I)
# 사이즈 표가 글자로 흘러든 조각 — 「S : 허리 37.5 / 밑위 34 / 허벅지 34 …」
_NUMISH = re.compile(r"[\d.]+")
# 「PRODUCT GUIDE」 눈금자 — 이 옷의 성질이 아니라 자의 눈금 이름이다
# (「비침 없음 조금있음 있음 안감 없음 반만있음 있음 신축성 없음 조금있음 있음」).
# tag_items.SCALE_BAR 가 핏 눈금을 잡는 것과 같은 병이다.
_SCALE = re.compile(r"(없음|있음|보통|조금있음|반만있음)")

# 영문으로만 설명하는 매장이 있다(annex-archive 「SLEEVELESS LAYERED T SHIRTS —
# DIFFERENT FABRIC MIXED — LASER CUT HOLE DETAIL」). _HARD 는 한국어 어휘다.
_HARD_EN = re.compile(
    r"\b(fit|fabric|cotton|wool|linen|denim|leather|silk|cashmere|nylon|polyester|rayon|"
    r"detail|pocket|zipper|button|collar|sleeve|sleeveless|hem|lining|silhouette|"
    r"oversized?|cropped?|relaxed|slim|wide|tapered|straight|boxy|"
    r"stitch|embroider\w*|knit|ribbed|pleat\w*|panel|layered|seam\w*|"
    r"stretch|lightweight|heavyweight|breathable|water\s?repellent)\b", re.I)


# 매장 설명은 불릿으로 쓴다 — 조각이 네댓 글자로 짧다(「소뿔 단추」·「세미 와이드핏」).
# detail_from_ocr 의 _HARD 에는 품목 이름이 없다. 거기서는 일부러 없다 — OCR 잡음 줄에
# 품목 이름이 섞여 들어오면 잡음을 설명으로 세게 된다. 여기(매장이 제 손으로 쓴 글)에서는
# 그 위험이 없으므로 품목·디테일 낱말까지 받는다.
_HARD_ITEM = re.compile(
    r"디테일|포인트|패턴|프린트|트리밍|마감|여밈|셔링|플레어|테이퍼드|스트레이트|박시|"
    r"팬츠|슬랙스|데님|진|쇼츠|스커트|원피스|드레스|자켓|재킷|코트|점퍼|블루종|패딩|"
    r"셔츠|블라우스|니트|가디건|스웨터|티셔츠|맨투맨|후디|후드|베스트|조끼|탑|"
    r"라벨|스티치|워시드|밑위|암홀|카라|칼라")


def says_clothes(part: str) -> bool:
    """이 조각이 **옷 이야기**를 하는가 — 옷의 성질이거나, 인상이거나, 혼용률이다."""
    return bool(_HARD.search(part) or _SOFT.search(part) or _FIBER_PCT.search(part)
                or _HARD_EN.search(part) or _HARD_ITEM.search(part))


def _is_table(part: str) -> bool:
    """치수표가 글자로 흘러든 조각인가 — 수가 조각의 3분의 1을 넘고 넷 이상이면 표다.

    표는 product_sizes.json 이 따로 갖고 있다. 설명 자리에 있으면 읽을 글이 아니라 잡음이다.
    """
    nums = _NUMISH.findall(part)
    return len(nums) >= 4 and sum(len(x) for x in nums) / max(1, len(part)) > 0.18


def _is_scale(part: str) -> bool:
    """핏·비침 눈금자인가 — 눈금 이름이 셋 넘게 잇달아 선다."""
    return len(_SCALE.findall(part)) >= 3


def _parts(text: str):
    for p in _SPLIT.split(text or ""):
        p = re.sub(r"\s+", " ", (p or "")).strip(" :=|·-ㆍ*")
        if not p:
            continue
        if len(p) <= 300:
            yield p
            continue
        # 길다고 버리지 않는다 — 잘라서 본다(첫판은 여기서 멀쩡한 설명을 통째로 잃었다)
        for q in _SPLIT2.split(p):
            q = re.sub(r"\s+", " ", q).strip(" :=|·-ㆍ*")
            if q:
                yield q[:400]


def material_of(text: str) -> str:
    """혼용률만 따로 건진다 — 치수표 한가운데 끼어 있어도 살린다.

    goodlifeworks 는 「SIZE GUIDE FABRIC COTTON 60% POLYESTER 40% SIZE 사이즈 어깨 …」
    처럼 소재와 표가 한 덩이로 붙어 온다. 조각째로 보면 앞의 `SIZE GUIDE` 에 걸려 소재까지
    날아간다. 혼용률은 detail_from_ocr 이 이미 정확히 뽑는다(합이 90~110 일 때만 받는다) —
    그 잣대를 그대로 불러 쓴다.
    """
    got = materials([re.sub(r"\s+", " ", ln) for ln in (text or "").splitlines()] or [text or ""])
    return " ".join(mix if part == "겉감" else f"{part} {mix}" for part, mix in got.items())


def clean(text: str) -> str:
    """찌꺼기를 걷어내고 옷 이야기만 남긴다. 남는 게 없으면 빈 글자열."""
    out: list[str] = []
    for part in _parts(text):
        m = _JUNK.search(part) or _PRICE.search(part)
        if m:
            # 조각 하나에 옷 이야기와 안내문이 같이 있으면 **통째로 버리지 않는다**.
            # 앞선 잣대가 그래서 한 번 졌다 — cayl 은 옷 이야기가 스무 덩이인데
            # 문단에 배송 안내가 섞여 「100% 비었다」로 세어졌다(2026-09-19).
            part = part[:m.start()].strip(" :=|·-ㆍ*")
            # 자른 자리에 이음말이 대롱대롱 남는다 — 「… 사이즈 추천 반드시」
            part = re.sub(r"\s+(?:반드시|또한|그리고|다만|단|그리|아울러)$", "", part).strip()
            if len(part) < 4 or _JUNK.search(part) or _PRICE.search(part):
                continue
        if len(part) < 4 or _is_table(part) or _is_scale(part):
            continue
        if not says_clothes(part):
            continue
        if part in out or any(part in p for p in out):
            continue
        out.append(part)
    mat = material_of(text)
    if mat:
        pairs = {(a.upper(), b) for a, b in _FIBER_PCT.findall(mat)}
        # 혼용률이 다시 적힌 자리는 위에서 고른 것과 같은 말이다 — 두 번 쓰지 않는다.
        # 통째로 버리지는 않는다: 「소뿔 단추 Material : Wool 41% / Polyester 29% …」처럼
        # 앞에 옷 이야기가 붙어 오면 그것까지 잃는다. 혼용률이 시작되는 자리에서 자른다.
        kept = []
        for part in out:
            q = {(a.upper(), b) for a, b in _FIBER_PCT.findall(part)}
            if len(q) >= 2 and q <= pairs:
                m0 = _FIBER_PCT.search(part)
                head = re.sub(r"[\s:/|,·-]*(?:material|fabric|소재|원단|혼용률)?[\s:=]*$", "",
                              part[:m0.start()], flags=re.I).strip()
                if len(head) >= 4 and says_clothes(head):
                    kept.append(head)
                continue
            kept.append(part)
        out = kept
        if not any(mat in p for p in out):
            out.insert(0, mat)
    # 조각을 빈칸으로 이어 붙이면 화면에 한 덩이로 주르륵 흐른다(사람 지적 2026-09-20).
    # 자른 경계가 곧 읽는 사람이 쉬는 자리다 — 줄로 넘겨 앱이 그리게 둔다.
    return "\n".join(out)[:2000]


def from_mined(rec: dict | None) -> str:
    """detail_from_ocr 이 그림에서 뽑아 둔 줄(crawl/detail/<slug>.jsonl)을 한 덩이로."""
    if not rec:
        return ""
    bits: list[str] = []
    for part, mix in (rec.get("material") or {}).items():
        bits.append(mix if part == "겉감" else f"{part} {mix}")
    # 뽑아 둔 설명 문장에도 OCR 잡음이 섞인다 — 여기서 한 번 더 같은 잣대를 건다.
    for s in (rec.get("description") or []):
        s = re.sub(r"\s+", " ", s).strip()
        if _JUNK.search(s) or _is_table(s) or _is_scale(s) or not says_clothes(s):
            continue
        han = len(_HANGUL.findall(s))
        if han < 8 or han / max(1, len(s)) < 0.45:
            continue      # 잡음 줄은 기호·로마자가 많다
        if s not in bits:
            bits.append(s)
    return "\n".join(bits)[:2000]


def best(description: str, mined: dict | None = None) -> tuple[str, str]:
    """보여 줄 설명과 그 출처. 매장 글이 먼저고, 아무 말도 안 하면 그림에서 읽은 글."""
    t = clean(description)
    if t:
        return t, "shop"
    t = from_mined(mined)
    return (t, "ocr") if t else ("", "")
