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
                    r"(?:^|(?<=\s))[·ㆍ]\s*|\s*[•▪◦]\s*|\s+[-–—*]\s+|\s*\|\s*|\s{3,}|"
                    # 별표를 글머리에 **붙여** 쓰는 매장 — 「 *세미 오버핏 … *고중량 …」. 뒤가 안 띄어 위의 「 * 」에
                    # 안 걸려 글 전체가 한 조각이 됐고, 그 조각이 배송 안내와 함께 버려져 알리스 777 의 설명이
                    # 「POLYESTER 100% LINING」 한 줄만 남았다(코덱스 검증 008, 2026-09-28).
                    r"(?:^|\s+)\*(?=[^\s*])|(?<=[가-힣])\*\s+|"
                    # 줄표도 같은 꼴 — 프레클 「상품문의 (0) -구제 가공과 바이오워싱으로 …」가 안내 탭 이름과 한 조각이
                    # 되어 함께 버려졌다(코덱스 검증 008). 뒤가 한글일 때만 — 「S -M」 · 「-5%」는 건드리지 않는다.
                    r"\s+-(?=[가-힣])")
# 2차: 그래도 긴 조각은 쉼표·두 칸으로 더 자른다. 버리지는 않는다.
# 빗금은 앞뒤가 띄었을 때만 — 「배색 디테일 전/후면에」가 「전 / 후면에」 두 줄이 됐다(9999archive-438).
_SPLIT2 = re.compile(r"\s*,\s*|\s{2,}|\s+/\s+(?=[가-힣A-Z])")

# ─── 버릴 갈래 ───
# 사람이 이름을 대어 버리라 한 셋(배송·세탁·후기/문의)과, 상품과 무관한 매장 안내.
_TEMPLATE = re.compile(r"\{[$#][A-Za-z_][^{}]{0,40}\}")
# 「같이 보면 좋은 상품」 목록 — 머리말 뒤에 「이름 값원 (할인값원)」이 잇달아 선다. 프레클 「STYLED WITH
# faux leather lace shorts in ivory 108,000원 75,600원 …」 · 제너럴아이디어 「추가구성상품 WOMAN 테일러드
# 미니스커트 [CHARCOAL] 59,000 …」. 쉼표에서 갈려 「600원 faux leather …」 같은 조각으로 설명에 섰다
# (코덱스 검증 008, 2026-09-28). 머리말부터 마지막 값까지 통째로 지운다.
_P = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d{2})?\s*원?|\d{4,7}\.\d{2})"
_RELATED = re.compile(rf"(?:STYLED\s+WITH|추가\s?구성\s?상품|관련\s?상품|RELATED\s+(?:ITEMS?|PRODUCTS?))"
                      rf"(?:\s*(?:(?!{_P}).){{1,120}}?{_P}(?:\s+{_P})*)+", re.I)
# 글자 번호를 단 실측 목록 — 「사이즈 [SIZE] S (55) a. 어깨 34 / b. 가슴 39.5 / … M (66) a. …」(제너럴아이디어).
# 사이즈표는 따로 읽으므로 설명에서는 지운다(코덱스 검증 008).
_LETTERED = re.compile(r"(?:사이즈\s*)?(?:\[SIZE\]\s*)?(?:(?:\b(?:[XSML]{1,3}|F|FREE|ONE\s?SIZE)\b\s*(?:\(\d{2,3}\))?\s*)?"
                       r"(?:\b[a-h]\.\s*[가-힣A-Za-z]{1,6}\s*\d{1,3}(?:\.\d)?(?:\s*\([^)]{1,6}\))?\s*/?\s*){2,})+", re.I)
# 한 줄 실측 — 「SIZE 1: CHEST - 119 / SLEEVE - 58.5/CB - 88/93 SIZE 2: …」(메리메이드) · 「SIZE (CM) 어깨 55 가슴 58 암홀 24 …」
# (포에토). 실측은 따로 읽으므로 설명에서 뺀다 — 숫자 조각 「119 / SLEEVE」가 설명 줄로 섰다(코덱스 011).
_EN_SIZE_ROWS = re.compile(r"(?:SIZING\s*[:：]?\s*)?(?:\bSIZE\s*[A-Z0-9]{1,4}\s*[:：]\s*"
                           r"(?:[A-Za-z][A-Za-z .]{0,24}?\s*[-:：]\s*\d{1,3}(?:[.,]\d+)?(?:\s*/\s*\d{1,3}(?:[.,]\d+)?)?\s*/?\s*){2,})+", re.I)
_KO_SIZE_RUN = re.compile(r"(?:SIZE\s*\(\s*CM\s*\)\s*)?(?:[가-힣]{1,5}\s*\d{1,3}(?:\.\d)?(?:\s*\([^)]{1,6}\))?\s*/?\s*){3,}", re.I)
_DESIGNER_HEAD = re.compile(r"(?:상세\s?설명\s*)?\[?DESIGNER\s+COMMENT\]?\s*[─━—\-]*", re.I)

_JUNK = re.compile(
    # 지나간 판매 안내 — 옷 이야기가 아니다. 99%IS- 38벌의 설명이 통째로
    # 「프리오더 마감: 2021/8/23」 한 줄이었다(앱 쪽 지적 2026-09-20).
    # **날짜가 붙은 꼴만** 막는다 — 「마감」을 통째로 막으면 「소매/밑단 시보리 마감」처럼
    # 옷을 말하는 줄이 날아간다(전수로 재니 그 꼴이 12건 있었다).
    r"(?:프리\s?오더|pre-?order)\s*(?:마감|종료|기간)\s*[:：]?\s*\d{2,4}\s*[/.\-]\s*\d{1,2}|"
    # 후기·문의 — 「만족 [1] 네이**** 2025-09-12 조회 2 추천 0」
    r"조회\s?\d+\s*추천\s?\d+|추천\s?\d+\s*$|\*{3,}|이전\s?페이지|다음\s?페이지|"
    # 카페24 주문 상자 — 설명 그릇(.detailArea)에 같이 담겨 온다. 프레클 「… 걸 고리 총 상품금액 (수량) : 0 (0개)」
    # (코덱스 검증 004, 2026-09-28). 문장 끝에 붙어 오므로 그 자리에서 자르면 앞의 옷 이야기가 남는다.
    r"총\s?상품\s?금액|TOTAL\s*\(\s*QUANTITY\s*\)|최소\s?주문\s?수량|"
    # 「새벽배송 오전 7시 이전 배송완료」 — 「배송」에서 자르면 「… 새벽」이 남았다(제너럴아이디어, 코덱스 검증 008).
    r"새벽\s?배송|"
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
    # 교환 안내의 법률 말 — 「고객님에 의한 상품,라벨, 택 등이 멸실」(어랜드) · 「상품의 택, 라벨을 제거한 경우」.
    # 「라벨」이 옷 낱말이라 says_clothes 를 통과해 설명에 남았다(코덱스 검증 008, 2026-09-28).
    r"고객님?\s?(?:에\s?의한|의\s?부주의)|멸실|(?:택|라벨)\s*(?:,\s*(?:택|라벨)\s*)?(?:을|를)?\s*(?:제거|떼|훼손)|"
    r"\bdry\s?clean|\bhand\s?wash|\bmachine\s?wash|\bbleach\b|\btumble\s?dry\b|"
    r"\bdo\s?not\s?\w+|\biron\b|\bwashing\s?method\b|\bhandling\b|"
    # 사이즈 재는 법·구매 안내 — 옷 이야기가 아니라 표 읽는 법이다
    r"기준입니다|측정\s?기준|측정\s?방법|뒷목부터|오차가|오차\s?범위|단면\s?기준|"
    r"구매해\s?주시|구매하시는|참고\s?하?시?어|참고해\s?주|"
    # 사이즈 추천 안내의 앞머리 — 「사이즈 추천 가이드」「사이즈 추천을 원하신다면」만 남아 설명 탭에 이 두 줄만
    # 떴다(forcesensitive 8편, 앱 세션 2026-09-25). 「정사이즈 추천」 같은 핏 이야기는 앞에 글자가 붙어 안 걸린다.
    r"(?<![가-힣])사이즈\s?추천\s?(?:가이드|을\s?원하)|"
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


def _parts(text: str, for_blend: bool = False, with_comma: bool = False):
    """조각들. with_comma=True 면 (조각, 바로 앞 조각과 쉼표로만 갈렸는가) 를 낸다 — clean 이 두 조각을 다 살리면
    쉼표로 도로 잇는다(아래 clean 주석)."""
    sp1, sp2 = (_SPLIT_BLEND, _SPLIT2_BLEND) if for_blend else (_SPLIT, _SPLIT2)
    for p in sp1.split(text or ""):
        p = re.sub(r"\s+", " ", (p or "")).strip(" :=|·-ㆍ*")
        if not p:
            continue
        if len(p) <= 300:
            yield (p, False) if with_comma else p
            continue
        # 길다고 버리지 않는다 — 잘라서 본다(첫판은 여기서 멀쩡한 설명을 통째로 잃었다)
        pos, comma = 0, False
        for m in [*sp2.finditer(p), None]:
            q = p[pos:m.start()] if m else p[pos:]
            q = re.sub(r"\s+", " ", q).strip(" :=|·-ㆍ*")
            if q:
                yield (q[:400], comma) if with_comma else q[:400]
            if m:
                pos, comma = m.end(), "," in m.group()


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


# ── 혼용률 2판: 출처를 넓히는 해석기(mix) ─────────────────────────────────────
#
# 위 blend 는 설명글(description)의 **짧은 조각** 하나에서만 읽는다. 판매중 의류 102,509벌 중 mat 이 나간 것이
# 13,909벌(13.6%)뿐이었는데, 못 읽은 88,600벌 가운데 70,000벌 가까이가 원본 어딘가에 혼용률을 갖고 있었다
# (2026-10-02 전수, blend 갈래). 못 읽은 까닭은 대개 출처가 아니라 **조각내기**였다:
#     dunst      「[상품 필수 표시 정보] 제품 소재 SHELL: COTTON 100% 색상 SALT PINK 치수 …」  ← 160자 문턱
#     insilence  「겉감 : Wool 50%, Polyester 50% 안감 : Polyester 100% 단추 : 천연 소뿔 단추 …」 ← 남은 글 > 6자
#     ae-ae      「Alpaca 5% / Wool 20% / Nylon 45% / Acrylic 30%」  ← 빗금마다 따로 검산해 넷 다 떨어짐
#     ader-error 「[Main] 면 73 폴리에스터 27 [Sub] 면 96 폴리우레탄 4」  ← % 를 안 쓰는 매장
# 그리고 그림 글(OCR) · 사이즈가이드 창 · 브라우저 글은 아예 안 봤다.
#
# 그래서 조각이 아니라 **「섬유 + 숫자」 덩이가 잇달아 선 줄기(run)** 를 찾는다. 줄기 앞뒤에 무슨 글이 있든
# 상관없고, 줄기 **안**은 깨끗해야 한다(덩이 사이에는 구분 기호 · 부위 이름 · 꾸밈말만). 틀린 숫자를 넣는 것이
# 빈칸보다 나쁘므로 blend 보다 엄격하게 받는다:
#   · 부위마다 합이 100 이어야 한다 — % 가 다 적혔으면 99~101(소수 반올림), 하나라도 % 가 없거나 깨졌으면
#     **정확히 100** 이 되는 해석이 **하나뿐**일 때만. blend 의 90~110 은 「COTTON 92%」만 읽힌 것(폴리 8% 를
#     놓친 것)까지 받았다 — saintpain 창 글에서 그렇게 「코튼 92」가 나갈 뻔했다.
#   · 섬유 이름은 아는 것만(_MIX_FIBERS). 데님 · 니트 · 메쉬는 짜임이지 섬유가 아니다. 웰론(충전솜 상표) ·
#     기타 · 메탈릭은 모르는 것으로 둔다 — 그 부위가 끼면 줄기를 통째로 버린다(아래 「남은 부위」).
#   · 부위를 섞지 않는다. 부위 이름이 줄기 안에 서면 거기서 새 부위다. 줄기 바로 뒤에 부위 이름 + 숫자나
#     다른 「NN%」가 남아 있으면 **덜 읽은 것**으로 보고 버린다 — 「SHELL-POLYESTER 100% / LINING-POLYESTER 100%
#     / FILLER-WELLON 100%」(nick-nicole)에서 겉감 · 안감만 내보내면 충전재가 없는 옷이 된다.
#   · 한 글에 서로 다른 줄기가 둘 이상 맞으면, 하나가 나머지를 다 품을 때(문장 「코튼 100% 소재」 + 표 「겉감 코튼 100%
#     안감 폴리 100%」)나 부위 이름이 서로 겹치지 않을 때만 받고 아니면 버린다(색마다 · 세트 벌마다 다를 수 있다, mix_pick).
#   · 검산에서 떨어진 줄기에 결과에 없는 부위 이름이 있으면 그 부위를 못 읽은 것이다 — 버린다(_mix_runs 의 lost_parts).
#   · % 없는 숫자(「겉감 면 100」 「FABRIC Cotton100 LINING Polyester100」 「[Main] 면 73」)는 바로 앞에
#     이름표(소재 · FABRIC · 혼용률 · 겉감 · [Main] …)가 있을 때만 받는다. 아무 데서나 「COTTON 100」을 받으면
#     「Payment information COTTON 100 XS부터」 같은 것도 들어온다.
#   · 그림 글(ocr=True)만 % 오독을 고친다. 판독기는 % 를 「9」 「96」 「X」 「o」로 읽는다 — 「코튼 1009」
#     (till-i-die) · 「면 10096」(divein) · 「면 939 스판 796」(divein, 93 · 7). 꼬리 9 · 96 을 떼어 본 해석과
#     그대로의 해석을 다 놓고 합이 정확히 100 이 되는 조합이 **하나뿐**일 때만 받는다. 이름표가 없으면 숫자 자체가
#     100 을 넘어 오독이 확실한 것(1009 · 939)과 글자 꼬리(100X)만 받는다 — 「울 759 혼방」은 75 하나라 버려진다.
_MIX_FIBERS = {"코튼", "폴리에스터", "나일론", "레이온", "린넨", "스판덱스", "울", "아크릴", "캐시미어", "텐셀",
               "알파카", "모헤어", "큐프라", "아세테이트", "실크", "모달", "다운", "깃털", "앙고라", "야크",
               "가죽", "천연가죽", "소가죽", "양가죽", "송아지가죽", "염소가죽", "말가죽", "인조가죽"}
# vocab_aliases 에 없는 이름 — 혼용률에 실제로 적힌 꼴만(2026-10-02 전수에서 본 것).
# 「깃털」은 vocab 이 다운으로 묶지만 「다운 80% 깃털 20%」는 둘을 갈라야 비율이 산다.
# 「모」는 「모 100」(ader-error 의 울)처럼 한 낱말로 설 때만 덩이가 된다(낱말 단위로 보므로 「모자」는 안 걸린다).
_MIX_ALIAS = {"모": "울", "양모": "울", "merino": "울", "lambswool": "울", "램스울": "울", "메리노울": "울",
              "polyamide": "나일론", "폴리아미드": "나일론", "elastan": "스판덱스", "엘라스테인": "스판덱스",
              "elasthane": "스판덱스", "pu": "스판덱스", "polyeurethane": "스판덱스", "polyurethan": "스판덱스", "쿠프로": "큐프라", "cuprammonium": "큐프라",
              "feather": "깃털", "feathers": "깃털", "깃털": "깃털", "페더": "깃털", "duck feather": "깃털",
              "goose feather": "깃털", "down": "다운", "솜털": "다운", "오리털": "다운", "거위털": "다운",
              "angora": "앙고라", "앙고라": "앙고라", "yak": "야크", "야크": "야크",
              "cow leather": "소가죽", "lamb leather": "양가죽", "sheep leather": "양가죽", "goat leather": "염소가죽",
              "sheep skin": "양가죽", "lamb skin": "양가죽", "cow skin": "소가죽", "goat skin": "염소가죽",
              "viscos": "레이온",       # 「Linning - Viscos 52%」(taille) · 「Viscos 26%」(eenk) — 오타
              # 2차(2026-10-02): mat 없는 판매중 의류에서 「낱말 NN%」로 남은 낱말 상위(diag2). 뜻이 하나인 철자 · 상표만 —
              # 매장 글에도 그대로 적힌다(etmon 「안감:POLYESRER100%」). 웰론 · 기타(other) · metal · camel · ramie 는 모르는 채로 둔다.
              "polyesrer": "폴리에스터", "polyster": "폴리에스터", "polyeter": "폴리에스터", "polyetser": "폴리에스터",
              "polyseter": "폴리에스터", "polyaster": "폴리에스터", "polyesier": "폴리에스터", "polyuretane": "스판덱스",
              "polyuretan": "스판덱스", "polyurehtane": "스판덱스", "polyurethan": "스판덱스", "acylic": "아크릴",
              "polyamid": "나일론", "elastin": "스판덱스", "엘라스틴": "스판덱스", "lycra": "스판덱스", "라이크라": "스판덱스",
              "bemberg": "큐프라", "벰베르크": "큐프라", "라이오셀": "텐셀", "ecovero": "레이온", "coulton": "코튼",
              "오리솜털": "다운", "거위솜털": "다운", "오리깃털": "깃털", "거위깃털": "깃털", "duckdown": "다운", "goosedown": "다운",
              "합성가죽": "인조가죽",
              # 3차(2026-10-10): 혼용률 없는 판매중 옷의 그림 글에서 「낱말 NN%」로 남은 것 — 뜻이 하나인 것만.
              # 웰론 · 테트론 · PBT 는 폴리에스터 섬유 상표 · 종류, 대나무는 대나무 레이온, 모다크릴은 아크릴 계열이다.
              # 그림 판독 오타: 「ACRYUC」(lartisan 29) · 「RYAON」 · 「SLIK」 · 「SAPN」. 웰론을 모르는 채로 두었더니
              # 「FILLER-WELLON 100%」 한 줄 때문에 겉감 · 안감까지 통째로 버려졌다(nick-nicole 패딩).
              "wellon": "폴리에스터", "웰론": "폴리에스터", "tetron": "폴리에스터", "테트론": "폴리에스터", "pbt": "폴리에스터",
              "bamboo": "레이온", "뱀부": "레이온", "modacrylic": "아크릴", "모다크릴": "아크릴",
              "acryuc": "아크릴", "ryaon": "레이온", "slik": "실크", "sapn": "스판덱스"}
# 그림 글에서만 쓰는 철자 맞추기 — 판독기는 글자 하나를 자주 바꾼다(「POLYESIER」 siyazu · 「ravon」 「acrvlic」 「polvester」
# generalidea). 아래 영문 섬유 이름 가운데 **하나만** 편집 거리 1(아홉 글자 넘으면 2) 안에 있을 때만 그 섬유로 본다. 「model」은
# modal 과 한 글자 차이지만 「MODEL 175cm」의 그 낱말이라 뺀다.
_MIX_FUZZY = {"polyester": "폴리에스터", "polyurethane": "스판덱스", "cotton": "코튼", "nylon": "나일론", "rayon": "레이온",
              "acrylic": "아크릴", "spandex": "스판덱스", "viscose": "레이온", "elastane": "스판덱스", "cashmere": "캐시미어",
              "mohair": "모헤어", "alpaca": "알파카", "modal": "모달", "tencel": "텐셀", "lyocell": "텐셀", "linen": "린넨",
              "polyamide": "나일론", "cupro": "큐프라", "acetate": "아세테이트", "angora": "앙고라"}
_MIX_FUZZY_STOP = {"model", "models", "ramon", "angola", "nation", "lining", "liner", "lines", "modern", "motel", "crayon", "cotto", "nylons"}


def _edit1(a: str, b: str, k: int) -> bool:
    """a 와 b 의 편집 거리가 k 이하인가(짧은 낱말용)."""
    if abs(len(a) - len(b)) > k:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
        if min(prev) > k:
            return False
    return prev[-1] <= k


def _mix_fuzzy(w: str) -> str | None:
    w = w.lower()
    if len(w) < 5 or w in _MIX_FUZZY_STOP or not w.isascii():
        return None
    hit = {f for name, f in _MIX_FUZZY.items() if _edit1(w, name, 2 if len(name) >= 9 else 1)}
    return hit.pop() if len(hit) == 1 else None
# 약자 혼용률 — 「MATERIAL : P 80% / R 20%」(not4nerd) · 「원단 혼용률: C 75 P 25」(일류). tag_items._ABBR 와 같은 표다.
# 대문자 한두 글자가 섬유인 것은 이름표 바로 뒤의 줄기에서만 받는다(아무 데서나 받으면 「S 5%」 · 품번이 섬유가 된다).
_MIX_ABBR = {"C": "코튼", "CO": "코튼", "P": "폴리에스터", "PE": "폴리에스터", "PL": "폴리에스터", "PO": "폴리에스터", "R": "레이온", "RA": "레이온",
             "MD": "모달", "MO": "모달", "SP": "스판덱스", "SD": "스판덱스", "PU": "스판덱스", "N": "나일론", "NY": "나일론",
             "W": "울", "WO": "울", "L": "린넨", "LI": "린넨", "AC": "아크릴", "CA": "캐시미어", "CS": "캐시미어",
             "SI": "실크", "SK": "실크", "TE": "텐셀", "LY": "텐셀"}
# 섬유 앞 꾸밈말 — 줄기 안에 서도 된다(「SUPIMA Cotton 65%」 「Recycled Polyester 50%」).
_MIX_MOD = {"organic", "recycled", "recycle", "supima", "pima", "combed", "merino", "lambs", "virgin", "new", "pure",
            "extra", "extrafine", "fine", "superfine", "super", "mercerized", "australian", "italian", "premium",
            "brushed", "duck", "goose", "white", "grey", "gray", "mulberry", "baby", "cow", "lamb", "sheep", "pig", "hog",
            "오가닉", "리사이클", "재생", "수피마", "피마", "메리노", "램스", "유기농", "친환경", "콤브", "콤드",
            # 「구스 다운 80%, 구스 페더 20%」(we11done) — 다운 · 깃털 앞의 새 이름
            "구스", "덕", "화이트", "그레이",
            # 「Tasmania wool 90%」(facade-pattern) · 「KID MOHAIR 27%」(noice) · 「RE-Polyester 5%」(goen-j)
            "tasmania", "tasmanian", "kid", "re", "geelong", "egyptian", "sea", "island", "long", "staple"}
# 「Outer Fabric - Nylon 100% - Inner Fabric - Polyester 100%」(espionage) · 「BODY FILL : DUCK DOWN 90%」(grove) — 부위 + fabric/fill 은
# 한 이름표로 먼저 잡는다(안 그러면 fabric 이 소재 이름표로 읽혀 줄기가 끊기고, body 가 겉감이 된다).
_MIX_PART = (r"(?:outer|inner|main|shell|lining|body|sub)[ \t]?fabric|body[ \t]?fill|(?:panel[ \t])?colou?r[ \t]?block(?:ing)?|"
             r"겉감|안감|배색감|배색|충전재|충전|시보리|본판|몸판|메인|주머니감|포켓감|outer\s?shell|outshell|shell|outer|upper|"
             r"body|main|lining|linning|liner|inner|trim|contrast|colou?ring|sub|partly|filling|filler|fill|padding|wadding|"
             r"ribbing|rib|pocketing|pocket|colou?ration|belt|벨트")
_MIX_PART_RX = re.compile(rf"(?i)(?<![A-Za-z가-힣])({_MIX_PART})(?:[ _]?(\d))?(?![A-Za-z가-힣])")
_MIX_PART_CANON = {"겉감": "겉감", "본판": "겉감", "몸판": "겉감", "메인": "겉감", "outer shell": "겉감",
                   "outershell": "겉감", "outshell": "겉감", "shell": "겉감", "outer": "겉감", "body": "겉감",
                   "main": "겉감", "안감": "안감", "lining": "안감", "inner": "안감", "배색": "배색", "배색감": "배색",
                   "trim": "배색", "contrast": "배색", "sub": "배색", "충전재": "충전재", "충전": "충전재",
                   "filling": "충전재", "filler": "충전재", "padding": "충전재", "wadding": "충전재",
                   "시보리": "시보리", "rib": "시보리", "ribbing": "시보리", "pocketing": "포켓감", "주머니감": "포켓감",
                   "포켓감": "포켓감", "upper": "겉감", "coloring": "배색", "colouring": "배색", "linning": "안감", "liner": "안감", "coloration": "배색", "colouration": "배색", "belt": "벨트", "벨트": "벨트", "partly": "부분", "pocket": "포켓감",
                   "fill": "충전재", "body fill": "충전재", "bodyfill": "충전재", "outer fabric": "겉감", "outerfabric": "겉감",
                   "main fabric": "겉감", "mainfabric": "겉감", "shell fabric": "겉감", "shellfabric": "겉감",
                   "body fabric": "겉감", "bodyfabric": "겉감", "inner fabric": "안감", "innerfabric": "안감",
                   "lining fabric": "안감", "liningfabric": "안감", "sub fabric": "배색", "subfabric": "배색",
                   "panel color blocking": "배색", "panel colour blocking": "배색", "color blocking": "배색",
                   "colour blocking": "배색", "color block": "배색", "colorblocking": "배색"}
_MIX_CTX = (r"fabrics?|materials?|composition|소재\s?정보|제품\s?소재|소재|혼용률|혼용|원단|섬유\s?조성|섬유의\s?조성|"
            r"패브릭\s?정보|패브릭")
_MIX_CTX_END = re.compile(rf"(?i)(?<![A-Za-z가-힣])(?:{_MIX_CTX})(?:[ _]?\d)?$")
_MIX_PART_END = re.compile(rf"(?i)(?<![A-Za-z가-힣])({_MIX_PART})(?:[ _]?(\d))?$")
_MIX_SEP = " \t\n,/+&|;:：-–—()[].·•▪*_"
_MIX_NUM = re.compile(r"(?<![\d.,])(\d{1,5}(?:\.\d{1,2})?)(?!\d)(?:(\s?[%％])|([XxoO○°])(?![A-Za-z가-힣]))?")
# 숫자 뒤가 단위면 혼용률이 아니다 — 「면 30수」 「COTTON 20'S」 「나일론 66」 뒤의 숫자, 「12oz」 …
_MIX_UNIT = re.compile(r"(?i)\s*(?:cm|mm|kg|g\b|수|s\b|['’]s|oz|d\b|gg|게이지|데니어|cc|ml|원|krw|만|ea|개|벌|장|호|년|월|일|"
                       r"세|도|°c|x\s?\d|\*\s?\d|[.,]\d|/\s?\d|~|-\s?\d)")
# 섬유 이름 뒤 괄호 속 다른 말 — 「폴리에스터 (Polyester) 100%」(carlyn) · 「Tencel(Lyocell) 33%」(espionage) · 「VISCOSE(RAYON) 29%」(noice)
_MIX_WORDS_BEFORE = re.compile(r"([A-Za-z가-힣]+(?:[ \t\-(]+[A-Za-z가-힣]+){0,4})\)?[ \t]*[:：\-]?[ \t]*$")
_MIX_WORDS_AFTER = re.compile(r"[ \t]*([A-Za-z가-힣]+(?:[ \t\-]+[A-Za-z가-힣]+){0,3})")


def _mix_fiber(words: list[str], abbr: bool = False, ocr: bool = False) -> str | None:
    """낱말 묶음(그대로의 차례) 하나가 통째로 아는 섬유 이름인가 → 표준 이름. abbr=True 면 대문자 약자도,
    ocr=True 면 낱말 하나의 철자 오독도(_MIX_FUZZY)."""
    if abbr and len(words) == 1 and words[0] in _MIX_ABBR:
        return _MIX_ABBR[words[0]]
    s = " ".join(w.lower() for w in words)
    if _MIX_PART_RX.fullmatch(s):
        return None                   # 「충전재」는 vocab 에서 다운의 별칭이지만 여기서는 부위 이름이다(「… 100% 충전재: 하스 솜」)
    if s in _MIX_ALIAS:
        return _MIX_ALIAS[s]
    if not _FIBER_CANON:
        _fiber("")                    # 사전 채우기
    f = _FIBER_CANON.get(s)
    if f in _MIX_FIBERS:
        return f
    return _mix_fuzzy(s) if ocr and len(words) == 1 else None


def _mix_values(num: str, explicit: bool, letter: bool, ocr: bool) -> tuple[list[float], bool]:
    """숫자 하나의 해석들 → (후보 값들, 오독이 확실한가). 확실 = 그대로는 100 을 넘어 % 오독 말고는 읽을 길이 없다."""
    v = float(num)
    if explicit or letter:
        return ([v] if 0 < v <= 100 else []), letter
    out = [v] if 0 < v <= 100 else []
    if ocr and "." not in num:
        for tail in ("9", "96"):
            if num.endswith(tail) and len(num) > len(tail):
                w = float(num[:-len(tail)])
                if 0 < w <= 100 and w not in out:
                    out.append(w)
    return out, bool(out) and v > 100


def _mix_atoms(text: str, ocr: bool, num_first: bool) -> list[dict]:
    """「섬유 숫자」(num_first=False) 또는 「숫자% 섬유」 덩이들.
    s = 덩이가 시작하는 자리(꾸밈말 포함), e = 끝난 자리, f = 섬유, c = 숫자 해석 후보들,
    x = % 가 적혔나, sure = % 오독이 확실한가(그대로는 100 을 넘는다 · 글자 꼬리)."""
    atoms = []
    for m in _MIX_NUM.finditer(text):
        num, pct, letter = m.group(1), m.group(2), m.group(3)
        if num_first:
            if not pct:
                continue
            a = _MIX_WORDS_AFTER.match(text, m.end())
            if not a:
                continue
            toks = list(re.finditer(r"[A-Za-z가-힣]+", a.group(1)))
            i0 = 0
            while i0 < len(toks) - 1 and toks[i0].group().lower() in _MIX_MOD:
                i0 += 1
            f = None
            for k in range(min(3, len(toks) - i0), 0, -1):
                f = _mix_fiber([t.group() for t in toks[i0:i0 + k]], ocr=ocr)
                if f:
                    break
            if not f:
                continue
            cands, _ = _mix_values(num, True, False, ocr)
            if cands:
                atoms.append({"s": m.start(), "e": a.start(1) + toks[i0 + k - 1].end(), "f": f, "c": cands,
                              "x": True, "sure": False})
            continue
        if letter and not ocr:
            continue                  # 매장 글의 「100X」는 오독이 아니라 다른 말이다
        if not pct and not letter and _MIX_UNIT.match(text, m.end()):
            continue                  # 단위가 붙은 숫자 — 「면 30수」 「20'S」
        if not pct and not letter and re.match(r"[ \t]+\d{1,3}(?:\.\d+)?[ \t]?%", text[m.end():m.end() + 9]):
            continue                  # 바로 뒤에 또 숫자 — 「COTTON 2 100%」의 2(깨진 글자) · 「NYLON 66 100%」
        if not pct and not letter and re.match(r"[A-Za-z]", text[m.end():m.end() + 1]):
            continue                  # 글자가 바로 붙은 숫자는 낱말의 일부다 — 「COTTON 3E 100%」의 3(깨진 「코튼」) · 「2way」
        j = m.start() - 1                 # 빠른 거름: 숫자 바로 앞(빈칸 · 쌍점 건너)이 글자가 아니면 덩이가 아니다
        while j >= 0 and text[j] in " \t:：-)":
            j -= 1
        if j < 0 or not (text[j].isalpha() or (ocr and pct and text[j].isdigit())):
            continue
        b = _MIX_WORDS_BEFORE.search(text, max(0, m.start() - 80), m.start())
        toks = list(re.finditer(r"[A-Za-z가-힣]+", b.group(1))) if b else []
        f, k, ab = None, 0, False
        for k in range(min(3, len(toks)), 0, -1):
            f = _mix_fiber([t.group() for t in toks[-k:]], ocr=ocr)
            if f:
                break
        if not f and ocr:
            # 그림 글 — 영문 섬유 이름과 숫자 사이에 한글 이름이 깨진 토막 하나(「COTTON 3E 100%」 「COTTON SE 94%」 mardi-mercredi).
            # 토막은 세 글자 이하 · 숫자만은 아님 · 섬유도 부위 이름도 아닐 때만 건너뛴다.
            jm = re.search(r"([A-Za-z]{4,})[ \t]+([A-Za-z0-9가-힣]{1,3})[ \t]*[:：]?[ \t]*$", text[max(0, m.start() - 40):m.start()])
            # 숫자만인 토막은 % 가 적힌 숫자 앞의 한 자리일 때만 — 「COTTON 2 100%」(mardi-mercredi, 「코튼」이 「2」로 깨짐)
            if jm and (not jm.group(2).isdigit() or (len(jm.group(2)) == 1 and pct)) and not _MIX_PART_RX.fullmatch(jm.group(2)) \
                    and not _mix_fiber([jm.group(2)]) and _mix_fiber([jm.group(1)], ocr=True):
                f = _mix_fiber([jm.group(1)], ocr=True)
                cands, sure = _mix_values(num, bool(pct), bool(letter), ocr)
                if cands:
                    atoms.append({"s": max(0, m.start() - 40) + jm.start(1), "e": m.end(), "f": f, "c": cands,
                                  "x": bool(pct), "sure": sure or bool(letter), "ab": False})
                continue
        if not f and not toks:
            continue
        if not f:
            # 약자는 이름표 바로 뒤이거나 바로 앞(6자 안)이 약자 덩이일 때만 — 그림 글의 치수표 「L 64 43 37」 「S 48」이
            # 린넨 · 실크 덩이가 되어, 옆의 진짜 혼용률과 붙은 토막으로 보여 함께 버려졌다.
            pos = b.start(1) + toks[-1].start()
            prev = atoms[-1] if atoms else None
            if _mix_lookback(text, pos)[0] or (prev and prev.get("ab") and pos - prev["e"] <= 6):
                k, f, ab = 1, _mix_fiber([toks[-1].group()], abbr=True), True
        if not f:
            continue
        j = len(toks) - k
        # 「Sheepskin Leather 100%」 — 끝 낱말(leather)은 그냥 가죽이고 앞 낱말이 더 자세하다(facade-pattern). 앞이 가죽의 한 갈래면 그걸 쓴다.
        if f == "가죽" and j > 0 and (_mix_fiber([toks[j - 1].group()]) or "").endswith("가죽"):
            f = _mix_fiber([toks[j - 1].group()])
            j -= 1
        # 꾸밈말과 **같은 섬유의 다른 말**은 이름에 넣는다 — 「POLYESTER 폴리에스터 100%」 「COTTON 면 35%」(mardi-mercredi 그림 글).
        while j > 0 and (toks[j - 1].group().lower() in _MIX_MOD or _mix_fiber([toks[j - 1].group()], ocr=ocr) == f):
            j -= 1
        cands, sure = _mix_values(num, bool(pct), bool(letter), ocr)
        if not cands:
            continue
        atoms.append({"s": b.start(1) + toks[j].start(), "e": m.end(), "f": f, "c": cands, "x": bool(pct),
                      "sure": (sure or bool(letter)) and not ab, "ab": ab})
    return atoms


# 부위 이름 뒤 괄호 풀이 — 「배색(소매, 밑단 시보리) :」 「겉감(SHELL):」 「겉감2 (립부) :」(satur · kirsh). 숫자 없는 짧은 괄호만.
_MIX_PAREN_RX = re.compile(r"[(\[（]([^()\[\]（）\d%]{1,24})[)\]）]")


class _MixParen:
    """괄호를 지우되 안이 부위 · 소재 이름표 하나면(「[Main]」 「(SHELL)」 「[Lining]」, ader-error · kirsh) 괄호만 벗긴다."""
    @staticmethod
    def sub(repl: str, t: str) -> str:
        def f(m):
            inner = m.group(1).strip()
            if _MIX_PART_RX.fullmatch(inner) or re.fullmatch(rf"(?i)(?:{_MIX_CTX})", inner):
                return f" {inner} "
            return repl
        return _MIX_PAREN_RX.sub(f, t)


_MIX_PAREN = _MixParen()


def _mix_part_name(m: re.Match) -> str:
    return _MIX_PART_CANON.get(re.sub(r"\s+", " ", m.group(1).lower()), m.group(1)) + (f" {m.group(2)}" if m.group(2) else "")


# 「FABRIC :\nM -COTTON 100%\nC -NYLON 100%」(merely-made — M = main, C = contrast, PR 리뷰 011 표본 14). 한 글자 이름표라
# 대문자 그대로 · 「글자 -섬유」 꼴로 · 한 글에 M 과 C 가 다 있을 때만 부위로 읽는다(_mix_runs 가 정한다).
_MIX_MC = re.compile(r"(?<![A-Za-z])([MC])[ \t]?-[ \t]*$")
_MIX_MC_NAME = {"M": "겉감", "C": "배색"}


def _mix_gap(g: str, fiber: str | None = None, mc: bool = False) -> tuple[bool, str | None]:
    """두 덩이 사이 글이 줄기 안의 것인가 → (괜찮은가, 새 부위 이름).

    부위 이름은 **첫 것**을 쓴다(괄호 풀이 안의 「시보리」가 「배색」을 덮지 않게 괄호부터 지운다).
    소재 이름표(FABRIC · 소재 …)가 끼면 새 글이다 — thugclub 은 「Fabric: SHELL: COTTON 100% … 소재: SHELL: COTTON 100%」로
    같은 혼용률을 두 번 적어 한 줄기로 읽으면 겉감이 둘이 됐다."""
    if len(g) > 50 or g.count("\n") > 2:
        return False, None
    if mc:
        m = re.fullmatch(r"[\s,/]*(?<![A-Za-z])([MC])[ \t]?-[ \t]*", g)
        if m:
            return True, _MIX_MC_NAME[m.group(1)]
    g = _MIX_PAREN.sub(" ", g)
    m = _MIX_PART_RX.search(g)
    part = _mix_part_name(m) if m else None
    rest = _MIX_PART_RX.sub(" ", g)
    if re.search(rf"(?i)(?<![A-Za-z가-힣])(?:{_MIX_CTX})(?![A-Za-z가-힣])", rest):
        return False, None
    rest = re.sub(r"(?i)(?<![A-Za-z가-힣])(?:and|및|외)(?![A-Za-z가-힣])", " ", rest)
    for w in re.findall(r"[A-Za-z가-힣]+", rest):
        # 꾸밈말, 그리고 바로 뒤 덩이와 같은 섬유를 미리 적은 말(「Filling - Duck down (Down 80% Feather 20%)」 uniform-bridge)
        if w.lower() not in _MIX_MOD and not (fiber and _mix_fiber([w]) == fiber):
            return False, None
    if re.search(r"\d", rest):
        return False, None
    return True, part


def _mix_lookback(text: str, s: int, mc: bool = False) -> tuple[bool, str | None]:
    """줄기 첫 덩이 바로 앞의 이름표 → (이름표가 있나, 부위 이름)."""
    if mc:
        m = _MIX_MC.search(text[max(0, s - 6):s])
        if m:
            return True, _MIX_MC_NAME[m.group(1)]
    t = text[max(0, s - 50):s].rstrip(_MIX_SEP)
    t = _MIX_PAREN.sub(" ", t).rstrip(_MIX_SEP)
    part, ctx = None, False
    m = _MIX_PART_END.search(t)
    if m:
        part = _mix_part_name(m)
        t = _MIX_PAREN.sub(" ", t[:m.start()]).rstrip(_MIX_SEP)
    if _MIX_CTX_END.search(t):
        ctx = True
    return ctx or part is not None, part


def _mix_residue(text: str, s: int, e: int, used: list[tuple[int, int]], ocr: bool = False) -> bool:
    """줄기 바로 앞뒤에 덜 읽은 혼용률이 남았나 — 부위 이름 뒤 숫자, 아무 덩이에도 안 든 「NN%」.

    「Fabric. 아크릴 28% 폴리에스터 28% 스판 21% 모달 18% S5%」(generalidea) — 판독기가 「PU 5%」를 「S5%」로 깨뜨리면
    나머지 넷의 합 95 가 blend 의 90~110 에 들었다. 깨진 덩이가 바로 옆에 있으면 덜 읽은 것이다."""
    def free(a: int, b: int) -> bool:
        for m in re.finditer(r"\d\s?%", text[a:b]):
            p = a + m.start()
            if not any(x <= p < y for x, y in used):
                return True
        if ocr:
            # 그림 글에서는 % 가 9 · 96 으로 깨진 숫자도 덜 읽은 덩이다 — 「제품 소재 겉나일론10096 안:폴리에스터1009」(concepts1one)에서
            # 「겉나일론」을 못 읽고 뒤의 「폴리에스터 100」만 옷 전체의 혼용률로 낼 뻔했다.
            for m in re.finditer(r"(?<=[가-힣A-Za-z])[ :：]?(\d{3,5})(?!\d)", text[a:b]):
                n, p = m.group(1), a + m.start(1)
                if any(x <= p < y for x, y in used) or int(n) <= 100:
                    continue
                if (n.endswith("9") and 0 < int(n[:-1]) <= 100) or (n.endswith("96") and 0 < int(n[:-2]) <= 100):
                    return True
        return False
    if free(e, e + 25) or free(max(0, s - 25), s):
        return True
    after = text[e:e + 30]
    m = re.match(rf"(?i)^[{re.escape(_MIX_SEP)}]*(?:{_MIX_PART})(?:[ _]?\d)?(?![A-Za-z가-힣])", _MIX_PAREN.sub(" ", after))
    if m:
        # 부위 이름 뒤에 단위 없는 숫자가 오면 그 부위의 혼용률을 못 읽은 것이다. 「충전재: 하스 솜 2oz」는 무게라 괜찮다
        # (afterprayshop 1331).
        for n in re.finditer(r"(?<![\d.])\d{1,5}(?:\.\d+)?(?![\d])", text[e + m.end():e + m.end() + 25]):
            if not _MIX_UNIT.match(text, e + m.end() + n.end()):
                return True
    return False


def _mix_runs(text: str, ocr: bool, num_first: bool) -> tuple[list, int]:
    """줄기들을 검산해 → ([(결과, 이름표가 있었나, (시작, 끝))], 걸렸지만 버린 줄기 수)."""
    atoms = _mix_atoms(text, ocr, num_first)
    mc = bool(re.search(r"(?<![A-Za-z])M[ \t]?-[ \t]*[A-Z]", text) and re.search(r"(?<![A-Za-z])C[ \t]?-[ \t]*[A-Z]", text))
    runs, cur = [], []
    for a in atoms:
        if cur and a["s"] >= cur[-1]["e"]:
            ok, part = _mix_gap(text[cur[-1]["e"]:a["s"]], a["f"], mc)
            if ok:
                a["part"] = part
                cur.append(a)
                continue
        if cur:
            runs.append(cur)
        a["part"] = None
        cur = [a]
    if cur:
        runs.append(cur)
    used = [(a["s"], a["e"]) for a in atoms]
    res = []
    for run in runs:
        res.append(_mix_check(text, run, used, ocr, mc))
    if ocr:
        _mix_columns(text, runs, res, used)
    # 25자 안에 붙은 두 줄기는 한 혼용률의 토막이다 — 같은 것을 되풀이한 것(「Fabric: … 소재: …」 thugclub)만 둔다.
    # satur 「겉감 : 면 100% / 배색(소매, 밑단 시보리) : 면 95% …」를 괄호를 몰랐을 때 뒤 토막만 읽은 것이 이 꼴이었다.
    cond = [r == "IF" for r in res]
    res = [None if r == "IF" else r for r in res]
    # 줄기 바로 앞이 모르는 이름표(「webbing :」 「Insole :」)면 그 부위를 못 읽은 것이다 — 이웃 줄기와 「같은 말」로 봐 주지 않는다
    # (horlisun 「outshell : nylon100% lining : nylon100% webbing : nylon100%」).
    unk = [bool(re.search(r"([A-Za-z가-힣]{2,})[ \t]*[:：][ \t]*$", text[max(0, r[0]["s"] - 20):r[0]["s"]]))
           and not _mix_lookback(text, r[0]["s"], mc)[0] for r in runs]

    def agree(x, y) -> bool:
        # 부위 이름을 뺀 값이 한쪽에 다 들어가면 같은 혼용률을 두 말로 적은 것이다 — generalidea 「겉감 : 폴리에스터 100%
        # 안감 : 폴리에스터 100% fabric : polyester 100% lining : polyester 100%」(영문 쪽은 겉감 이름이 「fabric」뿐이다).
        return _mix_sub(x[0], y[0]) or _mix_sub(y[0], x[0])
    for i in range(len(runs) - 1):
        if runs[i + 1][0]["s"] - runs[i][-1]["e"] < 25 and (res[i] is None or res[i + 1] is None or unk[i] or unk[i + 1]
                                                           or not agree(res[i], res[i + 1])):
            for j in (i, i + 1):
                if res[j] is not None:
                    _mix_why("붙은 토막", runs[j])
            res[i] = res[i + 1] = None
    good = [r for r in res if r]
    # 버린 줄기에 적힌 부위 이름 — 고른 혼용률에 없는 부위가 여기 있으면 그 옷의 부위 하나를 못 읽은 것이다(blend_of_brand).
    # tillidie 2645 는 그림 글에 「코튼 1009 소재를 이용한 오버롤」(문장)과 「소재 #2 COTTON 100 / 배색 POLYESTER 80 COTTON 20」
    # (표 — 이름표가 판독 찌꺼기 「#2」에 가려 못 읽음)이 같이 있다. 문장만 받으면 배색이 없는 옷이 된다.
    #
    # 섬유도 남긴다(% 가 적힌 덩이만) — 다른 출처의 결과가 이 섬유를 하나도 안 담으면 어긋나는 것이다(blend_of_brand ⑤).
    # atlm 1608 은 설명이 「Nylon 100% / Cow leather / Twill Cotton 100%」(가죽에 % 가 없어 떨어짐), 그림 글은 안감 줄
    # 「Twill cotton 100%」뿐이라 가방 전체가 「코튼 100%」가 될 뻔했다. 숫자를 뺀 글을 열쇠로 남겨 매장 견본을 가린다.
    lost = {"parts": set(), "ev": []}
    for run, r, c in zip(runs, res, cond):
        if r is None and not c:
            part0 = _mix_lookback(text, run[0]["s"])[1]
            lost["parts"] |= {x for x in [part0] + [a.get("part") for a in run] if x}
            fib = frozenset(a["f"] for a in run if a["x"] and not a.get("ab"))
            if fib:
                key = re.sub(r"\d+", "#", re.sub(r"\s+", " ", text[run[0]["s"]:run[-1]["e"]])).lower()
                lost["ev"].append((fib, key))
    return good, len(res) - len(good), lost


# 「※ COTTON 100% 소재의 경우 수축 · 변형 …」(nothing-written 세탁 안내) — 그 상품의 혼용률이 아니라 조건 문장이다.
# 「- 앙고라 100% 원단보다 다른 섬유와 혼방하여 …」(ronron 소재 안내 그림) — 견주는 문장도 그 옷의 혼용률이 아니다.
_MIX_IF = re.compile(r"\s*(?:소재|제품|원단|의류|섬유)?\s*(?:의|인|일|이)?\s*(?:경우|제품은|이상|미만|이하|초과|보다|대비|처럼|과\s?같은|와\s?같은)")


# 전후 대조 · 실패 까닭 세기용 — 리스트를 넣어 두면 버린 줄기마다 (까닭, 시작, 끝)을 적는다. 평소에는 None.
_MIX_TRACE: list | None = None


def _mix_why(why: str, run: list[dict]) -> None:
    if _MIX_TRACE is not None:
        _MIX_TRACE.append((why, run[0]["s"], run[-1]["e"]))


def _mix_unrepeat(run: list[dict], ocr: bool) -> list[dict]:
    """같은 혼용률을 한 줄기 안에서 되풀이한 것을 하나로.

    · 한글 · 영문을 붙여 적은 것 — generalidea 설명 「[FABRIC] 면 60% + 폴리에스터 40% cotton 60% + polyester 40% 새벽배송 …」
      (그 매장 mat 없는 판매중 의류 2,154벌 중 대부분). 덩이 차례가 앞 절반 = 뒤 절반이면 앞 절반만 쓴다. 부위 이름도 같아야 한다.
    · 그림 글이 같은 줄을 두 번 읽은 것 — 「POLYESTER 63%\nPOLYESTER 63%\nRAYON 31%」(siyazu). 섬유 · 숫자가 같은 덩이가
      **잇달아** 서면(그림 글만) 뒤엣것을 뺀다. 합 100 검산은 그대로라 정말로 두 번 적힌 혼용률이면 떨어진다."""
    sig = [(a["f"], tuple(a["c"])) for a in run]
    n = len(run)
    if n >= 2 and n % 2 == 0 and sig[:n // 2] == sig[n // 2:] and run[n // 2].get("part") is None \
            and [a.get("part") for a in run[1:n // 2]] == [a.get("part") for a in run[n // 2 + 1:]]:
        return run[:n // 2]
    if ocr:
        out = [run[0]]
        for a in run[1:]:
            if (a["f"], a["c"]) == (out[-1]["f"], out[-1]["c"]) and a.get("part") is None:
                continue
            out.append(a)
        return out
    return run


# 그림 글의 두 단 — 왼쪽 단의 혼용률 줄 끝에 오른쪽 단 글이 붙는다: 「COTTON 65%, 드라이크리닝 대한민국\nPOLYESTER 35%」(ronron
# FabRic | WasH | MadE in 표) · 「Cotton 64% + 편안한 스트래치\nPolyester 36%」(roem Fabric Point). 줄 끝 찌꺼기 때문에 줄기가
# 갈려 둘 다 합이 안 맞는다. **둘 다 떨어진** 이웃 줄기이고, 사이가 「숫자 없는 줄 꼬리 + 줄바꿈 (+ 부위 이름)」뿐이면 이어 보고,
# 이은 것이 검산을 다 통과할 때만 받는다(그림 글만).
_MIX_COL_GAP = re.compile(rf"(?i)[^\n\d%]{{0,30}}\n[ \t]*(?:({_MIX_PART})(?:[ _]?(\d))?[ \t]*[:：\-]?[ \t]*)?")


def _mix_columns(text: str, runs: list, res: list, used: list) -> None:
    def fits(x, y) -> bool:
        return isinstance(x, tuple) and isinstance(y, tuple) and (_mix_sub(x[0], y[0]) or _mix_sub(y[0], x[0]))
    i = 0
    while i < len(runs) - 1:
        if fits(res[i], res[i + 1]) or "IF" in (res[i], res[i + 1]):
            i += 1
            continue
        merged, j = list(runs[i]), i + 1
        while j < len(runs):
            m = _MIX_COL_GAP.fullmatch(text, merged[-1]["e"], runs[j][0]["s"])
            if not m:
                break
            part = _mix_part_name(m) if m.group(1) else None
            merged = merged + [dict(runs[j][0], part=part)] + runs[j][1:]
            r = _mix_check(text, merged, used, True)
            if r not in (None, "IF"):
                runs[i:j + 1] = [merged]
                res[i:j + 1] = [r]
                break
            j += 1
        i += 1


def _mix_check(text: str, run: list[dict], used: list, ocr: bool = False, mc: bool = False) -> tuple | None:
    """줄기 하나 검산 → (결과, 이름표가 있었나, (시작, 끝)) 또는 None."""
    if _MIX_IF.match(text, run[-1]["e"]):
        _mix_why("조건 문장", run)
        return "IF"                     # 조건 문장 — 받지도 않고, 덜 읽은 증거로도 안 센다
    labeled, part0 = _mix_lookback(text, run[0]["s"], mc)
    if _mix_residue(text, run[0]["s"], run[-1]["e"], used, ocr):
        _mix_why("옆 토막", run)
        return None
    run = _mix_unrepeat(run, ocr)
    parts, name = [], part0 or ""
    for a in run:
        if a["part"] is not None or not parts:
            if a["part"] is not None:
                name = a["part"]
            parts.append((name, []))
        parts[-1][1].append(a)
    out, ok = [], True
    for name, aa in parts:
        if len(aa) > 8:
            ok = False; _mix_why("덩이 너무 많음", run); break
        explicit = all(a["x"] for a in aa)
        if not labeled and (any(a.get("ab") for a in aa) or not all(a["x"] or a["sure"] for a in aa)):
            ok = False; _mix_why("이름표 없이 % 없음 · 약자", run); break       # % 없는 숫자 · 약자는 이름표 뒤에서만
        combos = [[]]
        for a in aa:
            combos = [c + [v] for c in combos for v in a["c"]]
        if explicit:
            hit = [c for c in combos if 99 <= sum(c) <= 101]
        else:
            hit = [c for c in combos if abs(sum(c) - 100) < 1e-6]
        if len(hit) != 1:
            ok = False; _mix_why("합 어긋남" if not hit else "해석 여럿", run); break
        # 한 부위에 같은 섬유가 두 번 — 「Recycle Cotton 60% Cotton 40%」(youth) · 「RAYON13% RAYON7%」(noice)는 합이 맞으니 더한다.
        # 두 부위를 붙여 읽은 것(「COTTON 100% - COTTON 70% LINEN 30%」 xlim)은 위 검산(합 100)에서 이미 떨어진다.
        vals: dict[str, float] = {}
        for a, v in zip(aa, hit[0]):
            vals[a["f"]] = vals.get(a["f"], 0) + v
        # 폴리우레탄이 반을 넘으면 늘어나는 실(스판덱스)이 아니라 코팅 · 인조가죽이다(tag_items.blend_fibers 와 같은 잣대).
        # vocab 은 폴리우레탄을 스판덱스로 묶지만 「스판덱스 100%」 레깅스 같은 말을 앱에 띄우지 않게 적힌 이름으로 둔다.
        part = {"p": name, "v": [["폴리우레탄" if f == "스판덱스" and v >= 50 else f,
                                  int(v) if float(v).is_integer() else round(v, 2)] for f, v in vals.items()]}
        same = [q for q in out if q["p"] == name and name]
        if same:
            if same[0]["v"] != part["v"]:
                ok = False; _mix_why("같은 부위 다른 값", run); break   # 같은 부위가 다른 값으로 두 번 — 색마다 다른 혼용률(kirsh 9071 「WHA- 몸판 … NAD - 몸판 …」)
            continue                # 같은 부위를 되풀이해 적은 것(OCR 「*Shell : … *Shell : …」)
        out.append(part)
    if not ok:
        return None
    return out, labeled, (run[0]["s"], run[-1]["e"])


def mix_cands(text: str, ocr: bool = False) -> tuple[list[dict], int, dict]:
    """한 글에서 검산을 통과한 혼용률 후보들 → ([{"m": 결과, "lab": 이름표, "span": (시작, 끝)}], 버린 줄기 수,
    버린 줄기의 흔적 {"parts": 부위 이름들, "ev": [(섬유들, 숫자 뺀 글)]}).

    「섬유 숫자」와 「숫자% 섬유」 두 차례로 다 읽는다. 같은 자리를 두 차례가 다 읽으면 **먼저 시작하는 쪽**이 그 글의
    차례다 — 「Alpaca 59% Acrylic 25% Nylon 15% Spandex 1%」를 숫자 앞 차례로 읽으면 「59% Acrylic …」이 되어 섬유가 한 칸씩
    밀린다(facade-pattern 4038). 고르는 것은 mix_pick 이 한다 — 매장 공용 줄을 먼저 걸러야 해서(tag_items.blend_of_brand)
    둘을 갈랐다."""
    t = re.sub(r"[\u00a0\u200b\ufeff]", " ", text or "").replace("％", "%")
    if not re.search(r"\d", t):
        return [], 0, {"parts": set(), "ev": []}
    got, bad, lost = [], 0, {"parts": set(), "ev": []}
    for nf in (False, True):
        g, b, lp = _mix_runs(t, ocr, nf)
        bad += b
        lost["parts"] |= lp["parts"]
        lost["ev"] += lp["ev"]
        got += [{"m": m, "lab": lab, "span": sp, "nf": nf} for m, lab, sp in g]
    out = []
    for c in got:
        a, b = c["span"]
        if any(d["nf"] != c["nf"] and d["span"][0] < b and a < d["span"][1] and d["span"][0] < a for d in got):
            continue
        out.append({k: v for k, v in c.items() if k != "nf"})
    return out, bad, lost


def _mix_sub(a: list[dict], b: list[dict]) -> bool:
    """a 의 부위가 다 b 에 들어가나 — 값이 같고 부위 이름이 같거나 한쪽이 비었을 때 같은 부위로 본다.
    이름을 아예 안 보면 색마다 다른 혼용률 「WHA- 몸판 폴리 100% 배색1 나일론 100% NAD - 몸판 나일론 100% 배색1 폴리 100%」(kirsh
    9071)의 두 줄기가 같은 것이 된다(값 묶음만 보면 {폴리 100, 나일론 100} 으로 같다)."""
    # 같은 이름의 부위가 b 에 있으면 그것과 값이 같아야 한다. 그런 이름이 b 에 없으면(「배색」 대 「시보리」 — satur 3188 은
    # 설명이 「배색(소매, 밑단 시보리)」, 그림이 「시보리」라 적었다) a 에 없는 이름의 부위와 값으로 맞춘다.
    names_a = {p["p"] for p in a if p["p"]}
    names_b = {q["p"] for q in b if q["p"]}
    left = list(b)
    for p in sorted(a, key=lambda x: x["p"] not in names_b):
        k = _mix_key([p])
        if p["p"] and p["p"] in names_b:
            hit = next((q for q in left if q["p"] == p["p"] and _mix_key([q]) == k), None)
        else:
            hit = next((q for q in left if _mix_key([q]) == k and (not q["p"] or not p["p"] or q["p"] not in names_a)), None)
        if hit is None:
            return False
        left.remove(hit)
    return True


def _mix_key(m: list[dict]) -> frozenset:
    """부위 이름을 뺀 부위들의 값 — 「겉감 코튼 100」과 이름 없는 「코튼 100」은 같은 부위다."""
    return frozenset(json.dumps(sorted([f, float(v)] for f, v in p["v"]), ensure_ascii=False) for p in m)


def mix_pick(cands: list[dict]) -> tuple[list[dict], str]:
    """후보들 → (혼용률, 까닭). 부위가 가장 많은 것을 고르되, 나머지가 **모두 그 부분집합**일 때만 받는다.

    eenk 는 「- 양가죽 100% 소재의 레더 팬츠」(설명 문장)와 「MATERIAL : Shell : Sheep Leather 100% Lining : Polyester 100%」
    (표)를 같이 적는다. 문장 쪽은 겉감만 말한 것이지 틀린 것이 아니다 — 표를 쓴다. 둘이 어긋나면(「BODY - COTTON 100%」 대
    그림의 「BODY COTTON 97% SPAN 3%」, noirer 1709 · 「MATERIAL : Polyester 61% …」 대 고시 「Rayon 50%, Polyester 50%」,
    eenk 6750) 어느 쪽이 맞는지 모르므로 버린다. 같은 부위 수면 이름표가 붙은 쪽, 그다음 먼저 온 쪽(부르는 쪽이 출처 차례로 넣는다).

    한 출처 안에서 부위 이름이 붙은 후보들이 **서로 다른 부위**만 말하면 합친다 — 「*겉감 : Cotton 100% - KUROKI社 from japan
    안감 : Polyester 65% , Cotton 35%」(anotheroffice) · 「Outshell - linen 35% …」 줄과 몇 줄 뒤 「Lining - polyester 100%」
    (label-archive)는 사이 글 때문에 줄기가 갈렸을 뿐 한 혼용률이다. 같은 이름이 둘이면(색마다 · 세트 벌마다) 안 합친다."""
    m, st = _mix_pick(cands)
    # 겉감(또는 이름 없는 부위)이 없으면 그 옷의 혼용률이 아니다 — 「Lining: 56% Cotton, 44% Rayon」만 읽힌 것(khakis)을 앱은
    # 부위가 하나라 이름을 떼고 「코튼 56% · 레이온 44%」로 적는다(layer-web materialMixAxes). 옛 blend 도 이런 것을 684벌 냈다.
    if m and not any(p["p"] == "" or p["p"].startswith("겉감") for p in m):
        return [], "partial"
    return m, st


def _mix_pick(cands: list[dict]) -> tuple[list[dict], str]:
    if not cands:
        return [], "none"
    best = max(range(len(cands)), key=lambda i: (len(cands[i]["m"]), bool(cands[i].get("lab")), -i))
    if all(_mix_sub(c["m"], cands[best]["m"]) for c in cands):
        return cands[best]["m"], "ok"
    srcs = {c.get("src") for c in cands}
    if len(srcs) == 1:
        parts: dict[str, dict] = {}
        for c in cands:
            for p in c["m"]:
                if not p["p"]:
                    return [], "conflict"
                q = parts.get(p["p"])
                if q is not None and q["v"] != p["v"]:
                    return [], "conflict"
                parts[p["p"]] = p
        return list(parts.values()), "ok"
    return [], "conflict"


def mix(text: str, ocr: bool = False) -> tuple[list[dict], str, tuple[int, int] | None]:
    """한 글만 볼 때 — 혼용률(blend 와 같은 모양) · 까닭("ok" · "none" · "conflict" · "bad") · 읽은 자리."""
    cands, bad, lost = mix_cands(text, ocr)
    if not cands:
        return [], ("bad" if bad else "none"), None
    m, st = mix_pick(cands)
    if m and lost["parts"] - {p["p"] for p in m}:
        return [], "partial", None
    if not m:
        return [], st, None
    sp = [c["span"] for c in cands if c["m"] == m] or [(min(c["span"][0] for c in cands), max(c["span"][1] for c in cands))]
    return m, st, sp[0]


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
    r"material|composition|상품\s?설명|제품\s?설명|패브릭\s?정보|소재\s?정보|제품\s?소재|디테일)"
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
    # 렌더링 안 된 카페24 틀(알리스 「{$product_detail}」 · 어랜드 「{#title}」 · 닐비피)은 **지우기만** 한다 —
    # 문단 맨 앞에 오는 일이 많아 _JUNK 처럼 그 자리에서 자르면 뒤의 설명이 통째로 날아간다(코덱스 검증 004).
    text = _TEMPLATE.sub(" ", text or "")
    text = _DESIGNER_HEAD.sub(" ", _LETTERED.sub(" ", _RELATED.sub(" ", text)))
    text = _KO_SIZE_RUN.sub(" ", _EN_SIZE_ROWS.sub(" ", text))
    # 긴 조각은 쉼표에서도 갈라 거른다(버릴 말을 잘게 가려내려고). 그런데 **둘 다 살아남은 이웃 조각**을 줄로 넘기면
    # 한 문장이 쉼표에서 두 특징으로 쪼개진다 — 「… 바지핏 연출 무릎 및 포켓」 / 「후면 패널에 적용된 …」
    # (9999archive-438, 앱 세션 2026-09-25). 거르는 일은 그대로 두고, 살아남은 이웃이면 쉼표로 도로 잇는다.
    last_whole = False           # 바로 앞 조각이 잘리지 않고 그대로 out 끝에 들어갔는가
    for part, comma in _parts(text, with_comma=True):
        join, last_whole = comma and last_whole, False
        if _SECTION_HEAD.match(part) or _SIZE_HEAD.match(part):
            continue
        part = _INLINE_HEAD.sub("", part)
        m = _JUNK.search(part) or _PRICE.search(part)
        cut = bool(m)
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
        if join and out:
            out[-1] += ", " + part
        else:
            out.append(part)
        last_whole = not cut
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
# 「특유의 · 고유의」 안의 「유의」는 안내가 아니다 — 포에토 「메리노 울 특유의 포근하고 …」 소개가 안내로 갔다(코덱스 011).
_NOTE_CUE = re.compile(r"주의|(?<![특고])유의|권장|권해|삼가|피해\s?주|금지|마세요|말아\s?주|양해|부탁|바랍니다|불량|교환|반품|"
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
