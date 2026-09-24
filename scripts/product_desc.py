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
import collections
import json
import re
from pathlib import Path

from detail_from_ocr import _HARD, _SOFT, _FIBER_PCT, _HANGUL, materials

DATA = Path(__file__).resolve().parent.parent / "data"

# ─── 조각내기 ───
# HTML 에서 긁은 글은 줄바꿈이 없다 — 한 줄에 수천 자가 붙어 온다.
# 1차: 문장 끝·불릿·세로선. 한국어 문장은 「다/요/죠/함/음」으로 끝난다.
# 「…다·요」 뒤에서 자르되 **말끝**일 때만 — 「…니다」「…어요」「…된다」. 글자 하나(「다」)만 보면 이름씨의
# 끝 글자에서 잘렸다: 「세미 와이드 버뮤다 / 핏 바이오 워싱」(9999archive-429, 앱이 짚음 2026-09-24).
_SPLIT = re.compile(r"\n+|(?:(?<=니다)|(?<=[어아해세에예죠]요)|(?<=[이된한있없했였는]다)|(?<=[죠함음]))\s+|(?<=[.!?])\s+|"
                    # 가운뎃점은 앞이 띄었을 때만 불릿이다 — 「배색 디테일 전·후면에」가 두 줄이 됐다(9999archive-438)
                    r"(?:^|(?<=\s))[·ㆍ]\s*|\s*[•▪◦]\s*|\s+[-–—*]\s+|\s*\|\s*|\s{3,}")
# 2차: 그래도 긴 조각은 쉼표·두 칸으로 더 자른다. 버리지는 않는다.
# 빗금은 앞뒤가 띄었을 때만 — 「배색 디테일 전/후면에」가 「전 / 후면에」 두 줄이 됐다(9999archive-438).
_SPLIT2 = re.compile(r"\s*,\s*|\s{2,}|\s+/\s+(?=[가-힣A-Z])")

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
    r"세탁|드라이\s?[클크]?리?닝|표백|다림질|건조|탈수|드라이어|취급|이염|다리미|찬물|단독\s?손|"
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
    # 편집 메모가 설명에 남는다 — 「…연하게 보이실 수 있습니다.메인 누끼 사진」(포스센스티브)
    r"누끼|메인\s?컷|상세\s?컷\s?(?:참고|아래)|"
    # 주문 칸이 설명으로 흘러든다 — 「Shopping Bag 0원 단독구매상품 0 (0개) 최소주문수량 1개 이상」(ostkaka 209)
    r"최소\s?주문\s?수량|최대\s?주문\s?수량|단독\s?구매\s?상품|"
    # 후기의 머리 — 「[1] 키: 몸무게: 사이즈: 평소 사이즈: …」
    r"키\s?:\s?몸무게|평소\s?사이즈\s?:|^\s*\d{2}\.\d{2}\.\d{2}\s+\d|"
    # 상품 칸 이름표 — 「상품명 TSHIRT … 판매가 ₩48 / 600 30%」(mardi-mercredi, 값이 쪼개져 _PRICE 를 비켜 간다)
    r"판매가|소비자가|상품명\s|"
    # 구매 단추·상품 이름 꼬리·예약 안내·고시 머리글 — 「장바구니 바로구매 … (해외」「[예약발송 10월 07일]」
    # 「[상품 필수 표시 정보] 제품 소재 SHELL」(dunst 575)
    r"장바구니|바로\s?구매|\(해외(?:배송)?|예약\s?발송|상품\s?필수\s?표시\s?정보|"
    r"관련\s?상품|related\s?items|추천\s?상품|함께\s?본|more\s?in\s?this|"
    # 쇼핑몰 틀이 설명에 흘러든다 — 「relation-color1 1927,#0040FF … 해당상품 컬러코드」「홈 Collection main Archive」(cayl)
    r"relation-color|해당\s?상품\s?컬러\s?코드|#[0-9A-Fa-f]{6}\b|(?:^|\s)홈\s+(?:Collection|Product|Shop)\b",
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
    r"stretch|lightweight|heavyweight|breathable|water\s?repellent|"
    # PAF 「Crewneck style jersey T-shirt / Poetic graphic featuring …」 — 생김새 낱말이 없어 빠졌다.
    # 품목 이름(t-shirt·jacket…)은 **넣지 않는다** — 넣었더니 상품 이름 줄(「22SS UNAFFECTED FUN BOX T-SHIRT
    # CHARCOAL (해외」)·메뉴(「홈 Collection … softshell jacket」)가 설명으로 들어왔다(전후 전수 587벌).
    r"crew\s?neck|v-?neck|mock\s?neck|jersey|graphic)\b", re.I)


# 매장 설명은 불릿으로 쓴다 — 조각이 네댓 글자로 짧다(「소뿔 단추」·「세미 와이드핏」).
# detail_from_ocr 의 _HARD 에는 품목 이름이 없다. 거기서는 일부러 없다 — OCR 잡음 줄에
# 품목 이름이 섞여 들어오면 잡음을 설명으로 세게 된다. 여기(매장이 제 손으로 쓴 글)에서는
# 그 위험이 없으므로 품목·디테일 낱말까지 받는다.
_HARD_ITEM = re.compile(
    r"디테일|포인트|패턴|프린트|트리밍|마감|여밈|셔링|플레어|테이퍼드|스트레이트|박시|"
    r"팬츠|슬랙스|데님|진|쇼츠|스커트|원피스|드레스|자켓|재킷|코트|점퍼|블루종|패딩|"
    r"셔츠|블라우스|니트|가디건|스웨터|티셔츠|맨투맨|후디|후드|베스트|조끼|탑|"
    r"라벨|스티치|워시드|밑위|암홀|카라|칼라")


# 잡화의 말 — 벨트·모자·가방·주얼리 설명이 옷 낱말이 없어서 통째로 버려졌다(포스센스티브 벨트
# 「천연 소가죽으로 제작 된 제품입니다」·볼캡 「모자챙이 큰편입니다」, 2026-09-24). 매장이 제 손으로 쓴
# 글(clean)에만 쓴다 — OCR 잡음 줄(from_mined)에는 안 건다(_HARD_ITEM 주석과 같은 까닭).
_HARD_ACC = re.compile(
    r"가죽|레더|스웨이드|누벅|캔버스|볼캡|캡|모자|챙|비니|버킷햇|벨트|버클|아일렛|가방|토트|숄더|크로스백|"
    r"스트랩|지갑|카드홀더|목걸이|네크리스|반지|링|귀걸이|이어링|팔찌|브레이슬릿|체인|펜던트|도금|"
    r"\b(?:cap|hat|beanie|belt|buckle|bag|tote|strap|wallet|necklace|ring|earring|bracelet|chain|pendant)s?\b", re.I)


# 매장 글은 「…니다」체다. 「…샀어요」「…좋아요」는 후기다(rssc 「카시오 시계랑 … 따라 샀어요」 · lookast 벨트 후기).
_PCT_FIBER = re.compile(r"\d{1,3}\s?%\s*[A-Za-z가-힣]|[A-Za-z가-힣]\s*\d{1,3}\s?%")
_SENTENCE_END = re.compile(r"(?:니다|이다|한다|된다|있다|없다)\s*[.!]?\s*$")
_ACC_CARE = re.compile(r"불량|하자|현상|주의|보관|세척|세탁|닦|관리|변색|탈락|오염|습기|직사광선|교환|반품|a/?s|수선|"
                       r"염료|이염|벗겨|손상|마찰|화장품|향수|주름|스크래치|자국|반점")


def says_clothes(part: str, acc: bool = False) -> bool:
    """이 조각이 **옷 이야기**를 하는가 — 옷의 성질이거나, 인상이거나, 혼용률이다.
    acc=True 면 잡화(벨트·모자·가방·주얼리)의 말도 받는다 — 매장이 쓴 글에서만."""
    if (_HARD.search(part) or _SOFT.search(part) or _FIBER_PCT.search(part)
            or _HARD_EN.search(part) or _HARD_ITEM.search(part)):
        return True
    # 잡화 낱말로만 통과하는 줄은 관리·하자 안내가 아니어야 한다 — 가방마다 붙은 「천연가죽의 자연스러운
    # 현상으로 불량 사유가 아닙니다」「화장품·향수가 가죽에 닿으면」(margesherwood 157 · facade-pattern 98)
    # 그리고 **문장**이어야 한다 — 잡화 낱말은 상품 이름 나열(「VISOR LOGO BALL CAP …」)·메뉴(「… BAG
    # ACCESSORIES」)·가격 조각에도 나온다. 전후 전수에서 새로 생긴 설명 267벌의 대부분이 그 꼴이었다.
    # 매장에 따라 명사형으로 쓴다 — 렉토 「위빙 조직의 시그니처 라운드 버클 장식 벨트 사이즈 조절 가능」.
    # 문장이 아니어도 한글이 주인 긴 문구면 받는다(상품 이름 나열·메뉴는 대개 영문 대문자다). 「…요」는 후기.
    if not (acc and _HARD_ACC.search(part)) or _ACC_CARE.search(part) or re.search(r"요\s*[.!~]*\s*$", part):
        return False
    return bool(_SENTENCE_END.search(part)
                or (len(part) >= 12 and len(_HANGUL.findall(part)) >= 0.4 * len(part.replace(" ", ""))))


def _is_table(part: str) -> bool:
    """치수표가 글자로 흘러든 조각인가 — 수가 조각의 3분의 1을 넘고 넷 이상이면 표다.

    표는 product_sizes.json 이 따로 갖고 있다. 설명 자리에 있으면 읽을 글이 아니라 잡음이다.
    """
    nums = _NUMISH.findall(part)
    return len(nums) >= 4 and sum(len(x) for x in nums) / max(1, len(part)) > 0.18


def _is_scale(part: str) -> bool:
    """핏·비침 눈금자인가 — 눈금 이름이 셋 넘게 잇달아 선다."""
    return len(_SCALE.findall(part)) >= 3


# 혼용률 칸(blend)은 **예전 나누기**를 그대로 쓴다. 설명 나누기를 덜 쪼개게 바꾸자(「버뮤다 / 핏」 2026-09-24)
# 혼용률이 긴 조각 안에 묻혀 blend 의 160자 문턱에 걸렸다 — grove 「SIZE S/M … FABRIC SHELL(FACE) : WOOL 51% …」
# 등 37벌의 mat 이 바뀌었다(전후 수출 비교). 칸은 설명과 따로라 예전 결과를 지킨다.
_SPLIT_BLEND = re.compile(r"\n+|(?<=[다요죠함음])\s+|(?<=[.!?])\s+|"
                          r"\s*[·ㆍ•▪◦]\s*|\s+[-–—*]\s+|\s*\|\s*|\s{3,}")
_SPLIT2_BLEND = re.compile(r"\s*,\s*|\s{2,}|\s*/\s*(?=[가-힣A-Z])")


def _parts(text: str, for_blend: bool = False):
    sp1, sp2 = (_SPLIT_BLEND, _SPLIT2_BLEND) if for_blend else (_SPLIT, _SPLIT2)
    for p in sp1.split(text or ""):
        p = re.sub(r"\s+", " ", (p or "")).strip(" :=|·-ㆍ*")
        if not p:
            continue
        if len(p) <= 300:
            yield p
            continue
        # 길다고 버리지 않는다 — 잘라서 본다(첫판은 여기서 멀쩡한 설명을 통째로 잃었다)
        for q in sp2.split(p):
            q = re.sub(r"\s+", " ", q).strip(" :=|·-ㆍ*")
            if q:
                yield q[:400]


# ── 혼용률을 칸으로 ──────────────────────────────────────────────────────────
#
# 앱이 짚었다(2026-09-20): 설명 미리보기 두 줄을 「COTTON 70% POLY 30%」가 먹는다.
# 그런데 그냥 깎으면 그 숫자가 사라진다 — 상세 조각의 소재 칸은 표준화된 이름(「코튼」)
# 뿐이라 70/30 이 어디에도 없다. **지울 게 아니라 옮길 것이다.**
#
# 오는 길에 같은 실수를 세 번 했다. 전부 **글을 안 나누고 먹인 것**이다:
#     ① 씻은 글의 첫 줄 → 82.1%  (거기 혼용률이 맨 앞인 건 우리가 끼워 넣어서다)
#     ② 원문의 첫 줄    → 0.7%   (원문에서는 첫 줄이 아니다)
#     ③ 원문을 줄바꿈으로 나눔 → 0.2%  (**원문에 줄바꿈이 없다.** 통짜 1,000자다)
# 옆에 있던 `_parts()` 를 쓰면 조각 중앙이 21자다. 그걸로 재니 10.7%.
#
# 마지막 빗장은 **소재 흰 목록**이다. 없으면 「더 보기 소재 COW LEATHER」가 소재 이름이
# 되고 산문에서 「A Nylon 100%」를 줍는다. `vocab_aliases.json` 의 소재 어휘를 그대로
# 쓴다 — 아는 소재가 아니면 그 부위를 통째로 안 받는다.
#
# 전수 9.7%(12,327벌) · 뽑힌 것 40개를 읽었고 애매한 것은 하나였다.
# **설명글은 안 건드린다** — 「*main pocket 2 / inner pocket 1 *겉감 Nylon 100%」처럼
# 한 조각에 옷 이야기가 같이 붙어 와서, 조각째 떼면 진짜 설명을 잃는다.
# 화면에서 깎는 것은 앱이 한다(거기는 스펙표가 바로 위에 있다는 것을 안다).
_BLEND_LEAD = re.compile(
    r"^\s*(?:더\s?보기|원단|소재|material|fabric|composition|혼용률)\s*[:：]?\s*", re.I)
_BLEND_PART = re.compile(
    r"(겉감|안감|배색|시보리|충전재|shell|lining|body|trim|filling)\s*[-:：]?", re.I)
_BLEND_ONE = re.compile(r"([A-Za-z가-힣][A-Za-z가-힣\s()-]{0,18}?)\s*(\d{1,3})\s?%")
_FIBER_CANON: dict[str, str] = {}


def _fiber(name: str) -> str | None:
    """적힌 소재 이름 → 우리 표준 이름. 아는 말이 아니면 None."""
    if not _FIBER_CANON:
        try:
            vocab = json.loads((DATA / "vocab_aliases.json").read_text(encoding="utf-8"))
        except Exception:
            return None
        for k, vs in (vocab.get("material") or {}).items():
            _FIBER_CANON[k.lower()] = k
            for v in (vs if isinstance(vs, list) else [vs]):
                _FIBER_CANON[str(v).lower()] = k
    s = re.sub(r"[^0-9A-Za-z가-힣 ]", " ", name).strip().lower()
    if s in _FIBER_CANON:
        return _FIBER_CANON[s]
    t = s.split()
    for w in (3, 2, 1):                    # 긴 이름부터 — 「cow leather」가 「leather」를 이긴다
        for i in range(len(t) - w + 1):
            g = " ".join(t[i:i + w])
            if g in _FIBER_CANON:
                return _FIBER_CANON[g]
    return None


def _blend_split(s: str) -> list[tuple[str, str]]:
    """부위로 가른다. 부위마다 따로 검산해야 한다 — 「Cotton 100% / Poly 100%」를
    합쳐 세면 200이라 버려지는데 실제로는 두 부위가 각각 100%다(앱 쪽이 짚어 줬다)."""
    if _BLEND_PART.search(s):
        out, last, name = [], 0, ""
        for m in _BLEND_PART.finditer(s):
            if last or name:
                out.append((name, s[last:m.start()]))
            name, last = m.group(1), m.end()
        out.append((name, s[last:]))
        return [(n, t) for n, t in out if "%" in t]
    if s.count("%") > 1 and "/" in s:
        return [("", x) for x in s.split("/") if "%" in x]
    return [("", s)]


def blend(text: str) -> list[dict]:
    """혼용률을 부위 구조 그대로 — `[{"p": 부위, "v": [[소재, 퍼센트], …]}, …]`."""
    for seg0 in _parts(text, for_blend=True):
        seg0 = seg0.strip()
        if "%" not in seg0 or len(seg0) > 160:
            continue
        out = []
        for name, seg in _blend_split(_BLEND_LEAD.sub("", seg0)):
            got = _BLEND_ONE.findall(seg)
            tot = sum(int(x) for _, x in got)
            left = len(_BLEND_ONE.sub("", seg).strip(" /,·:：|"))
            if not got or not (90 <= tot <= 110) or left > 6:
                out = []
                break
            vs = []
            for m, x in got:
                f = _fiber(m)
                if not f:
                    vs = []
                    break              # 아는 소재가 아니면 이 부위는 안 받는다
                vs.append([f, int(x)])
            if not vs:
                out = []
                break
            out.append({"p": name.lower(), "v": vs})
        if out:
            return out
    return []


def material_of(text: str) -> str:
    """혼용률만 따로 건진다 — 치수표 한가운데 끼어 있어도 살린다.

    goodlifeworks 는 「SIZE GUIDE FABRIC COTTON 60% POLYESTER 40% SIZE 사이즈 어깨 …」
    처럼 소재와 표가 한 덩이로 붙어 온다. 조각째로 보면 앞의 `SIZE GUIDE` 에 걸려 소재까지
    날아간다. 혼용률은 detail_from_ocr 이 이미 정확히 뽑는다(합이 90~110 일 때만 받는다) —
    그 잣대를 그대로 불러 쓴다.
    """
    got = materials([re.sub(r"\s+", " ", ln) for ln in (text or "").splitlines()] or [text or ""])
    return " ".join(mix if part == "겉감" else f"{part} {mix}" for part, mix in got.items())


# 매장 화면의 구간 이름표 — 어떤 소비자에게도 정보가 아니다(전수 2,006편).
_SECTION_HEAD = re.compile(
    r"^\s*(?:feature|features|detail|details|description|info|information|fabric|"
    r"material|composition|상품\s?설명|제품\s?설명|패브릭\s?정보|소재\s?정보|디테일)"
    r"\s*[:：]?\s*$", re.I)
# 치수표가 글로 흘러든 첫 줄(전수 275편). 사이즈표가 바로 위에 있는데 또 적힌다.
_SIZE_HEAD = re.compile(r"^\s*(?:총장|기장|어깨|가슴|허리|엉덩이|밑단|소매|허벅지|밑위)\s*\d")
# 글 **앞에 붙은** 이름표 — 「Detail 24/2합의 두꺼운 면 …」(앱 쪽 예시 2026-09-20).
# **영문만** 뗀다. 한글 「디테일」·「소재」는 문장에 그대로 쓰이므로 건드리면 안 된다
# (「디테일한 자수」·「소재가 좋은」). 뒤에 글이 넉넉히 남을 때만 뗀다.
_INLINE_HEAD = re.compile(
    r"^(?:feature|features|detail|details|description|info|information|fabric|"
    r"material|composition)\s*[:：]?\s+(?=.{10,})", re.I)
# 다 고른 뒤 **첫 줄에서만** 떼는 이름표. 뒤에 무엇이 남든(짧아도, 대괄호여도) 뗀다 —
# 「INFO [FABRIC]」·「DETAIL HOOD」처럼 짧은 줄이 위 잣대의 열 자 조건에 걸려 남았다.
# 「INFO [FABRIC]」처럼 이름표 뒤에 또 이름표가 대괄호로 오는 매장이 있다(noirer).
# 붙어 있는 만큼 되풀이해 뗀다. 보이지 않는 글자(BOM·제로폭)도 같이 지운다 —
# 그것 때문에 「INFO」가 이름표로 안 읽히고 한 줄로 남던 것이 있었다.
_HEAD_LABEL = re.compile(
    r"^[\s\ufeff\u200b\[(]*(?:feature|features|detail|details|description|info|information)"
    r"[\s\ufeff\u200b\])]*[:：]?[\s\ufeff\u200b]*", re.I)
# 이름표를 뗀 자리에 남는 대괄호 꼬리표 하나 — 「[FABRIC]」·「[COLOR]」.
_HEAD_BRACKET = re.compile(r"^\[[A-Za-z][A-Za-z\s/&-]{1,18}\]\s*")


# 문장이 끝난 자리 — 「.」「!」「?」 또는 「…니다·요·다」 뒤의 빈칸. 안내 낱말이 새 문장 첫머리에 오면
# (앞이 빈칸·기호뿐) 그 자리에서 자르고, 문장 가운데 오면 그 문장을 통째로 뺀다(clean 주석).
_SENT_STOP = re.compile(r"[.!?。](?=\s|$)|(?:니다|어요|아요|해요|세요|에요|예요|이다|합니다)(?=[\s.!~]|$)"
                        # 혼용률이 끝난 자리도 끝으로 본다 — 「FABRIC Cotton98 Spandex2 은은하게 …」
                        r"|%(?=\s)|\b[A-Za-z]{3,}\s?\d{1,3}(?=\s)")
# 불릿 글의 한 항목이 끝나는 낱말 — 앱이 긴 줄을 끊는 자리와 같은 말(앱 세션 2026-09-24)
_FEAT_END = re.compile(r"(?:사용|연출|디테일|구조|가능|포인트|적용|마감|완성|처리|실루엣|핏)(?=\s|$)")
# 이음말·조사에서 끊긴 끝 — 「있으므로」「마찰이나 수분에 의해」「염료로 인해」「어두운 원단 계열의 상품은」
# 「COTTON 100% 제품의」「잠그신 후 뒤집어서」「소재로 물」(한 글자 토막). 「상의·하의」는 옷 이름이라 뺀다.
_DANGLING = re.compile(
    r"(?:으로|므로|하여|해서|어서|아서|하고|하며|이나|거나|의해|인해|인한|위해|경우|에서|부터|까지|처럼|보다|또는|혹은|"
    r"및|등은|등의|등을|(?<![상하내])의|[가-힣](?:은|는|을|를)|(?<![블헤트])[가-힣]로|"
    # 영문 안내문의 머리 — 「We recommend … by washing and」
    r"(?i:\b(?:and|or|with|by|of|to|for|in|on|the|a|an))|"
    # 한 글자 토막은 좁게 — 「넥」「핏」「탑」은 옷 말이다(「… 라운드 넥」 coor). 후기 쓴 이의 성(「가디건 추천 김」)
    r"(?:^|\s)(?:물|및|등|후|시|때|중|김|이|박|최|정|홍|백|전))\s*$")


def clean(text: str, acc: bool = False) -> str:
    """찌꺼기를 걷어내고 옷 이야기만 남긴다. 남는 게 없으면 빈 글자열.
    acc=True(잡화 상품)면 벨트·모자·가방·주얼리의 말도 받는다 — 옷에 켜면 「천연가죽의 자연스러운 현상으로
    불량 사유가 아닙니다」 같은 안내문이 가죽 낱말 때문에 옷 설명으로 들어왔다(전후 전수 161벌)."""
    out: list[str] = []
    for part in _parts(text):
        if _SECTION_HEAD.match(part) or _SIZE_HEAD.match(part):
            continue
        part = _INLINE_HEAD.sub("", part)
        m = _JUNK.search(part) or _PRICE.search(part)
        if m:
            # 조각 하나에 옷 이야기와 안내문이 같이 있으면 **통째로 버리지 않는다**.
            # 앞선 잣대가 그래서 한 번 졌다 — cayl 은 옷 이야기가 스무 덩이인데
            # 문단에 배송 안내가 섞여 「100% 비었다」로 세어졌다(2026-09-19).
            # **문장 끝에서만** 자른다. 안내 낱말이 있는 자리에서 바로 자르던 때(2026-09-19~)는
            # 그 낱말이 든 문장의 앞머리가 남았다 — 「원단의 수축,변형이 생길 수 있으므로」
            # (kirsh 「… 건조기사용을 삼가해 주십시오」) · 「생지 원단 특성상 마찰이나 수분에 의해」
            # (9999archive 「… 이염 및 물 빠짐이」) · 상품 이름 줄 「… Denim Pants (해외」. 앱이 짚었다:
            # 9999archive 353편 중 143 · kirsh 929 중 180 이 문장 가운데서 끝났다(2026-09-24).
            # 자른 뒤 마지막 문장이 이음말·조사에서 끊겼으면(「있으므로」「의해」「상품은」「제품의」) 그 문장은
            # 안내문의 앞머리다 — 앞의 끝난 문장까지만 둔다. 말이 끝난 모양이면 그대로 둔다: 문장부호 없이
            # 이어 쓰는 설명(coor 「… 해링본 테이프 디테일」)과 혼용률 줄(「SHELL: 82% POLYESTER …」)을
            # 「끝난 문장이 없다」고 통째로 버렸더니 설명 7,248편이 사라졌다(전후 전수 첫 판).
            head = part[:m.start()].rstrip(" :=|·-ㆍ*(,/")
            # 닫히지 않은 괄호는 안내문의 머리다 — 「… 소가죽 카드지갑 (상세 사이즈 치수는」(matin-kim)
            head = re.sub(r"\s*\([^()]*$", "", head)
            # 홀로 남은 「물」은 「물세탁」의 머리다 — 그 낱말만 뗀다(「… 스트라이프 원단 물」 coor)
            head = re.sub(r"[\s,]+물$", "", head)
            ends = [e.end() for e in _SENT_STOP.finditer(head)]
            tail = head[ends[-1]:] if ends else head
            if tail.strip() and _DANGLING.search(tail):
                if ends:
                    head = head[:ends[-1]]
                elif len(head) < 80:
                    head = ""          # 짧은 조각 하나가 통째로 안내문의 앞머리다
                else:
                    # 문장부호 없는 긴 불릿 글 — 끝에서 가장 가까운 특징 낱말 뒤에서 끊는다
                    # (「… 실루엣 조절 가능 착용하고 [세탁을 진행함에 따라]」 9999archive-438)
                    fe = [e.end() for e in _FEAT_END.finditer(head)]
                    if fe and len(head) - fe[-1] <= 60:
                        head = head[:fe[-1]]
                # 끝난 자리가 하나도 없는 긴 글(문장부호 없이 이어 쓴 불릿)은 예전처럼 둔다 — 버리면
                # kijun 「… 스트랩 Material & Care FABRIC Cotton98 Spandex2 은은하게 비치는 시어한 소」처럼
                # 옷 이야기 전부를 잃는다(전후 전수 둘째 판).
            part = head.strip(" :=|·-ㆍ*")
            # 자른 자리에 이음말이 대롱대롱 남는다 — 「… 사이즈 추천 반드시」
            part = re.sub(r"\s+(?:반드시|또한|그리고|다만|단|그리|아울러)$", "", part).strip()
            if len(part) < 4 or _JUNK.search(part) or _PRICE.search(part):
                continue
        # 혼용률 줄은 숫자가 많아도 표가 아니다 — Hyein Seo 「Material : 100% Cotton, 70% Cotton 27% Silk
        # 3% Spandex, 100% Cotton[100% Tencel]」이 숫자 비율 0.20 으로 표로 걸려 설명이 통째로 비었다.
        if len(part) < 4 or (_is_table(part) and len(_PCT_FIBER.findall(part)) < 2) or _is_scale(part):
            continue
        if not says_clothes(part, acc=acc):
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
        # 혼용률을 맨 앞에 세운다. 앱이 「미리보기 두 줄을 이 줄이 먹는다」고 짚었고
        # 그 말이 맞지만(2026-09-20), **칸으로 옮기려다 접었다** — 전수로 재니 0.2%만
        # 잡히고 그마저 산문 조각을 주웠다(「… 리본에서 … 린넨 36%」 → 안감 100%).
        # 상세 조각의 소재 칸은 표준화된 이름뿐이라, 여기서 빼면 70/30 이 사라진다.
        # 앱이 화면에서 좁혀 깎기로 했다(퍼센트 하나 · 99 이상일 때만).
        if not any(mat in p for p in out):
            out.insert(0, mat)
    # 첫 줄 앞머리의 영문 이름표는 **맨 마지막에** 뗀다. 위쪽 고리에서 떼면 남은 조각이
    # 뒤의 거르개(says_clothes·길이)에 걸려 통째로 사라진다 — 「Detail 평균 모자보다」에서
    # 라벨만 떼자 「평균 모자보다」가 버려져 다음 줄과 이어지던 문장이 끊겼다(2026-09-21 실측).
    # 여기서는 이미 고를 것을 다 고른 뒤라 라벨만 떨어지고 글은 그대로 남는다.
    # 앱이 화면에서 같은 일을 하고 있었다 — 규칙이 두 군데로 갈라지지 않게 이쪽으로 모은다
    # (앱 제보 2026-09-21: 표본 12,739편 중 6.8%가 INFO·DETAIL·FEATURE 로 시작).
    if out:
        head = out[0]
        for _ in range(3):
            h2 = _HEAD_BRACKET.sub("", _HEAD_LABEL.sub("", head))
            if h2 == head:
                break
            head = h2
        head = head.strip(" :：|·-\ufeff\u200b")
        # 라벨을 떼고 아무것도 안 남으면 그 줄은 이름표뿐이다 — 하나뿐이어도 버린다
        # (「INFO [FABRIC]」만 있는 설명 아홉 편이 그래서 남아 있었다).
        out = [head] + out[1:] if head else out[1:]
    # 조각을 빈칸으로 이어 붙이면 화면에 한 덩이로 주르륵 흐른다(사람 지적 2026-09-20).
    # 자른 경계가 곧 읽는 사람이 쉬는 자리다 — 줄로 넘겨 앱이 그리게 둔다.
    return "\n".join(out)[:2000]


_MINED_CARE = re.compile(r"세제|표백|건조기|다림질|드라이\s?클리닝|손세탁|물세탁|세탁|탈수|보풀|필링|이염|물빠짐|"
                         r"주의해\s?주|주의하여|권장합니다|삼가|보관하여|보관해\s?주")
_OCR_CAPS = re.compile(r"(?<![A-Za-z])[A-Z]{2,4}(?![A-Za-z])")


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
        # 그림 글의 세탁·관리 문장은 설명이 아니다 — the-coldest-moment 는 매장 글의 세탁 한 줄을 빼자 그림의
        # 「합성세제 ASE 원단 손상을 유발할 수 있으므로 … 권장합니다」가 설명 자리로 올라왔다(2026-09-24).
        if _MINED_CARE.search(s):
            continue
        # 한글 문장에 뜻 없는 대문자 토막이 둘 이상 끼면 OCR 이 글자를 뭉갠 줄이다(「ASE … SAMA ASS」)
        if len(_OCR_CAPS.findall(s)) >= 2 and len(_HANGUL.findall(s)) > len(s) * 0.3:
            continue
        han = len(_HANGUL.findall(s))
        if han < 8 or han / max(1, len(s)) < 0.45:
            continue      # 잡음 줄은 기호·로마자가 많다
        if s not in bits:
            bits.append(s)
    return "\n".join(bits)[:2000]


# 「안감 없음」「신축성 있음」은 그 옷의 성질이다 — 같은 매장의 여러 벌이 같은 값을 가져도 안내문이 아니다(758벌).
_ATTR_LINE = re.compile(r"^[\s/·-]*(?:안감|신축성|비침|두께감?|핏|무게감?)\s*[:：]?\s*(?:있음|없음|보통|약간|조금|적음|많음)")


def store_repeats(texts: list[str], min_n: int, share: float = 0.4) -> set[str]:
    """한 매장의 설명들에서 되풀이되는 줄(best 가 만든 글의 줄 단위) — 그 매장의 안내문이다.

    포스센스티브 그림 글 13벌이 모두 「-후가공 디테일이 들어간 제품마다 개체차이가 있을 수 있습니다」를
    설명으로 달았다(2026-09-24). 옷을 말하는 낱말(디테일)이 들어 있어 줄 하나하나로는 못 거른다 —
    **그 매장 안에서 되풀이된다**는 것이 안내문의 표시다(tag_items.store_repeats 와 같은 생각).
    혼용률(%)이 든 줄은 되풀이돼도 둔다 — 같은 원단을 여러 벌에 쓴다.
    """
    texts = [t for t in texts if t]
    if len(texts) < min_n:
        return set()
    cnt = collections.Counter(ln for t in texts for ln in set(t.split("\n")) if ln.strip())
    lim = max(min_n, share * len(texts))
    return {ln for ln, c in cnt.items() if c >= lim and not _FIBER_PCT.search(ln) and not _ATTR_LINE.search(ln)}


def drop_lines(text: str, lines: set[str]) -> str:
    return "\n".join(ln for ln in (text or "").split("\n") if ln not in lines).strip() if lines else text


# 카페24 스펙 칸의 짧은 설명 — 넘버링은 설명 칸에 후기 위젯·반품 안내만 있고 진짜 설명
# (「시곗줄을 연상시키는 체인 브레이슬릿입니다」)은 여기에만 있다(2026-09-24).
SPEC_DESC_KEYS = ("상품간략설명", "상품요약정보", "간략설명", "상품 간략설명")


def best(description: str, mined: dict | None = None, acc: bool = False,
         spec: dict | None = None) -> tuple[str, str]:
    """보여 줄 설명과 그 출처. 매장 글이 먼저, 없으면 스펙의 간략설명, 그래도 없으면 그림에서 읽은 글."""
    t = clean(description, acc)
    if t:
        return t, "shop"
    if spec:
        t = clean("\n".join(str(spec[k]) for k in SPEC_DESC_KEYS if spec.get(k)), acc)
        if t:
            return t, "shop"
    t = from_mined(mined)
    return (t, "ocr") if t else ("", "")


# ── 특징 · 안내 나누기 (앱 세션 부탁 2026-09-24) ─────────────────────────────────
# 앱이 설명 탭을 「특징」(한 줄에 하나)과 「알아 두세요」(작게)로 나눠 그린다. 앱이 모든 매장에 같은 규칙을
# 걸면 매장 글 버릇이 달라 틀리는 곳이 생겨서 창고가 나눠 싣는다: descs 한 줄에 f(특징 줄) · n(안내 문장).
# 안내는 **말투만으로 가르지 않는다** — 「비대칭 포켓은 수납성을 극대화 시킬 수 있습니다」(cayl)는 공손체지만
# 특징이다. 공손체로 끝나면서 주의·권장·차이 같은 안내 말이 든 문장, 또는 ※·* 로 시작하는 문장만 안내다.
_NOTE_END = re.compile(r"(?:니다|세요|십시오|주세요|바랍니다|드립니다|해요|어요|아요)[.!~]*\s*$")
# 안내 낱말은 좁게 — 「변형이 가능한 … 아우터입니다」(sinoon)처럼 옷 이야기에도 나오는 말(변형·보관·확인)은 뺐다.
_NOTE_CUE = re.compile(r"주의|유의|권장|권해|삼가|피해\s?주|금지|마세요|말아\s?주|양해|부탁|바랍니다|불량|교환|반품|"
                       r"오차|개체\s?차|편차|차이가\s?(?:있|발생|날|생)|발생할\s?수|생길\s?수|다를\s?수\s?있|상이할\s?수|"
                       r"세탁|드라이\s?[클크]|이염|물\s?빠짐|보풀|측정|실측")
_NOTE_HEAD = re.compile(r"^\s*[※*]")
_SENT_CUT = re.compile(r"(?<=[.!?])\s+|(?:(?<=니다)|(?<=세요)|(?<=십시오))\s+")
_FEAT_CUT = re.compile(r"(?<=사용|연출|구조|가능|적용|마감|완성|처리)\s+|(?<=디테일|포인트)\s+")


def split_fn(text: str) -> tuple[list[str], list[str]]:
    """(특징 줄들, 안내 문장들). 줄 안에 특징과 안내가 섞이면 문장 단위로 가른다.
    긴 특징 줄(60자 넘고 문장부호 없음)은 「사용·연출·디테일·구조·가능·포인트·적용·마감·완성·처리」 뒤에서 끊는다."""
    feats: list[str] = []
    notes: list[str] = []
    # 매장이 붙여 쓴 「… 립 원단 사용소재 :면 100%」(kirsh) — 소재 칸은 제 줄로
    text = re.sub(r"(?<=\S)(?=(?:소재|원단|혼용률)\s*[:：])", "\n", text or "")
    for line in text.split("\n"):
        line = line.replace("﻿", "").strip()
        if not line:
            continue
        for sent in _SENT_CUT.split(line):
            sent = sent.strip()
            if not sent:
                continue
            if _NOTE_HEAD.match(sent) or (_NOTE_END.search(sent) and _NOTE_CUE.search(sent)):
                notes.append(re.sub(r"^\s*[*]\s*", "", sent))
                continue
            if len(sent) > 60:
                # 끝에 문장이 붙어 있어도 나눈다 — 「… 포켓 디테일 여유로운 … 마감 처리 [9999x후디진호] … 있습니다.」
                # 짧은 토막·이음말로 시작하는 토막은 앞에 붙인다 — 「부자재 사용 / 및 정교하고 견고한 마감 / 처리」
                pieces: list[str] = []
                for p in (x.strip() for x in _FEAT_CUT.split(sent)):
                    if not p:
                        continue
                    if pieces and (len(p) < 8 or re.match(r"(?:및|또는|그리고|혹은|과|와)\s", p)):
                        pieces[-1] += " " + p
                    else:
                        pieces.append(p)
                feats.extend(pieces)
            else:
                feats.append(sent)
    return feats, notes


def is_name_only(text: str, name: str) -> bool:
    """설명이 상품 이름 줄뿐인가 — 「Half sleeve puff cardigan in Pink」. 앱은 이름을 상세 맨 위에 크게 쓰므로
    설명이 그 한 줄뿐이면 빈 설명으로 내보낸다(앱 세션 2026-09-24, 전수 931편)."""
    norm = lambda s: re.sub(r"[\s\W_]+", "", (s or "").lower())
    nm = norm(name)
    lines = [norm(x) for x in (text or "").split("\n") if norm(x)]
    if not nm or not lines:
        return False
    return all(x == nm or (len(x) >= 4 and (x in nm or nm in x) and min(len(x), len(nm)) / max(len(x), len(nm)) >= 0.6)
               for x in lines)
