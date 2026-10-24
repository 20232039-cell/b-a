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
import html
import json
import re
from pathlib import Path

from detail_from_ocr import FIBER, _HARD, _SOFT, _FIBER_PCT, _HANGUL, materials

DATA = Path(__file__).resolve().parent.parent / "data"

# ─── 조각내기 ───
# HTML 에서 긁은 글은 줄바꿈이 없다 — 한 줄에 수천 자가 붙어 온다.
# 1차: 문장 끝·불릿·세로선. 한국어 문장은 「다/요/죠/함/음」으로 끝난다.
# 「…다·요」 뒤에서 자르되 **말끝**일 때만 — 「…니다」「…어요」「…된다」. 글자 하나(「다」)만 보면 이름씨의
# 끝 글자에서 잘렸다: 「세미 와이드 버뮤다 / 핏 바이오 워싱」(9999archive-429, 앱이 짚음 2026-09-24).
_SPLIT = re.compile(r"\n+|(?:(?<=니다)|(?<=[어아해세에예죠]요)|(?<=[이된한있없했였는]다)|(?<=[죠함음]))\s+|(?<![Ff]rom\.)(?<=[.!?])\s+|"
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
# 반품 불가 예시·축제 배너·재생목록은 소재 문장에 붙어도 그 조각만 뺀다.
_RETURN_EXAMPLE = re.compile(
    r"\(\s*언더웨어\s*,\s*이너웨어\s*,\s*가죽벨트\s*,\s*"
    r"가죽신발등\s+주름이\s+지게\s+되는\s+가죽제품들\s*\)")
_FESTIVAL_BANNER = re.compile(
    r"☼\s*ENTER\s+ETERNITY\s*✦\s*THE\s+PSYCHEDELIC\s+MUSIC\s+FESTIVAL\s+IS\s+ON\s*☼", re.I)
_PLAYLIST_LINK = re.compile(
    r"\bRelated\s+Playlist\s+\d+\s*[/|]\s*https?://[^\s]+", re.I)
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
    r"세탁(?!\s*후\s*수축이\s*적|에도\s*안정적인\s*형태를\s*유지|\s*후에도\s*변형이\s*적어|\s*케어에\s*용이하게\s*제작)|드라이\s?[클크]?리?닝|표백|다림질|건조(?!가\s*빠른)|탈수|드라이어|취급|이염|다리미|찬물|단독\s?손|"
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
    r"누끼|메인\s?컷|상세\s?컷\s?(?:참고|아래)|영상에서\s?확인|"
    # 매장 연락처 · 반품 주소 · 게시판 안내 — lmood 「(11189) 경기 포천시 … 로지스틱스」 · learve 「010-4646-6629」 ·
    # vibrate 「02-549-0059」 · saintpain 「상품 및 주문 관련 문의는 BOARD를 이용해 주세요」(텍스트 QA 2026-10-27, 962편)
    r"\(\d{5}\)\s*[가-힣]|(?<!\d)(?:0\d{1,2}|010)\s?-\s?\d{3,4}\s?-\s?\d{4}(?!\d)|상품\s?및\s?주문|문의는|BOARD\s?를\s?이용|"
    # unionblue 체크표 머리와 재는 법 — 「제품 가이드 실루엣 슬림 레귤러 …」「총장은 … 측정한 치수입니다」
    r"제품\s?가이드|측정한\s?(?:치수|사이즈|길이)|"
    # 반품 · A/S 안내와 쇼핑몰 경로 — nomanual 「물류 센터에 반송된 상품의 …」 552 · vunque 「(가죽 손상의 경우 A/S 불가) … 현재 위치
    # Shop OUTLET BAG」 191 · gongdreen 「… 재화가치가 현저하게 떨어지는 경우」
    r"반송|물류\s?센터|A\s?/\s?S\s?불가|현재\s?위치\s+Shop|재화\s?가치|"
    # 재는 법 문장 — 「총장은 립을 포함한 뒤 중심에서 수직으로 내려서 측정한 치수입니다」(unionblue) · 「기장은 칼라 부분을 제외한 …」
    r"(?:총장|기장|총\s?기장)은\s[^.\n]{0,40}?(?:측정|중심\s?길이|중심에서|제외한|포함한)|"
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
# 쉼표 뒤가 띄어 오기도 한다 — nick-nicole 「58, 000원 40, 600원」 · dunst 「KRW 109, 000」 · lememe 「₩ 138, 000」(가격 789편).
_PRICE = re.compile(r"(?:KRW|₩|won|원)\s?[\d,]{4,}|[\d,]{4,}\s?(?:원|KRW|₩|won)|"
                    r"(?:KRW|₩)\s?\d{1,3}(?:,\s?\d{3})+|\d{1,3}(?:,\s?\d{3})+\s?(?:원|won)\b|\d+원\s?\(\d+%\)|\bPOINT\s\d", re.I)
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
    r"detail|pockets?|zipper|button|collar|sleeve|sleeveless|hem|lining|silhouette|"
    r"oversized?|cropped?|relaxed|slim|wide|tapered|straight|boxy|"
    r"stitch|embroider\w*|knit|ribbed|pleat\w*|panel|layered|seam\w*|"
    r"stretch|lightweight|heavyweight|breathable|water\s?repellent|"
    # PAF 「Crewneck style jersey T-shirt / Poetic graphic featuring …」 — 생김새 낱말이 없어 빠졌다.
    # 품목 이름(t-shirt·jacket…)은 **넣지 않는다** — 넣었더니 상품 이름 줄(「22SS UNAFFECTED FUN BOX T-SHIRT
    # CHARCOAL (해외」)·메뉴(「홈 Collection … softshell jacket」)가 설명으로 들어왔다(전후 전수 587벌).
    r"crew\s?neck|v-?neck|mock\s?neck|jersey|graphic|"
    # crank 「Boots cut line / Low rised corduroy pants」 — 붙은 불릿을 가르자 낱낱이 거르개에 걸렸다(2026-10-27)
    r"corduroy|rised?|low[- ]?rise|high[- ]?rise|boots?\s?cut|flared?|wide[- ]leg)\b", re.I)


# 매장 설명은 불릿으로 쓴다 — 조각이 네댓 글자로 짧다(「소뿔 단추」·「세미 와이드핏」).
# detail_from_ocr 의 _HARD 에는 품목 이름이 없다. 거기서는 일부러 없다 — OCR 잡음 줄에
# 품목 이름이 섞여 들어오면 잡음을 설명으로 세게 된다. 여기(매장이 제 손으로 쓴 글)에서는
# 그 위험이 없으므로 품목·디테일 낱말까지 받는다.
_HARD_ITEM = re.compile(
    r"디테일|포인트|패턴|프린트|트리밍|마감|여밈|셔링|플레어|테이퍼드|스트레이트|박시|"
    r"팬츠|슬랙스|데님|진|쇼츠|스커트|원피스|드레스|자켓|재킷|코트|점퍼|블루종|패딩|"
    r"셔츠|블라우스|니트|가디건|스웨터|티셔츠|맨투맨|후디|후드|베스트|조끼|탑|"
    r"라벨|스티치|워시드|밑위|암홀|카라|칼라|아세테이트|렌즈")


# 잡화의 말 — 벨트·모자·가방·주얼리 설명이 옷 낱말이 없어서 통째로 버려졌다(포스센스티브 벨트
# 「천연 소가죽으로 제작 된 제품입니다」·볼캡 「모자챙이 큰편입니다」, 2026-09-24). 매장이 제 손으로 쓴
# 글(clean)에만 쓴다 — OCR 잡음 줄(from_mined)에는 안 건다(_HARD_ITEM 주석과 같은 까닭).
_HARD_ACC = re.compile(
    r"신으|신을|라스트|앞코|가죽|레더|스웨이드|누벅|캔버스|볼캡|캡|모자|챙|비니|버킷햇|벨트|버클|아일렛|가방|토트|숄더|크로스백|"
    r"스트랩|지갑|카드홀더|목걸이|네크리스|반지|링|귀걸이|이어링|팔찌|브레이슬릿|체인|펜던트|도금|"
    r"\b(?:cap|hat|beanie|belt|buckle|bag|tote|strap|wallet|necklace|ring|earring|bracelet|chain|pendant)s?\b", re.I)


# 매장 글은 「…니다」체다. 「…샀어요」「…좋아요」는 후기다(rssc 「카시오 시계랑 … 따라 샀어요」 · lookast 벨트 후기).
_PCT_FIBER = re.compile(r"\d{1,3}\s?%\s*[A-Za-z가-힣]|[A-Za-z가-힣]\s*\d{1,3}\s?%")
_REVERSE_FIBER_PCT = re.compile(rf"\d{{1,3}}\s*%\s*(?:{FIBER})(?![A-Za-z가-힣])", re.I)
_SENTENCE_END = re.compile(r"(?:니다|이다|한다|된다|있다|없다)\s*[.!]?\s*$")
_ACC_CARE = re.compile(r"불량|하자|현상|주의|보관|세척|세탁|닦|관리|변색|탈락|오염|습기|직사광선|교환|반품|a/?s|수선|"
                       r"염료|이염|벗겨|손상|마찰|화장품|향수|주름|스크래치|자국|반점")


def says_clothes(part: str, acc: bool = False) -> bool:
    """이 조각이 **옷 이야기**를 하는가 — 옷의 성질이거나, 인상이거나, 혼용률이다.
    acc=True 면 잡화(벨트·모자·가방·주얼리)의 말도 받는다 — 매장이 쓴 글에서만."""
    if (_HARD.search(part) or _SOFT.search(part) or _FIBER_PCT.search(part)
            or _HARD_EN.search(part) or _HARD_ITEM.search(part) or _REVERSE_FIBER_PCT.search(part)):
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
                fragment = q[:400] if for_blend else q
                yield (fragment, comma) if with_comma else fragment
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
#   · %가 모두 명시된 줄은 원본 합을 보존한다. 비율을 늘려 100으로 맞추지 않는다.
#     %가 없거나 OCR로 깨졌으면 정확히 100이 되는 해석이 하나뿐일 때만 받는다.
#   · 섬유 이름은 아는 것만(_MIX_FIBERS). 데님 · 니트 · 메쉬는 짜임이지 섬유가 아니다. 웰론(충전솜 상표) ·
#     확인된 기타섬유·금속섬유도 이름 그대로 보존한다. 모르는 원단 상품명은 받지 않는다.
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
              # 매장 글에도 그대로 적힌다(etmon 「안감:POLYESRER100%」).
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
# 저장 원문에서 확인한 섬유명. 가공명(CASH POLY 등)은 아래 꾸밈말로만 읽는다.
_MIX_ALIAS.update({
    "ramie": "라미", "라미": "라미", "camel": "카멜", "카멜": "카멜",
    "raffia": "라피아", "라피아": "라피아", "papyrus": "파피루스", "파피루스": "파피루스",
    "paper": "종이", "종이": "종이", "polyethylene": "폴리에틸렌", "폴리에틸렌": "폴리에틸렌",
    "uhmwpe": "UHMWPE", "metal fiber": "메탈섬유", "fox": "폭스", "폭스": "폭스",
    "alpaca wool": "알파카", "벰버그": "큐프라", "나이론": "나일론",
    "acrtlic": "아크릴", "tencil": "텐셀", "polyurethaene": "스판덱스",
    "arclic": "아크릴", "raccoon": "라쿤", "견": "실크",
    "cow leathrt": "소가죽", "cow skin": "소가죽", "goat skin": "염소가죽",
    "cash": "캐시미어", "others": "기타", "other fibers": "기타", "기타섬유": "기타", "기타": "기타",
    "sheep skin leather": "양가죽", "lambskin suede": "양가죽", "pig skin": "돼지가죽",
    "pig leather": "돼지가죽", "artificial leather": "인조가죽",
    "cow split leather": "소가죽",
    "genuine leather": "천연가죽",
    "lambskin shearling": "양가죽", "raccon": "라쿤", "polyurathan": "스판덱스",
})
# 잡화의 명시된 조성은 섬유와 구별한 이름으로 보존한다. 등급(925)이나 원단 상품명은 넣지 않는다.
_MIX_MATERIAL_ALIAS = {
    "rubber": "고무", "고무": "고무", "synthetic rubber": "합성고무", "합성고무": "합성고무",
    "brass": "황동", "황동": "황동", "surgical steel": "써지컬 스틸", "surgical": "써지컬 스틸",
    "stainless steel": "스테인리스", "스테인레스": "스테인리스", "tin": "주석",
    "uv acrylic": "UV 아크릴",
    "eva": "EVA",
    "tpr": "TPR",
}
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
_MIX_ABBR.update({"CU": "큐프라", "RY": "레이온"})
# 섬유 앞 꾸밈말 — 줄기 안에 서도 된다(「SUPIMA Cotton 65%」 「Recycled Polyester 50%」).
_MIX_MOD = {"organic", "recycled", "recycle", "supima", "pima", "combed", "merino", "lambs", "virgin", "new", "pure",
            "extra", "extrafine", "fine", "superfine", "super", "mercerized", "australian", "italian", "premium",
            "brushed", "duck", "goose", "white", "grey", "gray", "mulberry", "baby", "cow", "lamb", "sheep", "pig", "hog",
            "오가닉", "리사이클", "재생", "수피마", "피마", "메리노", "램스", "유기농", "친환경", "콤브", "콤드",
            # 「구스 다운 80%, 구스 페더 20%」(we11done) — 다운 · 깃털 앞의 새 이름
            "구스", "덕", "화이트", "그레이",
            # 「Tasmania wool 90%」(facade-pattern) · 「KID MOHAIR 27%」(noice) · 「RE-Polyester 5%」(goen-j)
            "tasmania", "tasmanian", "kid", "re", "geelong", "egyptian", "sea", "island", "long", "staple"}
_MIX_MOD.update({"cash", "soft", "washed", "워싱", "헤비"})
# 「Outer Fabric - Nylon 100% - Inner Fabric - Polyester 100%」(espionage) · 「BODY FILL : DUCK DOWN 90%」(grove) — 부위 + fabric/fill 은
# 한 이름표로 먼저 잡는다(안 그러면 fabric 이 소재 이름표로 읽혀 줄기가 끊기고, body 가 겉감이 된다).
_MIX_PART = (r"(?:main|sub)[ \t]+material|fabrics?(?=[ _]?\d)|shell[ \t]+(?:face|back|body|mesh)|(?:outer|inner|main|shell|lining|body|sub)[ \t]?fabric|body[ \t]?fill|(?:panel[ \t])?colou?r[ \t]?block(?:ing)?|"
             r"겉[ \t　]?감|안[ \t　]?감|배[ \t　]?색감|배[ \t　]?색|충전재|충전|시보리|본판|몸판|메인|주머니감|포켓감|outer\s?shell|outshell|shell|outer|upper|"
             r"body|main|lining|linning|liner|inner|trim|contrast|colou?ring|sub|partly|filling|filler|fill|padding|wadding|"
             r"ribbing|rib|pocketing|pocket|colou?ration|belt|벨트|outside|inside|innering|insulation|"
             r"bonded[ \t]+fur|mesh|hardware|strap|harness|muffler|pendant|string|outsole|insole|midsole|"
             r"겉면|이면|갑피|외피|내피|밑창|중창|인솔|주머니|립|top|skirt|scarf|원단|stud")
_MIX_PART_RX = re.compile(rf"(?i)(?<![A-Za-z가-힣])({_MIX_PART})(?:[ _]?([\d①-⑨]))?(?![A-Za-z가-힣])")
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
_MIX_PART_CANON.update({
    "outside": "겉감", "inside": "안감", "innering": "안감", "insulation": "충전재",
    "bonded fur": "합포 퍼", "mesh": "메쉬", "hardware": "금속부속", "strap": "스트랩",
    "harness": "하네스", "muffler": "머플러", "pendant": "펜던트", "string": "끈",
    "outsole": "밑창", "insole": "깔창", "midsole": "중창", "겉면": "겉감", "이면": "안감",
    "갑피": "겉감", "내피": "안감", "밑창": "밑창", "중창": "중창", "주머니": "포켓감", "립": "시보리",
    "top": "상의", "skirt": "스커트", "scarf": "스카프", "원단": "원단",
    "외피": "겉감", "인솔": "깔창", "stud": "스터드",
    "shell face": "겉감 앞면", "shell back": "겉감 뒷면", "shell body": "겉감", "shell mesh": "메쉬",
    "fabric": "원단", "fabrics": "원단",
    "main material": "겉감", "sub material": "배색",
})
_MIX_CTX = (r"fabrics?|materials?|composition|소재\s?정보|제품\s?소재|소재|혼용[률율]|혼용|원단|섬유\s?조성|섬유의\s?조성|"
            r"패브릭\s?정보|패브릭")
_MIX_CTX_END = re.compile(rf"(?i)(?<![A-Za-z가-힣])(?:{_MIX_CTX})(?:[ _]?\d)?$")
_MIX_PART_END = re.compile(rf"(?i)(?<![A-Za-z가-힣])({_MIX_PART})(?:[ _]?([\d①-⑨]))?$")
_MIX_SEP = " \t\n,/+&|;:：-–—()[].·•▪*_\"'"
_MIX_NUM = re.compile(r"(?<![\d.,])(\d{1,5}(?:\.\d{1,2})?)(?!\d)(?:(\s?[%％])|([XxoO○°])(?![A-Za-z가-힣]))?")
# 숫자 뒤가 단위면 혼용률이 아니다 — 「면 30수」 「COTTON 20'S」 「나일론 66」 뒤의 숫자, 「12oz」 …
# 단위는 같은 줄에서만 보고, 한 글자 한글 단위 뒤에 한글이 이어지면 단위가 아니다 — 그림 글이 「소재 COTTON 100」 다음 줄에
# 「세탁 DRY CLEANING」을 두면 「100\n세」를 「100세」로 읽어 혼용률을 버렸다(till-i-die PaddleOCR 시험 36벌 전부, 2026-10-11).
_MIX_UNIT = re.compile(r"(?i)[ \t]*(?:cm|mm|kg|g\b|(?:수|원|만|개|벌|장|호|년|월|일|세|도)(?![가-힣])|s\b|['’]s|oz|d\b|gg|게이지|데니어|"
                       r"cc|ml|krw|ea|°c|x\s?\d|\*\s?\d|[.,]\d|/\s?\d|~|-\s?\d)")
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
    if s in _MIX_ALIAS or s in _MIX_MATERIAL_ALIAS:
        return _MIX_ALIAS.get(s) or _MIX_MATERIAL_ALIAS[s]
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
    material_heading = re.search(r"(?im)^\s*MATERIALS?\s*$", text) if num_first else None
    for m in _MIX_NUM.finditer(text):
        num, pct, letter = m.group(1), m.group(2), m.group(3)
        if num_first:
            # % 없는 숫자앞 표기는 독립된 소재 탭 줄에서만 허용한다.
            bare_first = not pct and material_heading is not None and material_heading.end() <= m.start() \
                and not text[text.rfind("\n", 0, m.start()) + 1:m.start()].strip()
            if not pct and not bare_first:
                continue
            a = _MIX_WORDS_AFTER.match(text, m.end())
            if not a:
                continue
            toks = list(re.finditer(r"[A-Za-z]+|[가-힣]+", a.group(1)))
            f = None
            i0 = 0
            while i0 < len(toks):
                for k in range(min(3, len(toks) - i0), 0, -1):
                    f = _mix_fiber([t.group() for t in toks[i0:i0 + k]], ocr=ocr)
                    if f:
                        break
                if f and k == 1 and i0 + 1 < len(toks) and toks[i0].group().lower() in _MIX_MOD \
                        and _mix_fiber([toks[i0 + 1].group()], ocr=ocr):
                    i0 += 1
                    continue
                if f:
                    break
                if toks[i0].group().lower() not in _MIX_MOD:
                    break
                i0 += 1
            if not f:
                continue
            cands, _ = _mix_values(num, True, False, ocr)
            if cands:
                end = a.start(1) + toks[i0 + k - 1].end()
                leather_kind = re.match(r"\s*\((cowhide|lambskin|sheepskin)\)", text[end:], re.I)
                if f in {"가죽", "천연가죽"} and leather_kind:
                    f = _mix_fiber([leather_kind.group(1)]) or f
                    end += leather_kind.end()
                atoms.append({"s": m.start(), "e": end, "f": f, "c": cands,
                              "x": bool(pct), "sure": False})
            continue
        if letter and not ocr:
            continue                  # 매장 글의 「100X」는 오독이 아니라 다른 말이다
        numbered_next = re.match(r"[ \t]*(?:/\s*\d[.:]\s*[A-Za-z]|-\s*\d+가지)", text[m.end():])
        if not pct and not letter and _MIX_UNIT.match(text, m.end()) and not numbered_next:
            continue                  # 단위가 붙은 숫자 — 「면 30수」 「20'S」
        if not pct and not letter and re.match(r"[ \t]+\d{1,3}(?:\.\d+)?[ \t]?%", text[m.end():m.end() + 9]):
            continue                  # 바로 뒤에 또 숫자 — 「COTTON 2 100%」의 2(깨진 글자) · 「NYLON 66 100%」
        if not pct and not letter and re.match(r"[A-Za-z]", text[m.end():m.end() + 1]):
            continue                  # 글자가 바로 붙은 숫자는 낱말의 일부다 — 「COTTON 3E 100%」의 3(깨진 「코튼」) · 「2way」
        j = m.start() - 1                 # 빠른 거름: 숫자 바로 앞(빈칸 · 쌍점 건너)이 글자가 아니면 덩이가 아니다
        while j >= 0 and text[j] in (" \t:：-)#" if ocr else " \t:：-)"):
            j -= 1
        if j < 0 or not (text[j].isalpha() or (ocr and pct and text[j].isdigit())):
            continue
        b = _MIX_WORDS_BEFORE.search(text, max(0, m.start() - 80), m.start())
        if not b and ocr:
            b = re.search(r"([A-Za-z가-힣]+)[ \t]+#[ \t]*$", text[:m.start()])
        toks = list(re.finditer(r"[A-Za-z]+|[가-힣]+", b.group(1))) if b else []
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
        if f == "가죽" and j > 0 and toks[j - 1].group().lower() == "bio":
            continue                    # Bio Leather는 원단 상품명이며 실제 조성은 공개되지 않았다.
        # 「Sheepskin Leather 100%」 — 끝 낱말(leather)은 그냥 가죽이고 앞 낱말이 더 자세하다(facade-pattern). 앞이 가죽의 한 갈래면 그걸 쓴다.
        if f == "가죽" and j > 0 and (_mix_fiber([toks[j - 1].group()]) or "").endswith("가죽"):
            f = _mix_fiber([toks[j - 1].group()])
            j -= 1
        # 꾸밈말과 **같은 섬유의 다른 말**은 이름에 넣는다 — 「POLYESTER 폴리에스터 100%」 「COTTON 면 35%」(mardi-mercredi 그림 글).
        while j > 0 and (toks[j - 1].group().lower() in _MIX_MOD or _mix_fiber([toks[j - 1].group()], ocr=ocr) == f):
            j -= 1
        if j >= 2 and _mix_fiber([t.group() for t in toks[j - 2:j]], ocr=ocr) == f:
            j -= 2
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
    key = re.sub(r"\s+", " ", m.group(1).lower())
    name = _MIX_PART_CANON.get(key, _MIX_PART_CANON.get(key.replace(" ", ""), m.group(1)))
    number = m.group(2)
    if number and number in "①②③④⑤⑥⑦⑧⑨":
        number = str(ord(number) - ord("①") + 1)
    return name + (f" {number}" if number else "")


# 「FABRIC :\nM -COTTON 100%\nC -NYLON 100%」(merely-made — M = main, C = contrast, PR 리뷰 011 표본 14). 한 글자 이름표라
# 대문자 그대로 · 「글자 -섬유」 꼴로 · 한 글에 M 과 C 가 다 있을 때만 부위로 읽는다(_mix_runs 가 정한다).
_MIX_MC = re.compile(r"(?<![A-Za-z])([MC])[ \t]?-[ \t]*$")
_MIX_MC_NAME = {"M": "겉감", "C": "배색"}


def _mix_gap(g: str, fiber: str | None = None, mc: bool = False) -> tuple[bool, str | None]:
    """두 덩이 사이 글이 줄기 안의 것인가 → (괜찮은가, 새 부위 이름).

    부위 이름은 **첫 것**을 쓴다(괄호 풀이 안의 「시보리」가 「배색」을 덮지 않게 괄호부터 지운다).
    소재 이름표(FABRIC · 소재 …)가 끼면 새 글이다 — thugclub 은 「Fabric: SHELL: COTTON 100% … 소재: SHELL: COTTON 100%」로
    같은 혼용률을 두 번 적어 한 줄기로 읽으면 겉감이 둘이 됐다."""
    ordinal = re.fullmatch(r"\s*(?:\((?:겉감|안감)\)\s*)?[/,;\s]*(?:\((\d)\)|(\d)\s*[.:])\s*", g)
    if ordinal:
        return True, "원단 " + (ordinal.group(1) or ordinal.group(2))
    if len(g) > 50 or g.count("\n") > 2:
        return False, None
    if mc:
        m = re.fullmatch(r"[\s,/]*(?<![A-Za-z])([MC])[ \t]?-[ \t]*", g)
        if m:
            return True, _MIX_MC_NAME[m.group(1)]
    g = _MIX_PAREN.sub(" ", g)
    m = _MIX_PART_RX.search(g)
    part = _mix_part_name(m) if m else None
    components = [x for x in _MIX_PART_RX.finditer(g) if _mix_part_name(x) in {"상의", "스커트", "스카프"}]
    if components:
        part = _mix_part_name(components[-1])
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
    ordinal = re.search(r"(?i)(?<![A-Za-z가-힣\d])(?:\((\d)\)|(?:\bfabrics?\s+)?(\d)\s*[.:])\s*$", text[max(0, s - 25):s])
    lead = text[max(0, s - 25):s][:ordinal.start()].rstrip(_MIX_SEP) if ordinal else ""
    if ordinal and not _MIX_PART_END.search(lead):
        return True, "원단 " + (ordinal.group(1) or ordinal.group(2))
    t = text[max(0, s - 50):s].rstrip(_MIX_SEP)
    t = _MIX_PAREN.sub(" ", t).rstrip(_MIX_SEP)
    part, ctx = None, False
    m = _MIX_PART_END.search(t)
    if m:
        part = _mix_part_name(m)
        # 「Brass hardware COW LEATHER 100%」의 hardware는 설명이다. 부속 이름은 명시적 이름표일 때만.
        if part in {"금속부속", "스트랩", "하네스", "스터드"} and not re.search(r"[:：)\]]", text[max(0, s - 8):s]):
            part = None
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
        tail = text[e + m.end():e + m.end() + 25]
        prices = [p.span() for p in re.finditer(r"(?:[₩$€£¥]\s*|(?:KRW|USD)\s*)\d[\d,.]*", tail)]
        for n in re.finditer(r"(?<![\d.])\d{1,5}(?:\.\d+)?(?![\d])", tail):
            # 관련 상품 「MESH SLING BAG / Black ₩207,000」의 가격은 미판독 혼용률이 아니다.
            if any(a <= n.start() < b for a, b in prices):
                continue
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
                # 슬래시로 구분된 완전한 두 조성은 신체 부위를 추측하지 않고 원단 번호로 보존한다.
                if part is None and re.fullmatch(r"\s*/\s*", text[cur[-1]["e"]:a["s"]]) \
                        and all(len(x["c"]) == 1 for x in cur) and sum(x["c"][0] for x in cur) == 100 \
                        and not any(x.get("part") for x in cur) and not _mix_lookback(text, cur[0]["s"])[1]:
                    cur[0]["initial_part"] = "원단 1"
                    part = "원단 2"
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
        return (_mix_sub(x[0], y[0]) or _mix_sub(y[0], x[0])
                or _mix_fragment(x[0], y[0]) or _mix_fragment(y[0], x[0]))
    for i in range(len(runs) - 1):
        # 문장 속 「코튼 100 원사」와 바로 뒤 「COTTON 100%」는 같은 값을 반복한 것이다.
        # 숫자·섬유 차례가 모두 같고, 앞뒤 어느 쪽에도 부위 이름이 없을 때만 토막 차단에서 제외한다.
        sig = lambda run: [(a["f"], a["c"]) for a in run]
        if sig(runs[i]) == sig(runs[i + 1]) and not any(a.get("part") for r in runs[i:i + 2] for a in r) \
                and not any(_mix_lookback(text, r[0]["s"])[1] for r in runs[i:i + 2]) \
                and not unk[i] and not unk[i + 1] and (res[i] is not None or res[i + 1] is not None):
            continue
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
            # 이름표 없는 숫자 항목을 OCR 잡음의 약어만으로 원단 부위로 만들지 않는다.
            # 「A 9, 4 : WO 7」·「a 9: RY 29」는 상세 사진의 판독 잡음이다.
            if part0 and part0.startswith("원단 ") and all(a.get("ab") and not a["x"] for a in run):
                lead = text[text.rfind("\n", 0, run[0]["s"]) + 1:run[0]["s"]]
                # 실제 독립 「1: C50 N50」·「FABRIC 1: …」는 여전히 미완성 부위다.
                if not re.fullmatch(r"(?i)[\s/;,]*(?:(?:fabrics?\s*)?\d\s*[.:]|\(\d\))\s*", lead):
                    part0 = None
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
            # 두 단 편집의 다음 줄 왼쪽 설명이 오른쪽 섬유 앞에 끼기도 한다.
            gap = text[merged[-1]["e"]:runs[j][0]["s"]]
            bullet_column = re.fullmatch(r"[/,; \t]*\n[ \t]*[-•·][^\d%\n]{1,50}", gap)
            if not m and not bullet_column:
                break
            part = _mix_part_name(m) if m and m.group(1) else None
            merged = merged + [dict(runs[j][0], part=part)] + runs[j][1:]
            r = _mix_check(text, merged, used, True)
            if bullet_column and isinstance(r, tuple) and any(sum(v for _, v in p["v"]) != 100 for p in r[0]):
                r = None
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
    if not labeled:
        labeled = _mix_bare_label(text, run)
        # 본문에 직접 적은 「폴리63 레이온20 울15 스판2 블랜딩」도 혼용률 구역이다.
        if len(run) >= 2 and re.match(r"\s*(?:블[랜렌]딩|혼방)(?:된|한|\s)", text[run[-1]["e"]:]):
            labeled = True
        if run[0].get("initial_part") and re.search(r"(?im)^\s*(?:MATERIALS?|FABRICS?|COMPOSITION)\s*$", text[:run[0]["s"]]):
            labeled = True
    if _mix_residue(text, run[0]["s"], run[-1]["e"], used, ocr):
        _mix_why("옆 토막", run)
        return None
    run = _mix_unrepeat(run, ocr)
    parts, name = [], run[0].get("initial_part") or part0 or ""
    for a in run:
        if a["part"] is not None or not parts:
            if a["part"] is not None:
                name = a["part"]
            parts.append((name, []))
        parts[-1][1].append(a)
    out, ok = [], True
    for name, aa in parts:
        suffix = re.match(r"\s*\((겉감|안감)\)", text[aa[-1]["e"]:])
        if not suffix:
            suffix = re.match(r"\s+(겉감|안감)(?=\s*(?:$|[,/•·▪|]))", text[aa[-1]["e"]:])
        if suffix:
            name = suffix.group(1)
        if len(aa) > 8:
            ok = False; _mix_why("덩이 너무 많음", run); break
        explicit = all(a["x"] for a in aa)
        if not labeled and (any(a.get("ab") for a in aa) or not all(a["x"] or a["sure"] for a in aa)):
            ok = False; _mix_why("이름표 없이 % 없음 · 약자", run); break       # % 없는 숫자 · 약자는 이름표 뒤에서만
        combos = [[]]
        for a in aa:
            combos = [c + [v] for c in combos for v in a["c"]]
        if explicit:
            # 96·98%도 매장이 적은 값이다. 두 부위가 이름 없이 붙은 100+100은 받지 않는다.
            hit = [c for c in combos if 0 < sum(c) <= 101 and (labeled or 99 <= sum(c) <= 101)]
        else:
            hit = [c for c in combos if abs(sum(c) - 100) < 1e-6]
        if len(hit) != 1:
            ok = False; _mix_why("합 어긋남" if not hit else "해석 여럿", run); break
        # 한 부위에 같은 섬유가 두 번 — 「Recycle Cotton 60% Cotton 40%」(youth) · 「RAYON13% RAYON7%」(noice)는 합이 맞으니 더한다.
        # 두 부위를 붙여 읽은 것(「COTTON 100% - COTTON 70% LINEN 30%」 xlim)은 위 검산(합 100)에서 이미 떨어진다.
        vals: dict[str, float] = {}
        for a, v in zip(aa, hit[0]):
            f = a["f"]
            if f == "스판덱스" and (v >= 50 or (name.startswith("메쉬") and re.search(r"(?i)polyurethane|폴리우레탄", text[a["s"]:a["e"]]))):
                f = "폴리우레탄"
            vals[f] = vals.get(f, 0) + v
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
    # 「Shell, Lining: Nylon 100%」는 하나의 조성을 공동 부위 각각에 적용한 표기다.
    shared = re.search(rf"(?i)({_MIX_PART})\s*[,ㆍ&]\s*({_MIX_PART})\s*[:：]\s*$",
                       text[max(0, run[0]["s"] - 80):run[0]["s"]])
    if shared and out:
        names = [_MIX_PART_CANON.get(shared.group(i).lower(), shared.group(i)) for i in (1, 2)]
        if out[0]["p"] == names[-1]:
            out.insert(0, {"p": names[0], "v": [v[:] for v in out[0]["v"]]})
    return out, labeled, (run[0]["s"], run[-1]["e"])


def _mix_bare_label(text: str, run: list[dict]) -> bool:
    """소재 탭의 독립 숫자 목록, 또는 원단 설명 바로 앞의 불릿 목록. 치수·가격 문장은 허용하지 않는다."""
    s, e = run[0]["s"], run[-1]["e"]
    if any(a.get("ab") for a in run):
        return False
    before = text[:s]
    after = text[e:]
    # 제목과 값 사이에 사이즈 탭이 끼어도, 값이 독립된 한 줄이면 이름표를 보존한다.
    line_start = before.rsplit("\n", 1)[-1]
    line_end = after.split("\n", 1)[0]
    if not line_start.strip(" \t-·•") and not line_end.strip(" \t.,;/") \
            and re.search(r"(?im)^\s*(?:MATERIALS?|FABRICS?|COMPOSITION)\s*$", before):
        return True
    # 「상품명 - nylon 55 fine wool 25 baby alpaca 20 - ... 원사 ...」
    return (len(run) >= 2 and bool(re.search(r"(?:^|\s)-\s*$", before))
            and bool(re.match(r"\s+-\s+", after)))


_OCR_FIBER_EN = sorted(set(_MIX_FUZZY) | {"wool", "silk", "spandex", "polyester"}, key=len, reverse=True)
_OCR_SPLIT = re.compile(r"(?i)\b(?:" + "|".join(
    re.escape(w[:i]) + " " + re.escape(w[i:]) for w in _OCR_FIBER_EN if len(w) >= 5 for i in range(2, len(w) - 1)) + r")\b")
_OCR_FIB = "|".join(_OCR_FIBER_EN)
_OCR_GLUED = re.compile(r"(?i)(?<![A-Za-z])(" + _OCR_FIB + r")(?![A-Za-z])")


def ocr_fix(text: str) -> str:
    """판독기가 흔히 깨뜨리는 혼용률 글자를 바로잡는다 — PaddleOCR 그림 글(crawl/ocr_paddle)에만 쓴다(till-i-die 시험 2026-10-11).
    「COT TON」 → 「COTTON」, 「72NYLON」 · 「POLYESTER25」 → 칸을 띄운다, 「1O0」 · 「7ONYLON」(숫자 사이 · 뒤 영문 O) → 「100」 · 「70」,
    「COTTON77 I POLYESTER23」(칸막이 | 를 I 로 읽음) → 「/」. 섬유 이름 곁만 고친다 — 「100D」 「30S」 「2XL」은 그대로."""
    t = _OCR_SPLIT.sub(lambda m: m.group().replace(" ", ""), text or "")
    t = re.sub(r"(?i)(\d)[Oo](?=(?:" + _OCR_FIB + r")(?![A-Za-z]))", r"\g<1>0", t)
    t = _OCR_GLUED.sub(r" \1 ", t)
    t = re.sub(r"(?<![A-Za-z])\d[\dOo]*(?![A-Za-z\d])",
               lambda m: m.group().replace("O", "0").replace("o", "0"), t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return re.sub(r"(?<=[\d%]) [Il|] (?=[A-Za-z가-힣])", " / ", t)


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
    # 주부위에 소재명만 있고 비율이 없으면 partial이다. 예: [Main1] 90/10 [Main2] 소가죽.
    # 겉감 비율이 명확하고 안감은 이름만 공개한 경우에는 그 겉감 값까지 버리지 않는다.
    # 괄호/쌍점 이름표와 바로 뒤의 아는 소재명에 한정하여 설명 속 부위 낱말을 수집하지 않는다.
    labels = list(_MIX_PART_RX.finditer(t))
    for i, m in enumerate(labels):
        tail = t[m.end():labels[i + 1].start() if i + 1 < len(labels) else m.end() + 80]
        if not re.match(r"\s*[\]):：]", tail):
            continue
        body = tail.lstrip(" \t\n]):：-")
        words = re.match(r"[A-Za-z가-힣]+(?:[ \t]+[A-Za-z가-힣]+){0,2}", body)
        if not words:
            continue
        toks = words.group().split()
        known = bool(_mix_fiber(toks))
        part = _mix_part_name(m)
        if known and not re.search(r"\d", body[:60]) and mix_has_main([{"p": part, "v": []}]):
            lost["parts"].add(part)
    # 이름표 없이 비율 없는 소재 이름이 「/」 바로 앞에 오면 그 뒤 혼용률은 겉감이 아니다 — atelier-de-lumen 가방 · 케이스 84벌
    # 「Product Info - Cow Leather / Polyester 92% Spandex 8%」(가죽 겉감 / 안감). 뒤엣것을 이름 없는 부위로 읽어 가죽 가방의
    # 혼용률이 「폴리에스터 92 · 스판덱스 8」이 됐다(qa27 2026-10-27). 겉감을 못 읽은 것으로 센다 — 이름 없는 부위만 읽힌 결과는 버려진다.
    for c in out:
        a = c["span"][0]
        pre = re.search(r"([A-Za-z가-힣]+(?:[ \t]+[A-Za-z가-힣]+){0,2})[ \t]*[/+&][ \t]*$", t[max(0, a - 40):a])
        if pre and c["m"] and all(p["p"] == "" for p in c["m"]):
            words = pre.group(1).split()
            if any(_mix_fiber(words[i:]) for i in range(len(words))):
                lost["parts"].add("겉감")
    return out, bad, lost


def _mix_fragment(a: list[dict], b: list[dict]) -> bool:
    """100 미만 조각의 섬유·비율을 전부 담은 전체 후보가 있나."""
    shorter = False
    for p in a:
        values = {tuple(v) for v in p["v"]}
        matches = [q for q in b if (not p["p"] or not q["p"] or p["p"] == q["p"])
                   and values <= {tuple(v) for v in q["v"]}]
        if not matches:
            return False
        if all(values < {tuple(v) for v in q["v"]} for q in matches):
            if sum(v for _, v in p["v"]) >= 99:
                return False
            shorter = True
    return shorter


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
    # 값이 다른 후보는 충돌로 남기고, 동일 비율을 포함한 전체 후보가 있으면 그쪽을 쓴다.
    cands = [c for c in cands if not any(d is not c and _mix_fragment(c["m"], d["m"]) for d in cands)]
    m, st = _mix_pick(cands)
    # 겉감(또는 이름 없는 부위)이 없으면 그 옷의 혼용률이 아니다 — 「Lining: 56% Cotton, 44% Rayon」만 읽힌 것(khakis)을 앱은
    # 부위가 하나라 이름을 떼고 「코튼 56% · 레이온 44%」로 적는다(layer-web materialMixAxes). 옛 blend 도 이런 것을 684벌 냈다.
    if m and not mix_has_main(m):
        return [], "partial"
    return m, st


def mix_has_main(m: list[dict]) -> bool:
    """안감·배색 단독은 완전한 조성이 아니다. 원단 번호와 세트의 주 구성품은 이름을 보존한다."""
    return any(p["p"] == "" or p["p"].startswith(("겉감", "원단 "))
               or p["p"] in {"원단", "상의", "펜던트"} for p in m)


def _mix_pick(cands: list[dict]) -> tuple[list[dict], str]:
    if not cands:
        return [], "none"
    # 번호 없는 「원단」은 기본 소재 제목이다. 이를 별도 부위로 합치면
    # 「Body Nylon100 / 원단 Polyester86 PU14」라는 매장 충돌을 2개 겉감으로 만들게 된다.
    roots = {_mix_key([p]) for c in cands for p in c["m"] if p["p"] == "원단"}
    mains = {_mix_key([p]) for c in cands for p in c["m"] if p["p"] in {"", "겉감"}}
    if roots and mains:
        if len(roots | mains) > 1:
            return [], "conflict"
        # 같은 값의 소재 제목과 겉감이 한 후보에 중복된 경우는 겉감 표기를 보존한다.
        cands = [dict(c, m=[p for p in c["m"] if p["p"] != "원단" or not any(
            q["p"] in {"", "겉감"} and _mix_key([q]) == _mix_key([p]) for q in c["m"])]) for c in cands]
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


def material_parts(text: str) -> dict[str, str]:
    """혼용률만 따로 건진다 — 치수표 한가운데 끼어 있어도 살린다. {부위: 「COTTON 60% POLYESTER 40%」}.

    goodlifeworks 는 「SIZE GUIDE FABRIC COTTON 60% POLYESTER 40% SIZE 사이즈 어깨 …」
    처럼 소재와 표가 한 덩이로 붙어 온다. 조각째로 보면 앞의 `SIZE GUIDE` 에 걸려 소재까지
    날아간다. 혼용률은 detail_from_ocr 이 이미 정확히 뽑는다(합이 90~110 일 때만 받는다) —
    그 잣대를 그대로 불러 쓴다.
    """
    return materials([re.sub(r"\s+", " ", ln) for ln in (text or "").splitlines()] or [text or ""])


def _join_parts(got: dict[str, str]) -> str:
    return " ".join(mix if part == "겉감" else f"{part} {mix}" for part, mix in got.items())


def material_of(text: str) -> str:
    return _join_parts(material_parts(text))


# ── 혼용률 머리줄을 태거의 혼용률(mat)에 맞춘다(qa27 2026-10-27) ──────────────────────────────
# 설명 맨 앞 혼용률 줄은 위 material_parts(detail_from_ocr.materials)가 글에서 따로 읽은 것이라 mat 과 갈라졌다
# (판매중 · 품절 전수 2026-10-27):
#   · 부위 이름표가 틀림 4,366벌 — 줄 **어디든** 「안감」 · 「배색」이 있으면 그 줄의 혼용률 전부를 그 부위로 적었다.
#     matin-kim 「면 100% -비침 보통 -신축성 보통 -안감 없음」 → 「안감 면 100%」, ava-molli 「안감 처리로 보온성 우수」.
#   · 마지막 섬유가 잘림 1,047벌 — 「POLYURETHANE(SPANDEX) 3%」 · 「SPAN 5%」를 몰라 합 90 언저리로 받았다(nick-nicole · rest-recreation).
#   · 가죽 옷 · 가방인데 안감 섬유 409벌 — 섬유 낱말에 가죽이 없어 「Cow Leather 100% / Lining Polyester 100%」의 안감만 남았다.
#   · 다른 상품 견본 — noice 벨트 「혼용률 : COWHIDE 100%」 옆 매장 견본 「fabric :MERINO WOOL 50%…」가 머리줄이 됐다.
# mat 은 여러 출처를 대 보고 고른 값이라(tag_items.blend_of_brand) 그것이 기준이다. 글에서 읽은 줄이 mat 과
# 부위 · 섬유 · 비율까지 같으면 매장 말 그대로(「WOOL 88% NYLON 12%」) 두고, 다르면 mat 으로 쓴다.
# mat 이 비었으면(태거가 어긋남 · 견본 · 안감만으로 버린 것) 머리줄을 안 만든다 — 틀린 값보다 빈 값.
_MAT_EQ = {"폴리우레탄": "스판덱스"}       # 반 안 되는 PU 는 스판덱스로 적는다(tag_items.load_manual_mat 와 같은 규칙)
_MAIN_PART = re.compile(r"^(?:겉감|원단|몸판|본판|메인)?\s*1?$")


def _canon_fiber(name: str) -> str | None:
    w = re.sub(r"[^0-9A-Za-z가-힣 ]", " ", name or "").split()
    f = (_mix_fiber(w) or _fiber(" ".join(w))) if w else None
    return _MAT_EQ.get(f, f) if f else None


def _part_role(p: str) -> str:
    p = (p or "").strip()
    return "겉감" if _MAIN_PART.match(p) else re.sub(r"\s+", " ", p)


def _pct(v) -> str:
    v = float(v)
    return str(int(v)) if v == int(v) else f"{v:g}"


def mat_line(mat: list) -> str:
    """mat → 머리줄. 부위가 하나면 이름표 없이(「코튼 70% 폴리에스터 30%」), 여럿이면 「겉감 … / 안감 …」."""
    groups = [g for g in (mat or []) if isinstance(g, dict) and g.get("v")]
    out = []
    for g in groups:
        p = (g.get("p") or "").strip()
        if len(groups) == 1 and _part_role(p) == "겉감":
            p = ""                          # 부위가 겉감 하나면 이름표 없이(앱 소재 칸과 같은 모양)
        elif not p:
            p = "겉감"
        mix = " ".join(f"{f} {_pct(v)}%" for f, v, *_ in g["v"])
        out.append(f"{p} {mix}".strip())
    return " / ".join(out)


def _mat_sets(mat: list) -> dict[str, list]:
    out: dict[str, list] = {}
    for g in mat or []:
        if isinstance(g, dict) and g.get("v"):
            out.setdefault(_part_role(g.get("p")), []).append(
                sorted((_MAT_EQ.get(str(f), str(f)), float(v)) for f, v, *_ in g["v"]))
    return out


def parts_agree(got: dict[str, str], mat: list) -> bool:
    """글에서 읽은 부위별 혼용률이 mat 과 같은가 — 부위(겉감 · 이름 없음 · 원단은 한 부위) · 섬유 · 비율 모두.
    mat 의 부위를 다 말하지 않아도(겉감만) 말한 것이 다 맞으면 같다고 본다. 겉감 쪽 부위는 꼭 있어야 한다."""
    if not got or not mat:
        return False
    ms = _mat_sets(mat)
    roles = set()
    for part, mix in got.items():
        pairs = [(_canon_fiber(a), float(b)) for a, b in _FIBER_PCT.findall(mix)]
        if not pairs or any(f is None for f, _ in pairs) or sorted(pairs) not in ms.get(_part_role(part), []):
            return False
        roles.add(_part_role(part))
    return "겉감" in roles or "겉감" not in ms


def canon_pairs(text: str) -> set[tuple[str, float]]:
    """글 속 「섬유 NN%」 → {(표준 이름, 비율)} — 머리줄과 같은 말을 다시 적은 줄을 가릴 때 쓴다."""
    out = set()
    for a, b in _FIBER_PCT.findall(text or ""):
        f = _canon_fiber(a)
        out.add((f or a.upper(), float(b)))
    return out


def head_of(got: dict[str, str], mat: list | None) -> str:
    """머리줄. mat 이 None 이면(태그 파일 없이 부르는 곳) 예전처럼 글에서 읽은 그대로."""
    if mat is None:
        return _join_parts(got)
    # 글이 혼용률을 말하지 않았으면(또는 합이 안 맞아 못 읽었으면) 머리줄을 새로 세우지 않는다 — mat 은 상세 조각의 소재 칸에
    # 따로 나간다. 여기서는 글에서 읽은 머리줄이 틀린 것만 바로잡는다(첫 판에 mat 으로 다 세웠더니 noice 1,793벌 중 1,359벌이 바뀌었다).
    if not mat or not got:
        return ""
    if parts_agree(got, mat):
        return " / ".join(mix if part == "겉감" else f"{part} {mix}" for part, mix in got.items())
    return mat_line(mat)


# 매장 화면의 구간 이름표 — 어떤 소비자에게도 정보가 아니다(전수 2,006편).
_SECTION_HEAD = re.compile(
    r"^\s*(?:feature|features|detail|details|description|info|information|fabric|"
    r"material|composition|상품\s?설명|제품\s?설명|패브릭\s?정보|소재\s?정보|제품\s?소재|디테일)"
    r"\s*[:：]?\s*$", re.I)
# 치수표가 글로 흘러든 첫 줄(전수 275편). 사이즈표가 바로 위에 있는데 또 적힌다.
_SIZE_HEAD = re.compile(r"^\s*(?:총기장|총장|기장|어깨|가슴|허리|엉덩이|밑단|소매|허벅지|밑위)\s*\d")
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
    r"특성상|일반적인|적합한|뒤집어|통해|거쳐|이며|으며|높고|및|등은|등의|등을|(?<![상하내])의|[가-힣](?:은|는|을|를)|(?<![블헤트])[가-힣]로|"
    # 영문 안내문의 머리 — 「We recommend … by washing and」
    r"(?i:\b(?:and|or|with|by|of|to|for|in|on|the|a|an))|"
    # 한 글자 토막은 좁게 — 「넥」「핏」「탑」은 옷 말이다(「… 라운드 넥」 coor). 후기 쓴 이의 성(「가디건 추천 김」)
    r"(?:^|\s)(?:물|및|등|후|시|때|중|김|이|박|최|정|홍|백|전))\s*$")


# PART I (2026-10-18): 문장으로 쪼개기 전에 UI/후기 영역의 경계를 보존한다.
_HTML_TAG = re.compile(
    r"<+(?:/?(?:p|br|div|span|a|b|strong|em|i|u|ul|ol|li|table|tbody|thead|tr|td|th|"
    r"h[1-6]|img|section|article|font|center|hr)\b)[^<>]*>", re.I)
_HTML_BLOCK = re.compile(r"<!--.*?-->|<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_BROKEN_CLOSE = re.compile(r"</(?:p|div|span)\b(?=\s|$)(?![^<>]*>)", re.I)
_REVIEW_GUIDE = re.compile(
    r"리뷰\s*로\s*보는\s*활용\s*가이드.*?"
    r"(?:위\s*내용은\s*구매자들의\s*후기를\s*바탕으로\s*요약되었습니다[.]?|$)", re.S)
_REVIEW_DATE = re.compile(r"후기\s+\d{2,4}[.]\d{2}[.]\d{2}\s+[가-힣A-Za-z][*]*")
_SHOP_UI = re.compile(
    r"\bbuy\s+now\b|\bsold\s+out\b|회원\s*등급별\s*혜택|나의\s*등급\s*확인하기(?:\s+VIP\b)?", re.I)


# OCR에서 한글을 영문/기호로 오독한 짧은 토막. 모르는 영문이라는 이유만으로는
# 버리지 않는다(브랜드·원사 이름·상품 코드도 있다). 닫히지 않은 기호 등 독립된
# 잡음 근거가 있을 때만 쓰는 보호 어휘다. 추출용 원문에는 적용하지 않는다.
_OCR_READABLE = re.compile(
    rf"(?:{_HARD_EN.pattern}|{FIBER}|"
    r"size|sizes|free|one|xs|s|m|l|xl|xxl|xxxl|cm|mm|inch|kg|g|oz|"
    r"ykk|riri|sbs|lycra|coolmax|goretex|polartec|stoll|"
    r"dry|clean|hand|wash|machine|bleach|iron|care|instructions|"
    r"shell|body|tape|rib|color|black|white|navy|grey|gray|beige|"
    r"shoulder|chest|length|waist|hip|thigh|rise|width|tip)", re.I)
_OCR_BRACKETS = re.compile(r"\[[^\[\]\n]*\]|\{[^{}\n]*\}")


def _ocr_token_noise(token: str, korean: bool) -> bool:
    """확실히 깨진 짧은 토막만 판정. 낯선 영단어·숫자·혼용률은 근거가 아니다."""
    core = token.strip(",.;:!?\"'“”‘’()·ㆍ•*")
    # 깨진 상호 뒤에 붙은 조사까지 함께 뺀다: 0\\5\\#'의 → 빈칸.
    core = re.sub(r"(?<=[^가-힣])(?:의|이|가|을|를|은|는|로|과|와|에|도)$", "", core)
    if not core or _HANGUL.search(core):
        return False
    bare = core.strip("[]{}|,.;:!?\"'“”‘’()")
    if _OCR_READABLE.fullmatch(bare):
        return False
    # 역슬래시·중괄호 등이 겹친 토막(0\5\#')은 글자가 아니다.
    # 숫자 뒤 기호 하나(실측/각주 등)나 #123 같은 코드는 남긴다.
    bad = sum(c in "\\{}@^#_" for c in core)
    if bad >= 2 and not re.search(r"[A-Za-z]{3,}", core):
        return True
    # 한글 문장 안의 OAS}, Mele], YSolo|. 균형 잡힌 [색상/코드]는
    # 호출 쪽에서 보호하고, 숫자가 섞인 상품 코드도 건드리지 않는다.
    m = re.fullmatch(r"([A-Za-z]{2,12})(?:\]|\}|\|)", core)
    if not m:
        return False
    return core.endswith("}") or korean or not (m[1].isupper() or m[1].islower())


def ocr_display_text(text: str) -> str:
    """표시용 OCR의 확실한 잡음 조각만 뺀다. 정상 문장은 줄째 버리지 않는다.

    clean(매장 HTML)에는 일반 적용하지 않는다. from_mined 또는 명시적인
    [그림 글자] 뒤에서만 부른다. 혼용률·사이즈 해석용 입력은 그대로 둔다.
    """
    out = []
    for line in (text or "").split("\n"):
        protected = [m.span() for m in _OCR_BRACKETS.finditer(line)]
        korean = bool(_HANGUL.search(line))
        spans = [m.span() for m in re.finditer(r"\S+", line)
                 if not any(a <= m.start() and m.end() <= b for a, b in protected)
                 and _ocr_token_noise(m[0], korean)]
        if spans:
            for a, b in reversed(spans):
                line = line[:a] + line[b:]
            line = re.sub(r"[ \t]{2,}", " ", line).strip()
            if not line:
                continue
        # 기호뿐인 행과 기호가 섞인 한 글자 영문 나열. S/M/L이나 A/B/C/D
        # 같은 표 머리는 보존한다. 숫자뿐인 실측 행도 이 조건에 안 걸린다.
        letters = re.findall(r"[A-Za-z]+", line)
        singletons = (len(letters) >= 4 and all(len(w) == 1 for w in letters)
                      and all(w.islower() for w in letters)
                      and "".join(letters).upper() not in "ABCDEFGH"
                      and not set(w.upper() for w in letters) <= set("SMLX")
                      and bool(re.search(r"[\\{}@^_¢|]", line)))
        noise_line = (not any(c.isalnum() for c in line)
                      or (singletons and not korean and not re.search(r"\d", line)))
        if line.strip() and line.strip() not in {"%", "％", "±", "℃"} and noise_line:
            continue
        out.append(line)
    return "\n".join(out)


def _description_text(text: str, ocr: bool = False) -> str:
    """표시용 설명 전처리. 손실된 글자를 추측해 복원하지 않는다."""
    text = text or ""
    # 중첩 엔티티는 두 겹까지만 해제한다. &amp; 및 수치 비교 기호는 보존한다.
    for _ in range(2):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = _HTML_BLOCK.sub("\n", text)
    text = _HTML_TAG.sub(lambda m: "\n" if re.match(
        r"<+/?(?:p|br|div|li|tr|h[1-6]|hr)\b", m[0], re.I) else "", text)
    text = _BROKEN_CLOSE.sub("\n", text)
    text = _SHOP_UI.sub(" ", text)

    def drop_guide(m: re.Match) -> str:
        block = m[0]
        if "요약되었습니다" in block:
            return "\n"
        # 공개 descs에는 종료 고지가 빠지고 '사용 시점'에서 줄이 끝난다.
        end = re.search(r"사용\s*시점[^\n]*\n", block)
        return "\n" + block[end.end():] if end else "\n"
    text = _REVIEW_GUIDE.sub(drop_guide, text)
    # 원본 후기 게시판: 성인 안내 뒤 날짜/마스킹 작성자가 있는 경우에만 적용.
    # 다음 페이지 뒤에는 실제 상품 설명이 다시 나오므로 그 뒤는 살린다.
    text = re.sub(
        r'"?19세\s*미만의\s*미성년자"?.*?(?=다음\s*페이지|$)',
        lambda m: "\n" if (re.search(r"네이버\s*페이\s*구매자|\S*\*{3,}", m[0])
            and re.search(r"\d{2,4}[-./]\d{2}[-./]\d{2}", m[0])) else m[0],
        text, flags=re.S)
    text = re.sub(r"(?:이전\s*페이지(?:\s+\d+)*\s*)?다음\s*페이지", "\n", text)
    # 이미 공개된 descs에서는 작성자 마스킹/조회 수가 사라져 있다.
    # 같은 줄의 마지막 후기 날짜+작성자까지 제거하고 뒤의 정상 설명을 보존한다.
    lines = []
    for line in text.split("\n"):
        dates = list(_REVIEW_DATE.finditer(line))
        if dates:
            line = line[dates[-1].end():]
            line = re.sub(r"^\s*페이지\s*", "", line)
        lines.append(line)
    text = "\n".join(lines)
    # U+FFFD는 원문 바이트가 소실됐다는 뜻이다. 해당 문장만 제외한다.
    # 단순 삭제하면 '겹�PO' → '겹PO'처럼 잘못된 말이 남는다.
    if "\ufffd" in text:
        text = "\n".join(s for s in re.split(r"\n+|(?<![Ff]rom\.)(?<=[.!?])\s+", text) if "\ufffd" not in s)
    if ocr:
        return ocr_display_text(text)
    shop, marker, picture = text.partition("[그림 글자]")
    return shop + marker + ocr_display_text(picture) if marker else text


# 표시용으로만 정리한다. 혼용률/실측 추출용 원문과 blend의 분절은 바꾸지 않는다.
_EMPTY_BRACKET = re.compile(r"\(\s*\)|\[\s*\]|\{\s*\}")
_TAIL_LABEL = re.compile(
    r"(?:\s+|(?<=%))(?:WASHING\s+TIP|WASHING|CARE|COLOR|COMMENT|MAIN|"
    r"(?:MATERIAL\s+)?(?:FABRIC\s+)?(?:OUTSHELL|SHELL|LINING)|FABRIC|"
    r"STORY\s+DETAIL\s+MATERIAL|제품설명|상세설명)\s*[:：]?\s*$", re.I)
_ORPHAN_LABEL = re.compile(
    r"^[+\s]*(?:(?:Instruction\s+)?DETAIL|MATERIAL\s+Shell|SHOES\s+detail|size\s+tip|"
    r"(?:SIZE\s*(?:&\s*FIT|DETAIL|ONE\s+SIZE)?|ONE\s+SIZE|Sleeve|원산지|허리\s*최대\s*사이즈)|"
    r"Size\s+detail\s*\(cm\)|&\s*Care)\s*[:：]?\s*$", re.I)
_CARE_CONTEXT = re.compile(
    r"물빨래|물\s*빠짐|털\s*빠짐(?!을\s*최소화)|부분\s*세척|고열과\s*스팀|스팀을?\s*사용하지|얇은\s*천을\s*덧대|"
    r"옷걸이에\s*걸어\s*보관|지퍼백에\s*넣어|오염\s*시|오염된\s*부분|"
    r"(?:소재|제품|상품)의?\s*결함|불량\s*사유|(?:물|습기).*마찰에\s*약|"
    r"기장은\s*(?:리브|립|칼라)\s*부분을\s*제외|(?:색상|컬러|디테일).*이미지와\s*가장|"
    r"이미지가\s*실제\s*제품\s*색상|분실될\s*경우|"
    r"(?:지퍼|단추).*잠근\s*후|(?:헤어|섬유)가?\s*탈락|염료\s*결\s*응집|"
    r"개체\s*차이|개체마다\s*미세한.*데미지가\s*있을|올풀림.*발생할|추가\s*금액|(?:주문|맞춤)\s*제작\s*상품|"
    r"프린트\s*부분에\s*직접적인\s*열|형태\s*변형\s*방지를\s*위해\s*전체|사이즈\s*측정\s*시|측정\s*위치|사이즈\s*가이드|care\s+instructions", re.I)
# 여러 품목에 함께 붙는 소재 관리 페이지. 옷 자체의 워싱/조직/성능과 구별되는
# 일반 관리 문형만 받는다. bare fiber(예: Linen)는 이 문맥이 있을 때에만 표제다.
_CARE_TEMPLATE = re.compile(
    r"(?:Cotton\s+|면\s+)?컬러를\s*선명하게\s*유지하기\s*위해\s*겨드랑이|"
    r"(?:Wool\s+)?울\s*소재는\s*다른\s*섬유만큼\s*자주|"
    r"대다수의\s*울\s*소재\s*섬유는|이를\s*제대로\s*따르지\s*않으면|"
    r"(?:또한\s*,?\s*)?니트\s*아이템은\s*회전식|"
    r"니트는\s*젖었을\s*때\s*무거워|남아있는\s*물기가\s*흡수되면서|"
    r"(?:Denim\s+)?데님\s*데님은\s*입을\s*때마다|데님은\s*입을\s*때마다|"
    r"데님의\s*무게감이\s*주름을\s*펴는|(?:Silk\s+)?실크\s*제품은\s*입을\s*때마다|"
    r"부드러운\s*가죽은\s*긁힐\s*수", re.I)
_CARE_FIBER_HEADS = {"cotton", "wool", "denim", "linen", "silk"}
_CARE_MATERIAL_CONTEXT = re.compile(r"\bMATERIAL\b.*?\bSIZE\s*\(\s*CM\s*\)", re.I | re.S)

_PERFORMANCE = re.compile(r"세탁\s*후\s*수축이\s*적|세탁에도\s*안정적인\s*형태를\s*유지|세탁\s*후에도\s*변형이\s*적어|세탁\s*케어에\s*용이하게\s*제작")

# 두 개 이상의 치수-값 셀만 표로 처리한다. %, 게이지, 포켓 개수는 셀에 포함하지 않는다.
_DIM = (r"(?:CENTER\s+BACK|BOTTOM\s+HEM|CROWN\s+DEPTH|BRIM\s+LENGTH|"
        r"SLEEVE\s+LENGTH|BOTTOM\s+WIDTH|CROTCH\s+DEPTH|ARMHOLES?|ARM|LENGTH|WAIST|HIPS?|THIGH|CROTCH|RISE|SHOULDER|CHEST|SLEEVE|HEM|"
        r"총기장|총장|기장|어깨\s*(?:너비|넓이|단면)?|가슴\s*(?:단면|둘레|너비)?|허리\s*(?:둘레|단면)?|(?:힙|엉덩이)\s*(?:단면|둘레)?|"
        r"앞밑위|밑위|허벅지\s*(?:단면|둘레)?|밑단|암홀|소매\s*(?:기장|길이|통|장|단면|밑단|부리|너비|넓이)?|목(?:넓이|너비)|총길이|팔기장|트임길이|깊이|챙\s*길이|가로|세로|폭|스트랩)")
_VALUE = r"\d{1,3}(?:\.\d+)?(?![\d.%])(?:\s*(?:[-~]\s*\d{1,3}(?:\.\d+)?|/\s*\d{2,3}(?:\.\d+)?))?\s*(?:CCM|CM|MM|IN|C)?\b"
_SIZE_PREFIX = r"(?:(?:[XSML]{1,3}|[1-5])\s*(?:SIZE)?\s*[:：]?\s*)?"
_CELL = rf"{_DIM}\s*(?:\([^()\n]{{1,24}}\))?\s*[:：]?\s*{_VALUE}"
_SIZE_CELLS = re.compile(rf"{_SIZE_PREFIX}{_CELL}(?:[\s/,;]+{_SIZE_PREFIX}{_CELL})+", re.I)
_SIZE_HEADERS = re.compile(
    rf"(?:{_DIM}\s*(?:\([^()\n]{{1,24}}\))?\s*[/|,]?\s*){{3,}}"
    r"(?:\(?\s*cm\s*\)?\s*)?(?:\(OS\)\s*[:：]?\s*)?(?:(?:[XSML]{1,3}|FREE|ONE\s*SIZE)\s*[:：]?\s*)?"
    r"(?:\d{1,3}(?:\.\d+)?\s*[/|,]?\s*){3,}", re.I)
_MODEL_BODY = re.compile(
    r"(?:모델\s*\(\s*키\s*\d{2,3}\s*cm\s*\)\s*피팅\s*사이즈\s*/\s*원사이즈|"
    r"모델이\s*착용한\s*사이즈는\s*ONE\s*SIZE\s*입니다[.]?|"
    r"(?:남|여)\s*\d{2,3}\s*cm\s*\([^)]*size\)|"
    r"\(?model\s*[-:]?\s*\n?\s*\d\s*size\s*착용\)?|"
    r"(?:MODEL\s+)?(?:HEIGHT|키)\s*[:：]\s*\d{2,3}\s*(?:cm)?"
    r"(?:\s*[/,]?\s*(?:DRESS|BUST|CHEST|WAIST|HIPS?|가슴둘레|허리둘레|엉덩이둘레)"
    r"\s*[:：]\s*\d{1,3}(?:\.\d+)?\s*(?:cm|in)?)+)", re.I)


def tidy_line(text: str) -> str:
    """내용 없는 꼬리만 걷는다. [SHELL], 색상값, 상품 코드는 보존한다."""
    text = re.sub(r"[\u200b\ufeff\u2800]", "", text).strip()
    for _ in range(4):
        before = text
        text = _EMPTY_BRACKET.sub("", text)
        text = re.sub(r"\s*[\[(]\s*$", "", text)
        m = _TAIL_LABEL.search(text)
        if m and (_HANGUL.search(text[:m.start()]) or "%" in text[:m.start()]
                  or _HANGUL.search(m[0])
                  or re.search(r"WASHING|CARE|COLOR|COMMENT|MAIN|MATERIAL\s+SHELL", m[0], re.I)):
            text = text[:m.start()].strip()
        if text == before:
            break
    text = re.sub(r"(?:\s+|^)(?:WIPING\s+OFF\s+)?1[.]\s*$", "", text, flags=re.I)
    text = re.sub(r"\s+\?$", "", text)
    text = re.sub(r"(?i)\s+Size\s+detail\s*\(cm\)\s*$", "", text)
    text = re.sub(r"(?i)^(?:Fabric\s*)?&\s*Care\s+", "", text)
    text = re.sub(r"\s+반지\s*폭\s*mm(?=,|$)", "", text)
    text = re.sub(r"\s+(?:SIZE\s+)?[XSML]\s*:\s*[XSML]?\s*$", "", text, flags=re.I)
    # 닫는 괄호가 원문 후속 문장과 함께 탈락한 경우, 남은 여는 괄호만 뗀다.
    if text.startswith("(") and text.count("(") > text.count(")"):
        text = text[1:].lstrip()
    if _ORPHAN_LABEL.fullmatch(text) or _SECTION_HEAD.fullmatch(text):
        return ""
    return text


def cap_complete(text: str, limit: int = 2000) -> str:
    """한도 안의 마지막 문장/원문 줄 경계까지만 내보낸다. 소수점은 경계가 아니다."""
    if len(text) <= limit:
        return text
    prefix = text[:limit + 1]
    ends = [m.start() for m in re.finditer(r"\n", prefix) if m.start() <= limit]
    ends += [m.end() for m in re.finditer(
        r"[.!?。](?=\s|$)|(?:니다|어요|아요|해요|세요)(?=\s|$)", prefix) if m.end() <= limit]
    return text[:max(ends)].strip() if ends else ""


def dedup_lines(lines: list[str]) -> list[str]:
    """같은 상품의 같은 줄만 정규화한다. 부위명은 키에서 지우지 않는다."""
    out, seen = [], set()
    for line in lines:
        # 표제에 기대어 선택되던 정상 본문은 선택을 마친 뒤 표제만 뗀다.
        line = re.sub(r"(?i)^(?:Product\s+(?:Info|Information|Description)\s+DETAIL|Instruction\s+DETAIL)\s+", "", line)
        line = tidy_line(line)
        key = re.sub(r"[\W_]+", "", re.sub(r"(?i)^(?:(?:MAIN\s+)?MATERIALS?|원단|혼용률|Comment)\s*[:：]?\s*", "", line).casefold())
        if key and key not in seen:
            out.append(line)
            seen.add(key)
    return out


# 매장 화면의 탭 · 단추 · 접기 글자 — 설명 그릇에 같이 담겨 온다(텍스트 QA 2026-10-27, 21곳 7,688편).
#     insilence 「… 자세히보기 이탈리아 베라펠레 …」 1,478 · kirsh 「상세설명 BIG CHERRY T-SHIRTS …」 869
#     plac 「… Service Info / Size Measurement Material & Care Cotton 98%」 854 · open-yy 「… Clothing Shoes This」 728
#     brown-yard 「… YKK ZIPPER 더 보기 소재 COTTON 100%」 616 · misu-a-barbe 「QUANTITY: Description Size Fabric」 482
#     youth 「CLOSE Size Measurement」 · lememe 「(...) (CLOSE)」 · the-museum-visitor 「(INFO)RMATION DETAIL」
#     beslow 「상품 설명 …」 · generalidea 「상품상세 리얼 착용핏 영상에서 확인해보세요!」 · stand-oil 「(상품 상세설명 )」
#     vibrate 「PAYMENT INFO」 · noice 「세부 사항 상품정보 제공고시 관리 및 유지 방법」 · foeto 「상품수정」(관리자 단추)
#     unionblue 「aaaaaaaaa」(자리 채움 글자) · lememe 「_imgTag_」
# 낱말 자리에 경계를 넣어 앞뒤 글은 따로 읽는다(그 자리에서 자르면 뒤의 옷 이야기를 잃는다).
_UI_LABEL = re.compile(
    r"자세히\s?보기|(?:(?:Function|Material)\s+)?(?:상품\s?)?상세\s?설명\s?참조|\(\s*상품\s?상세\s?설명\s*\)|"
    r"(?:상품\s?)?상세\s?설명(?:\s?정보)?(?=\s|$)|상품\s?상세(?:\s?정보)?(?=\s|$)|(?:^\s*|\bInformation\s+)상품\s?설명(?=\s|$)|"
    r"\bService\s+Info\b|(?:CLOSE\s+)?\bSize\s+Measurement\b|\bMaterial\s*&\s*Care\b|\bClothing\s+Shoes\s+This\b|"
    r"더\s?보기(?=\s*(?:$|소재|[A-Z]))|\bQUANTITY\s*:|\bDescription\s+Size(?:\s+Fabric)?\b|^\s*Size\s+Fabric\b|"
    r"\(\s*\.\.\.\s*\)\s*\(\s*CLOSE\s*\)|\b(?:INFO)?RMATION\s+DETAIL\b|\bPAYMENT\s+INFO\b|"
    r"(?:세부\s?사항\s*)?상품\s?정보\s?제공\s?고시(?:\s*관리\s?및\s?유지\s?방법)?|상품\s?수정|"
    r"\bWish\s+기본\s?정보|기본\s?정보(?=\s*$)|구매\s?하기(?=\s+[A-Z]|\s*$)|CUSTOMER\s+SERVICE(?:\s+CENTER)?|"
    r"MANUFACTURER\s+INFORMATION\s*\+?|_?imgTag_?|\b([A-Za-z])\1{5,}\b|"
    r"(?:Repairs\s+)?Other\s+Colou?r\b|RECOMMEND\s+ITEMS?|You\s+may\s+also\s+like|STYLING\s+ITEMS?|"
    r"함께\s?보면\s?좋은\s?상품|코디\s?및\s?추천\s?제품|연관\s?상품|Related\s+Products?|함께\s?구매할\s?수\s?있는\s?제품(?:\s?닫기)?|"
    # 배송 안내 조각 — nothing-written 「(주말/공휴일 제외) COTTON 40% …」 456편(조각 뒤의 혼용률은 살린다)
    r"\(?\s*주말\s?[/,]?\s?공휴일\s?제외\s*\)?|\bFit\s*&\s*Sizing\b", re.I | re.M)
# 줄바꿈이 사라진 영문 불릿 — crank 「Boots cut lineLow rised corduroy pantsThree mini pocketsCOTTON 100%」(104편).
# 소문자로 시작한 낱말 끝에 대문자가 붙을 때만 끊는다 — 「LambsWool」「PertexQuantum」「PrimaLoft」 같은 상표 이름은 대문자로 시작한다.
_GLUED_EN = re.compile(r"(?<![A-Za-z])([a-z]{3,})(?=[A-Z](?:[a-z]{2}|[A-Z]{2}))")
# 한글 문장 끝에 영어 번역이 띄어 쓰지 않고 붙는다 — far-from-what 「… 균형감을 더합니다.A hooded double-breasted …」
_GLUED_TRANSLATION = re.compile(r"(?<=[가-힣][.!?])(?=[A-Z][a-z])")


def _display_blocks(text: str) -> str:
    # _parts도 세 칸 이상을 경계로 본다. 긴 공백을 먼저 접어 경계 탐색의 제곱 시간 후퇴를 막는다.
    text = re.sub(r"[ \t]{3,}", "\n", text)
    text = re.sub(r"\n(?:[ \t]*\n)+", "\n", text)
    text = _UI_LABEL.sub("\n", text)
    text = _GLUED_EN.sub(r"\1\n", text)
    text = _GLUED_TRANSLATION.sub("\n", text)
    # UI/안내가 같은 행에 붙어 있어도 그 앞뒤 정상 특징은 독립 구역으로 읽는다.
    text = re.sub(r"상품상세\s+상세설명\s*", "\n", text)
    text = re.sub(r"\bCLOSE\s+(?=[가-힣])", "\n", text)
    text = re.sub(r"(?=사이즈\s*가이드|DO\s+NOT\s+WASH|\bSIZE\s*\(\s*CM\s*\)|\b(?:Payment|Delivery)\s+information\b)", "\n", text, flags=re.I)
    text = re.sub(r"(?i)\bPayment\s+information\s*", "\n", text)
    text = re.sub(r"(?=사이즈\s+(?:FREE|[SML](?:\s|\()))", "\n", text, flags=re.I)
    text = re.sub(rf"(?i)\bfabric\s+({FIBER})(?=\s+(?:color|size)\b)", r"원단 \1", text)
    text = re.sub(r"\{\s*(?:(?:opacity|transform|display|visibility)\s*:[^{};]+;?\s*)+\}", "\n", text, flags=re.I)
    text = re.sub(r"총\s*평점\s*[\d.]+\s*전체\s*리뷰\s*\d+", "\n", text)
    text = re.sub(r"(?:제조사\s*[:：]\s*(?:\(주\)|주식회사)?[가-힣A-Za-z0-9㈜&._-]{2,40}|제조년월\s*[:：]\s*\d{4}[./-]\d{1,2})", "\n", text)
    text = re.sub(r"서울\s+[가-힣]+구\s+[가-힣]+로\s*\d+(?:\s+[가-힣]+빌딩\s*\d+층(?:\s+[가-힣]{2,12})?)?", "\n", text)
    # 소제목 경계를 먼저 보존해 특징·소재·관리 문장이 한 덩이가 되지 않게 한다.
    text = re.sub(r"\+(?:shoes\s+(?:spec|detail)|size\s+tip)\s*", "\n", text, flags=re.I)
    text = re.sub(r"\[\s*(?:소재|디테일|Size)\s*\]", "\n", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z])(?=(?:MAIN\s+MATERIAL|SUB\s+MATERIAL\d*|SHELL)\s*[:：])", "\n", text, flags=re.I)
    text = re.sub(r"(?i)\blong\s+length\s+COMMENT\s+", "\n", text)
    text = re.sub(r"(?i)(?:^|\n)DETAIL\s+", "\n", text)
    text = re.sub(r"(?i)\bInstruction\s+(?=[가-힣])", "", text)
    text = re.sub(r"(?i)\bProduct\s+(?:Info|Information|Description)\b(?!\s+DETAIL\b)", "\n", text)
    text = re.sub(r"상품추가설명\s*번역정보", "\n", text)
    text = re.sub(r"상품소재\s*", "\n", text)
    text = re.sub(r"\s*상품간략설명\s*", "\n", text)
    text = re.sub(r"(?=\[Made\s+to\s+Order\s+Collection\])", "\n", text, flags=re.I)
    text = re.sub(r"\[story\]", "\n", text, flags=re.I)
    text = re.sub(r"(?i)\s+Product\s+Code\s*:\s*[A-Z0-9-]+", "\n", text)
    # 평점 위젯의 점수별 비율은 설명이 아니다.
    text = re.sub(r"(?:[1-5]점\s+\d+%\s*){3,}", "\n", text)
    text = re.sub(r"(?<=[가-힣])\n+(?=<[^>\n]{1,100}>\n?입니다[.])", " ", text)
    text = re.sub(r"(<[^>\n]{1,100}>)\n+(입니다[.])", r"\1 \2", text)
    text = _MODEL_BODY.sub("\n", text)
    text = _SIZE_HEADERS.sub("\n", text)
    # 같은 헤더 뒤 여러 사이즈 값이 붙은 표(Chest 60 62.5 ...).
    multi = rf"{_DIM}\s*(?:\([^()\n]{{1,24}}\))?\s*[:：]?\s*{_VALUE}(?:\s+{_VALUE})+"
    text = re.sub(rf"(?:{multi})(?:[\s/,;]+{multi})+", "\n", text, flags=re.I)
    text = re.sub(rf"(?im)^\s*(?:{_DIM}\s*[/|,]?\s*){{3,}}(?:\([^()]*\))?\s*$", "\n", text)
    text = _SIZE_CELLS.sub("\n", text)
    text = re.sub(r"[1-5]\s*사이즈\s*(?=\n|$)", "\n", text)
    text = re.sub(r"(?i)(?:SIZE\s*&\s*FIT\s*)?SIZE\s+(?:ONE\s+SIZE|[XSML]\s*(?:,\s*[XSML])*|DETAIL(?:\s+[1-5]사이즈)?|(?:[1-5]\s*:\s*)+|spec\s+O)\s*(?=\n|$)", "\n", text)
    text = re.sub(r"(?i)\s+SIZE\s*&\s*FIT\s+SIZE\s*(?=\n|$)", "\n", text)
    # 치수표의 고립된 셀도 원문 행 전체가 셀일 때만 제거한다.
    text = re.sub(rf"(?im)^\s*{_SIZE_PREFIX}{_CELL}\s*$", "\n", text)
    # 붙여 쓴 안내/소재 라벨 앞에서 경계를 만든다. MA-1, 2-WAY는 대상이 아니다.
    text = re.sub(r"(?=※|\*퍼제품|(?<!\s)-실측\s*사이즈|단위는\s*cm이며)", "\n", text)
    text = re.sub(r"(?<=%)(?=CARE\b)", "\n", text, flags=re.I)
    text = re.sub(r"(?=(?:WASHING\s+TIP|Care\s+Instructions)\b)", "\n", text, flags=re.I)
    # 원문 줄바꿈으로 끊긴 수식 관계만 잇는다. 독립 불릿은 그대로 둔다.
    text = re.sub(r"(?<=[가-힣])\n(?=(?:및|또는|가능한|가능하며|안에서|할\s*수|부분은)\s)", " ", text)
    text = re.sub(r"(?i)(?:Size\s*&\s*Fit\s+Guide\s*(?:\(cm\)\s*)*(?:FREE|(?:SIZE\s+\d\s*)+))(?=\n|$)", "\n", text)
    text = re.sub(r"(?:\s+|^)(?:의류\s+신발|원산지)\s*(?=\n|$)", "\n", text)
    # 다음 줄에 실제 소재 값이 있으면 LINING은 빈 꼬리가 아니라 그 값의 부위다.
    text = re.sub(rf"(?i)\b(OUTSHELL|SHELL|LINING)\s*(?:\n|[-–—*|,/])\s*(?=(?:{FIBER})\s*\d{{1,3}}\s*%|\d{{1,3}}\s*%\s*(?:{FIBER}))", r"\n\1 ", text)
    return "\n".join(tidy_line(ln) for ln in text.splitlines())


def clean(text: str, acc: bool = False, mat: list | None = None, names: dict | None = None) -> str:
    """찌꺼기를 걷어내고 옷 이야기만 남긴다. 남는 게 없으면 빈 글자열.
    mat 을 주면(태거가 정한 혼용률) 맨 앞 혼용률 줄을 그것에 맞춘다(head_of 주석). None 이면 글에서 읽은 그대로.
    acc=True(잡화 상품)면 벨트·모자·가방·주얼리의 말도 받는다 — 옷에 켜면 「천연가죽의 자연스러운 현상으로
    불량 사유가 아닙니다」 같은 안내문이 가죽 낱말 때문에 옷 설명으로 들어왔다(전후 전수 161벌).
    names 는 같은 매장 상품 이름 색인(name_index) — 값을 단 추천 상품 목록을 이름째 지운다(strip_listed)."""
    out: list[str] = []
    # 렌더링 안 된 카페24 틀(알리스 「{$product_detail}」 · 어랜드 「{#title}」 · 닐비피)은 **지우기만** 한다 —
    # 문단 맨 앞에 오는 일이 많아 _JUNK 처럼 그 자리에서 자르면 뒤의 설명이 통째로 날아간다(코덱스 검증 004).
    text = _TEMPLATE.sub(" ", strip_listed(_description_text(text), names))
    text = _RETURN_EXAMPLE.sub("\n", text)
    text = _FESTIVAL_BANNER.sub("\n", text)
    text = _PLAYLIST_LINK.sub("\n", text)
    care_template = bool(_CARE_TEMPLATE.search(text))
    care_material = bool(_CARE_MATERIAL_CONTEXT.search(text))
    if care_template:
        # 관리 문단의 앞을 나눠 앞의 정상 상품 문장을 함께 버리지 않는다.
        text = _CARE_TEMPLATE.sub(lambda m: "\n" + m[0], text)
    text = _DESIGNER_HEAD.sub("\n", _LETTERED.sub(" ", _RELATED.sub(" ", text)))
    material_text = text
    text = _display_blocks(text)
    text = _KO_SIZE_RUN.sub(" ", _EN_SIZE_ROWS.sub(" ", text))
    # 긴 조각은 쉼표에서도 갈라 거른다(버릴 말을 잘게 가려내려고). 그런데 **둘 다 살아남은 이웃 조각**을 줄로 넘기면
    # 한 문장이 쉼표에서 두 특징으로 쪼개진다 — 「… 바지핏 연출 무릎 및 포켓」 / 「후면 패널에 적용된 …」
    # (9999archive-438, 앱 세션 2026-09-25). 거르는 일은 그대로 두고, 살아남은 이웃이면 쉼표로 도로 잇는다.
    last_whole = False           # 바로 앞 조각이 잘리지 않고 그대로 out 끝에 들어갔는가
    for part, comma in _parts(text, with_comma=True):
        retained_feature = False
        join, last_whole = comma and last_whole, False
        if _SECTION_HEAD.match(part) or _SIZE_HEAD.match(part):
            continue
        part = _INLINE_HEAD.sub("", part)
        if care_template and (_CARE_TEMPLATE.match(part.lstrip("( "))
                              or part.casefold() in _CARE_FIBER_HEADS
                              or re.fullmatch(r"Product\s+(?:Info|Information|Description)\s+DETAIL", part, re.I)):
            continue
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
            care = _CARE_CONTEXT.search(head)
            if care:
                meta = _CARE_MATERIAL_CONTEXT.search(head) if care_material else None
                feature = head[:meta.start()].strip() if meta else ""
                if feature and not _DANGLING.search(feature) and (says_clothes(feature, acc=acc) or feature.endswith("디자인")):
                    # 특징 + 소재/사이즈 구역 + 안내: 안내 앞까지는 유효한 본문이다.
                    head = head[:care.start()].rstrip(" ,:/")
                    retained_feature = True
                else:
                    stops = list(_SENT_STOP.finditer(head))
                    head = head[:stops[-1].end()] if stops else ""
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
        if care_template and part.casefold() in _CARE_FIBER_HEADS:
            continue
        if (len(part) < 4 and not (join and len(part) >= 2)) or (_is_table(part) and len(_PCT_FIBER.findall(part)) < 2) or _is_scale(part):
            continue
        if not says_clothes(part, acc=acc) and not (join and out) and not retained_feature:
            continue
        if part in out or any(part in p for p in out):
            continue
        if join and out:
            out[-1] += ", " + part
        else:
            out.append(part)
        last_whole = not cut
    out = drop_translation(unrepeat(out))
    got_mat = material_parts(material_text)
    head_mat = head_of(got_mat, mat)
    if mat:
        # 머리줄을 mat 으로 새로 썼어도 매장 글의 같은 혼용률(「COTTON 73% NYLON 24% …」)은 다시 적힌 말이다 — 표준 이름으로 대 본다
        pairs = {(_MAT_EQ.get(str(f), str(f)), float(v)) for g in mat if isinstance(g, dict) for f, v, *_ in (g.get("v") or [])}
    else:
        # mat 이 비어 머리줄을 안 세워도, 글에서 읽은 혼용률 덩이는 예전처럼 본문 줄에서 뗀다 — 남기면 「Barcode RR253AC014BKF
        # Details and composition COTTON 60% POLYESTER 35%」처럼 머리줄로 옮겨지던 덩이가 잡문째 본문에 드러난다(rest-recreation 1629)
        pairs = canon_pairs(_join_parts(got_mat) if mat is not None else head_mat)
    if pairs:
        # 혼용률이 다시 적힌 자리는 위에서 고른 것과 같은 말이다 — 두 번 쓰지 않는다.
        # 통째로 버리지는 않는다: 「소뿔 단추 Material : Wool 41% / Polyester 29% …」처럼
        # 앞에 옷 이야기가 붙어 오면 그것까지 잃는다. 혼용률이 시작되는 자리에서 자른다.
        kept = []
        for part in out:
            q = canon_pairs(part)
            if len(q) >= 2 and q <= pairs:
                m0 = _FIBER_PCT.search(part)
                head = re.sub(r"[\s:/|,·-]*(?:material|fabric|소재|원단|혼용률)?[\s:=]*$", "",
                              part[:m0.start()], flags=re.I).strip()
                head = re.sub(r"(?i)\s+LINING\s+Viscose\s*$", "", head)
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
        if head_mat and not any(head_mat in p for p in out):
            out.insert(0, head_mat)
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
    out = [ln for ln in out if not re.fullmatch(r"(?:소재|원단)\s*특성\s*상", ln)]
    # 조각을 빈칸으로 이어 붙이면 화면에 한 덩이로 주르륵 흐른다(사람 지적 2026-09-20).
    # 자른 경계가 곧 읽는 사람이 쉬는 자리다 — 줄로 넘겨 앱이 그리게 둔다.
    return cap_complete("\n".join(dedup_lines(out)))


# ── 되풀이 · 번역 병기 ──────────────────────────────────────────────────────────
# 아코디언 머리(「디테일」「DETAILS」)를 사이에 두고 같은 문단이 두 번 온다 — 요약 칸과 상세 칸이 같은 글을 갖고 있어서다
# (텍스트 QA 2026-10-27 — open-yy 436 · divein 417 · kirsh 121 · noice 78):
#     divein-819  「부드럽고 … 여유로운 착용감 디테일 부드럽고 … 여유로운 착용감」(한 줄 안)
#     open-yy-3067「코튼 저지 소재 … 자수 디테일」 / 「MODEL WEARS … DETAILS 코튼 저지 소재 … 자수 디테일」(앞 줄을 품은 줄)
_REPEAT_HEAD = re.compile(r"(?:^|\s)(?:디테일|상세|DETAILS?|Detail|INFO|Information|Description)\s*[:：]?\s*$", re.I)


def unrepeat(lines: list[str]) -> list[str]:
    """한 줄 안에서 앞 절반이 되풀이되면 뒤를 떼고, 앞 줄(30자 넘는)을 통째로 품은 줄에서는 그 부분을 뗀다."""
    out: list[str] = []
    for ln in lines:
        if len(ln) >= 60:
            k = ln.find(ln[:30], 20)
            if k > 0 and ln.startswith(ln[k:].rstrip()[:len(ln) - k]):
                ln = _REPEAT_HEAD.sub("", ln[:k].rstrip()).strip()
        for e in out:
            if len(e) >= 30 and e in ln and e != ln:
                rest = [_REPEAT_HEAD.sub("", x).strip(" :：|·-") for x in ln.split(e)]
                # 떼고 남은 앞뒤 토막은 옷 이야기일 때만 둔다(「MODEL WEARS … 175CM 44SIZE」 같은 머리 토막)
                ln = "\n".join(x for x in rest if len(x) >= 4 and says_clothes(x, acc=True))
        out.extend(x for x in ln.split("\n") if x)
    return out


# 한글 설명 뒤에 같은 내용의 영어 번역이 붙는다 — mmlg · far-from-what · mischief · shirter · sansan · helet · eastlogue.
# 한글 문장이 둘 넘게 있을 때만 영어 줄을 뺀다:
#   · 영어 산문 줄(한글 없음 · 여섯 낱말 넘음 · 낱말 절반 넘게 소문자) — 「A hooded double-breasted peacoat in charcoal gray wool.」
#   · 한글 줄이 셋 넘는 설명에서 영어 불릿이 셋 넘게 모여 있으면 번역 묶음이다 — eastlogue 「Slanted pockets at the front /
#     Two patch pockets at the back / …」 옆에 「전면 대각선의 포켓 디테일 / 후면 두개의 패치 포켓 / …」. 한두 줄 섞인 영어 특징
#     (「Japanese fabric」)은 둔다.
# 혼용률 · 숫자가 든 줄과 대문자뿐인 줄(색 · 사양 「BLACK DENIM」)은 두고, 영어 불릿만 쓰는 매장(helet)은 한글 문장이 없어 그대로다.
def drop_translation(lines: list[str]) -> list[str]:
    ko = [ln for ln in lines if len(_HANGUL.findall(ln)) >= 8 and len(_HANGUL.findall(ln)) >= 0.3 * len(re.sub(r"\s", "", ln))]
    if len(ko) < 2:
        return lines

    def kind(ln: str) -> str:
        t = tidy_line(ln)
        if _HANGUL.search(t) or re.search(r"[\d%]", t):
            return ""
        words = re.findall(r"[A-Za-z][A-Za-z'’-]*", t)
        low = sum(w.islower() for w in words)
        if len(words) >= 6 and low >= 0.5 * len(words):
            return "prose"
        return "bullet" if len(words) >= 3 and low >= 1 else ""
    kinds = [kind(ln) for ln in lines]
    block = len(ko) >= 3 and kinds.count("bullet") >= 3
    return [ln for ln, k in zip(lines, kinds) if not (k == "prose" or (k == "bullet" and block))]


_MINED_CARE = re.compile(r"세제|표백|건조기|다림질|드라이\s?클리닝|손세탁|물세탁|세탁|탈수|보풀|필링|이염|물빠짐|"
                         r"주의해\s?주|주의하여|권장합니다|삼가|보관하여|보관해\s?주")
_OCR_CAPS = re.compile(r"(?<![A-Za-z])[A-Z]{2,4}(?![A-Za-z])")


def from_mined(rec: dict | None, mat: list | None = None) -> str:
    """detail_from_ocr 이 그림에서 뽑아 둔 줄(crawl/detail/<slug>.jsonl)을 한 덩이로.
    mat 을 주면 그림에서 읽은 혼용률 줄도 clean 과 같이 mat 에 맞춘다(head_of)."""
    if not rec:
        return ""
    bits: list[str] = []
    got = {part: mix for part, mix in (rec.get("material") or {}).items() if "\ufffd" not in part + mix}
    head = head_of(got, mat)
    if head:
        bits.append(head)
    # 뽑아 둔 설명 문장에도 OCR 잡음이 섞인다 — 여기서 한 번 더 같은 잣대를 건다.
    for s in _description_text("\n".join(rec.get("description") or []), ocr=True).split("\n"):
        s = re.sub(r"\s+", " ", s).strip()
        if _JUNK.search(s) or _CARE_CONTEXT.search(s) or _CARE_TEMPLATE.search(s) or _is_table(s) or _is_scale(s) or not says_clothes(s):
            continue
        # 그림 글의 세탁·관리 문장은 설명이 아니다 — the-coldest-moment 는 매장 글의 세탁 한 줄을 빼자 그림의
        # 「합성세제 ASE 원단 손상을 유발할 수 있으므로 … 권장합니다」가 설명 자리로 올라왔다(2026-09-24).
        if _MINED_CARE.search(s) and not _PERFORMANCE.search(s):
            continue
        # 한글 문장에 뜻 없는 대문자 토막이 둘 이상 끼면 OCR 이 글자를 뭉갠 줄이다(「ASE … SAMA ASS」)
        if len(_OCR_CAPS.findall(s)) >= 2 and len(_HANGUL.findall(s)) > len(s) * 0.3:
            continue
        han = len(_HANGUL.findall(s))
        if han < 8 or han / max(1, len(s)) < 0.45:
            continue      # 잡음 줄은 기호·로마자가 많다
        if ocr_noise_line(s):
            continue
        s = ocr_drop_short_tokens(s)
        if s not in bits:
            bits.append(s)
    return cap_complete("\n".join(dedup_lines(bits)))


# ── 그림 글(OCR) 잡음 줄 ───────────────────────────────────────────────────────
# 그림에서 읽은 글에 남은 것(텍스트 QA 2026-10-27):
#   ① 한글 문장에 뜻 없는 영문 토막 하나 — siyazu 「깔끔한 Leds 연출합니다」 600 · rolarola 493 · areuban 「부드러운 ASUS 더했습니다」.
#      토막이 한글 낱말 자리를 먹은 것이라 토막만 빼면 문장이 깨진다 — 줄째 뺀다. 둘 이상일 때만 빼던 것(_OCR_CAPS)을 하나로 넓힌다.
#      어느 토막이 잡음인지는 **매장 글(HTML)에 쓰이는가**로 가린다 — 매장 글 11만 편에서 한 번도 안 나오는 「ASS」「SAS」
#      「AMS」는 판독 잡음이고, 「YKK」「WOOL」은 매장 글에도 많다(set_ocr_vocab). 어휘가 없을 때(크롤러)는 본 잡음 목록만 쓴다.
#   ② 체크박스 특성표 — known-better 「촉감 ㅁ 부드러움 @ 적당한 편」 651 · till-i-die 586.
#   ③ 실측 재는 법 — rolarola 「평평한 바닥에 놓고 … 측정한 사이스입니다」 723 · kiimuir-684(볼캡에 상의 재는 법).
#   ④ 후기 · 내부 메모 · 홍보 — kiimuir 「… 따뜻해서 놀랐습니다」 · curetty 「디피시 주의」 · pog-service 「문의 결과」 ·
#      kiimuir 「지금 바로 만나 보세요」 · blayer 「한 사이즈 SS 주천드립니다」.
_OCR_NOISE_SEEN = {"ASS", "SAS", "ABS", "AMS", "AAS", "SHS", "ASUS", "SSS", "HSS", "MES", "MSS", "AME", "BAS", "AWS",
                   "SOS", "ASO", "POS", "PES", "FES", "HES", "SES", "AES", "LAS", "WAS", "AHS", "ANS", "SAE", "SUAS",
                   "Pets", "Peds", "Leds", "Als", "VKK", "FTW", "AZO", "ATES", "OHSS", "Oss", "ows", "Aes", "Oes"}
_OCR_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2,5}(?![A-Za-z0-9])")
_OCR_GRID = re.compile(r"ㅁ\s|@|\bOo\b|\(>|[□■☐☑✓✔]")
_OCR_MEASURE = re.compile(
    r"측정(?:합니다|한다|하였|했|한\s|된\s|하여|시\s)|즉정|평평한\s?바닥|재는\s?방법|사이\s?스입니다|"
    r"(?:총장|총\s?기장|어깨\s?단면|가슴\s?단면|소매\s?장|소매\s?길이|허리\s?단면|밑위|허벅지\s?단면|밑단\s?단면)\s*[:：]")
_OCR_TALK = re.compile(
    r"(?:네요|예뻐요|좋아요|샀어요|볼게요|했어요|었어요|았어요|놀랐(?:습니다|어요)|입니당|ㅎㅎ|ㅋㅋ)[.!~?]*\s*$|[“”]|"
    r"디피\s?시|문의\s?결과|만나\s?보세요|지금\s?바로|사이즈\s?(?:추천|업\b|다운)|한\s?사이즈\s?(?:업|크게|작게|SS)|주천|"
    r"정\s?사이즈(?:로|를)?\s?(?:추천|권장)")
OCR_VOCAB: set[str] | None = None


def set_ocr_vocab(texts, min_count: int = 20) -> None:
    """매장 글(HTML 설명)에 쓰이는 짧은 영문 낱말 모음 — 그림 글의 영문 토막이 진짜 낱말인지 가리는 잣대."""
    global OCR_VOCAB
    cnt: collections.Counter = collections.Counter()
    for t in texts:
        cnt.update(w.casefold() for w in _OCR_TOKEN.findall(t or ""))
    OCR_VOCAB = {w for w, c in cnt.items() if c >= min_count}


# 체크 표시를 못 읽어 선택지만 나란히 남은 줄 — known-better 「부드러움 적당한편 부드럽지않음」「안감있음 부분안감 안감없음」(318편).
# 어느 것이 골라졌는지 모르니 정보가 아니다.
_OCR_OPTION = re.compile(r"부드러움|적당한\s?편|부드럽지\s?않음|있음|없음|부분\s?안감|보통|약간|얇음|두꺼움|아주\s?좋음|좋음|높음|낮음")


def ocr_noise_line(s: str) -> bool:
    """그림 글 한 줄이 잡음인가(위 ①~④ · 선택지만 남은 줄)."""
    if _OCR_GRID.search(s) or _OCR_MEASURE.search(s) or _OCR_TALK.search(s):
        return True
    if len(s) <= 40 and len(_OCR_OPTION.findall(s)) >= 3 and not _NOTE_END.search(s):
        # 「신축성 약간 있음 비침 없음 안감 없음」처럼 항목마다 값이 하나씩이면 골라진 값이다 — 둔다
        names = set(re.findall(r"신축성|비침|안감|두께감?|촉감|무게감?|핏", s))
        clash = ("부드러움" in s and re.search(r"부드럽지", s)) or ("있음" in s and "없음" in s and len(names) <= 1)
        if clash:
            return True
    if len(_HANGUL.findall(s)) < 0.3 * len(s):
        return False
    return any(len(w) >= 3 and _ocr_noise_token(w) for w in _OCR_TOKEN.findall(s))


def _ocr_noise_token(w: str) -> bool:
    return w in _OCR_NOISE_SEEN or (OCR_VOCAB is not None and w.casefold() not in OCR_VOCAB and not _OCR_READABLE.fullmatch(w))


def ocr_drop_short_tokens(s: str) -> str:
    """두 글자 토막(「소재로 He 골지 스트라이프」 till-i-die)은 줄을 버리지 않고 토막만 뺀다 — 두 글자는 대개 조사 · 짧은 낱말을
    잘못 읽은 것이라 빼도 문장이 읽힌다. 세 글자 넘는 토막은 낱말 자리를 먹은 것이라 줄째 뺀다(ocr_noise_line)."""
    if len(_HANGUL.findall(s)) < 0.3 * len(s):
        return s
    return re.sub(r"\s{2,}", " ", _OCR_TOKEN.sub(lambda m: "" if len(m.group()) == 2 and _ocr_noise_token(m.group())
                                                else m.group(), s)).strip()


# 「안감 없음」「신축성 있음」은 그 옷의 성질이다 — 같은 매장의 여러 벌이 같은 값을 가져도 안내문이 아니다(758벌).
_ATTR_LINE = re.compile(r"^[\s/·-]*(?:안감|신축성|비침|두께감?|핏|무게감?)\s*[:：]?\s*(?:있음|없음|보통|약간|조금|적음|많음)")


_STORE_PROMO = re.compile(r"(?:모든|전)\s*(?:금속\s*)?부자재|브랜드의\s*모든|자사\s*제품|본사|매장|고객센터")
_STORE_NOTICE = re.compile(r"상품\s*가치가\s*현저히|MAINTENANCE|열풍|원산지|상품설명\s*겉감|"
                           r"(?:장시간|보관|물에\s*담|수분에\s*노출|옷걸이에).*(?:변형|변색|수축|보관)|"
                           r"모델은.*샘플|헤어\s*용품|가장\s*적합한|정품\s*인증|공식\s*인증\s*제품|HIDDEN\s+TAG|"
                           r"지퍼폴리백.*파우더|상품\s*,\s*라벨\s*,\s*TAG.*분실|"
                           r"부자재.*상품\s*택.*패키지|사이즈는\s*재는\s*방법", re.I)


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
    return {ln for ln, c in cnt.items() if c >= lim and not _FIBER_PCT.search(ln) and not _ATTR_LINE.search(ln)
            and (_NOTE_CUE.search(ln) or _CARE_CONTEXT.search(ln) or _STORE_PROMO.search(ln) or _STORE_NOTICE.search(ln))}


# ── 매장 견본 혼용률 — 다른 상품의 소재 블록(qa27 2026-10-27) ─────────────────────────────────────
# 매장이 상세 틀에 박아 둔 혼용률이 그 옷의 것이 아니다:
#   the-coldest-moment 755/907벌 「SHELL : COTTON 46% , RAYON 46% , POLYURETHANE 8% CONTRAST : … POLYURETHANE 568%」
#     (뒤 숫자만 상품마다 다르다 — 목걸이 · 모자에도 붙었다. 라이브 확인: 페이지에 실제로 박혀 있다)
#   noice 269/406벌 「fabric :MERINO WOOL 50%NYLON 30%ACRYLIC 20%」 — 같은 페이지 「혼용률 : COTTON 100%」가 그 옷의 것이다.
# 태거는 이런 줄을 혼용률(mat)로 안 고른다(tag_items.blend_of_brand ①②⑤). 설명 글에는 남아 mat 과 어긋난 소재를 보여 줬다.
# 한 매장 설명의 20%(최소 5벌) 넘게 같은 꼴(숫자만 뺀)로 나오고, mat 이 있는 상품에 대 보아 절반 넘게 어긋나면 견본이다.
# 견본은 그 상품의 mat 과 어긋날 때(또는 mat 이 없을 때)만 지운다 — 니트에 붙은 noice 견본은 니트 자신의 혼용률과 같다.
_TPL_LABEL = r"(?:shell|outshell|contrast|lining|trim|body|fabric|material|composition|겉감|안감|배색|원단|소재|혼용률)"
_TPL_MOD = r"(?:(?:merino|organic|recycled|recycle|supima|pima|lambs|virgin|superfine|extra\s?fine|duck|goose)\s*)?"
_TPL_RUN = re.compile(rf"(?:{_TPL_LABEL}\s*[:：]?\s*)?(?:{_TPL_MOD}(?:{FIBER})\s*[:：]?\s*\d{{1,4}}\s*%(?:[\s,/·+&]|"
                      rf"\s*{_TPL_LABEL}\s*[:：]?\s*)*)+", re.I)


def comp_runs(line: str) -> list[tuple[int, int, str]]:
    """줄 속 혼용률 덩이 → [(시작, 끝, 열쇠)]. 열쇠는 숫자를 뺀 꼴(견본은 숫자만 바꿔 붙는다)."""
    out = []
    for m in _TPL_RUN.finditer(line or ""):
        if len(_FIBER_PCT.findall(m.group())) < 2:
            continue                       # 「COTTON 100%」 한 칸은 견본으로 세지 않는다 — 한 가지 원단 매장은 다 그렇다
        key = re.sub(r"\s+", " ", re.sub(r"\d+", "#", m.group().lower())).strip(" ,/·+&")
        out.append((m.start(), m.end(), key))
    return out


def _run_agrees(run: str, mat: list | None) -> bool:
    if not mat:
        return False
    have = {(_MAT_EQ.get(str(f), str(f)), float(v)) for g in mat if isinstance(g, dict) for f, v, *_ in (g.get("v") or [])}
    q = {(f, v) for f, v in canon_pairs(run) if v <= 100}
    return bool(q) and q <= have


def store_templates(items: list[tuple[str, list | None]], share: float = 0.2, min_n: int = 5) -> set[str]:
    """한 매장의 (설명, mat) 들 → 견본 혼용률 열쇠들(위 주석)."""
    texts = [(t, m) for t, m in items if t]
    seen: collections.Counter = collections.Counter()
    for t, _ in texts:
        seen.update({k for ln in t.split("\n") for _, _, k in comp_runs(ln)})
    lim = max(min_n, share * len(texts))
    cand = {k for k, c in seen.items() if c >= lim}
    if not cand:
        return set()
    checked: collections.Counter = collections.Counter()
    agree: collections.Counter = collections.Counter()
    for t, m in texts:
        if not m:
            continue
        for ln in t.split("\n"):
            for a, b, k in comp_runs(ln):
                if k in cand:
                    checked[k] += 1
                    agree[k] += _run_agrees(ln[a:b], m)
    return {k for k in cand if checked[k] >= 3 and agree[k] < 0.5 * checked[k]}


def drop_templates(text: str, mat: list | None, keys: set[str], name: str = "") -> str:
    """견본 혼용률을 지운다. 그 상품의 mat 과 같은 것은 둔다. 지우고 남은 것이 옷 이야기가 아니면(「Made In China
    TCM color block puffer jacket (brown)」 — 상품 이름 꼬리) 줄째 뺀다."""
    if not keys or not text:
        return text
    out = []
    for ln in text.split("\n"):
        runs = [(a, b) for a, b, k in comp_runs(ln) if k in keys and not _run_agrees(ln[a:b], mat)]
        if not runs:
            out.append(ln)
            continue
        for a, b in reversed(runs):
            ln = ln[:a] + " " + ln[b:]
        if name:
            ln = re.sub(re.escape(name), " ", ln, flags=re.I)
        ln = re.sub(r"(?i)\bmade\s+in\s+[a-z]+\b", " ", ln)
        # 덩이를 뺀 자리 끝에 홀로 남은 영문 이름표(「… fabric :」) — 한글 「원단」 「소재」는 문장에 쓰여 건드리지 않는다
        ln = re.sub(r"(?i)(?:^|(?<=\s))(?:shell|outshell|contrast|lining|trim|body|fabric|material|composition)\s*[:：]?\s*$", " ", ln.strip())
        ln = re.sub(r"\s+", " ", ln).strip(" :：,/·-•*|")
        if len(ln) >= 4 and says_clothes(ln):
            out.append(ln)
    return "\n".join(out).strip()


def drop_lines(text: str, lines: set[str]) -> str:
    return "\n".join(ln for ln in (text or "").split("\n") if ln not in lines).strip() if lines else text


# ── 매장 공통 문장(상용구) ──────────────────────────────────────────────────────
# store_repeats 는 안내 낱말(주의 · 세탁 · 매장 …)이 든 줄만, 매장 설명의 40% 넘게 되풀이될 때 뺀다. 그 문턱 밑에서
# 상용구가 설명을 덮고 있었다(텍스트 QA 2026-10-27 — 36곳 9,459편):
#     years-ago     「ECP 인증 제품들은 가공을 통해 수축률을 최소화한 제품들로 …」 1,061/1,596
#     anotheroffice 「코트 코트(래글런) 재킷 점퍼 패딩 …」(사이즈 가이드 탭 이름) 831/994
#     mmlg          「Mmlg 레이블의 원단은 87MM Co., Ltd.」 787/1,528 · lmood 「(11189) 경기 포천시 …」 332/753
#     modnine       「인디고 염색은 원단에 염료와 섬유가 …」 275/275(티셔츠 · 가방에도)
# 안내 낱말이 없는 상용구가 많아(브랜드 이야기 · 메뉴 · 원단 개발사) 낱말로는 못 잡는다 — **문장이 매장 안의 서로 다른
# 상품 설명 20% 넘게(적어도 20편) 나오고, 둘 이상의 갈래(상의 · 가방 …)에 걸치면** 그 매장의 상용구로 본다.
# 색만 다른 형제는 설명이 같아서, 같은 설명은 한 번만 센다(그래야 한 상품군의 문장이 상용구로 잡히지 않는다).
# 상품의 사실은 되풀이돼도 지킨다 — 혼용률 · 눈금(안감 없음) · 제조국 · 치수(cm · g)가 든 문장.
_BOILER_KEEP = re.compile(r"\d\s?(?:cm|mm|kg|g|oz|게이지|gg)\b|made\s?in|제조국|원산지|\d+\s?수\b", re.I)


def sentences(line: str) -> list[str]:
    """줄을 문장으로 — 상용구는 줄 가운데에 붙어 오기도 한다(「COTTON 100% 물류 센터에 반송된 …」)."""
    return [s.strip() for s in _SENT_CUT.split(line or "") if s.strip()]


def _boiler_key(s: str) -> str:
    return re.sub(r"[\W_]+", "", (s or "").casefold())


def store_boilerplate(items: list[tuple[str, str]], min_n: int = 20, share: float = 0.2) -> set[str]:
    """한 매장의 (설명, 갈래) 들에서 상용구 문장의 열쇠(_boiler_key) 모음."""
    texts: dict[str, set[str]] = collections.defaultdict(set)
    for t, cat in items:
        if t:
            texts[t].add(cat or "")
    if len(texts) < min_n:
        return set()
    cnt: collections.Counter = collections.Counter()
    cats: dict[str, set[str]] = collections.defaultdict(set)
    for t, cs in texts.items():
        seen = set()
        for ln in t.split("\n"):
            for s in sentences(ln):
                k = _boiler_key(s)
                if len(k) < 12 or k in seen:
                    continue
                if _FIBER_PCT.search(s) or _REVERSE_FIBER_PCT.search(s) or _ATTR_LINE.search(s) or _BOILER_KEEP.search(s) \
                        or _fact_like(s):
                    continue
                seen.add(k)
                cnt[k] += 1
                cats[k] |= cs
    lim = max(min_n, share * len(texts))
    return {k for k, c in cnt.items() if c >= lim and len(cats[k]) >= 2}


def _fact_like(s: str) -> bool:
    """짧은 특징 조각은 매장 안에서 되풀이돼도 그 상품의 사실이다 — mmlg 「Regular relaxed fit」 669편 · moif 「여유 있는
    오버사이즈 실루엣.」 · xlim 「2 FRONT POCKETS」 · birthdaysuit 「고밀도로 직접 편직한 립(RIB) 원단 사용」(전수 미리보기 2026-10-27).
    안내 낱말이 없고, 말로 끝맺지 않고(「…니다」), 옷 이야기를 하는 45자 안의 조각이다."""
    # 같은 원단·핏을 여러 품목에 쓴 긴 완결문도 사실이다. 문장 길이와 말끝만으로 지우지 않는다(2026-10-28).
    feature = re.search(r"(?:오버|슬림|루즈|레귤러|릴랙스|와이드|크롭|세미|박시)[ -]?(?:사이즈|핏)|"
                        r"실루엣|포켓|주머니|넥라인|소매|밑단|자수|스티치|단추|스트링|안감|혼방\s?소재", s)
    if _PERFORMANCE.search(s):
        return True
    # 다만 「모든 · 모두 · 전 제품」으로 브랜드 전체를 말하는 문장은 상품의 사실이 아니다 — lmood 「봉제사는 모두 일본수입산
    # 코아사를 사용하며 주머니 안감 또한 …」가 넥타이 카드에까지 새었다(텍스트 QA 2026-10-27).
    brand_wide = re.search(r"모든|모두|전\s?제품|전\s?상품|자사|브랜드의", s)
    return (not _NOTE_CUE.search(s) and not _CARE_CONTEXT.search(s) and not brand_wide
            and (bool(feature) or (len(s) <= 45 and not _NOTE_END.search(s) and says_clothes(s, acc=True))))


def drop_boilerplate(text: str, keys: set[str]) -> tuple[str, int]:
    """상용구 문장을 뺀 글과 뺀 문장 수."""
    if not keys or not text:
        return text, 0
    out, n = [], 0
    for ln in text.split("\n"):
        ss = sentences(ln)
        kept = [s for s in ss if _boiler_key(s) not in keys]
        n += len(ss) - len(kept)
        if len(kept) == len(ss):
            out.append(ln)
        elif kept:
            out.append(" ".join(kept))
    return "\n".join(out).strip(), n


# ── 다른 상품의 혼용률 틀 ───────────────────────────────────────────────────────
# 매장 틀에 박힌 남의 혼용률이 상품마다 붙어 온다(텍스트 QA 2026-10-27 — 라이브 페이지로 확인):
#     the-coldest-moment 755/907 「SHELL : COTTON 46% , RAYON 46% , POLYURETHANE 8% CONTRAST : … POLYURETHANE 568%」
#                         — 상품 제 것(「SHELL : COTTON 100%」) 옆에 또 붙고, 목걸이에도 붙는다. 끝 숫자가 상품마다 깨져 있다.
#     noice 323/405       「fabric :MERINO WOOL 50%NYLON 30%ACRYLIC 20%」 — 같은 쪽의 「혼용률 : COTTON 100%」와 어긋난다.
# 혼용률 덩이를 부위(SHELL · CONTRAST …)로 나눠 센다. 한 부위 덩이가 매장 설명의 25% 넘게(적어도 20편) 나오고 그 덩이를 단
# 상품이 옷과 잡화에 다 걸치면 매장 틀이다. 그 틀은 ① 잡화에서, ② 같은 설명에 다른 섬유의 혼용률이 따로 있을 때,
# ③ 한 부위의 합이 100을 넘을 때 덩이째 지운다. 옷인데 다른 혼용률이 없으면 둔다(그 옷의 진짜 혼용률일 수 있다).
# 아뜰리에 드 루멘의 「Cow Leather / Polyester 92% Spandex 8%」는 가방 안감이라 틀이 아니다(잡화에만 걸친다).
_BLEND_ATOM = rf"(?:(?:{FIBER})[A-Za-z가-힣 ()]{{0,12}}?\s*\d{{1,3}}\s?%|\d{{1,3}}\s?%\s*(?:{FIBER})[A-Za-z가-힣]{{0,8}})"
_BLEND_RUN = re.compile(rf"(?:(?:[A-Za-z가-힣]{{2,12}}\d?\s*[:：-]\s*)?{_BLEND_ATOM}\s*[,/+]?\s*){{2,}}", re.I)
_BLEND_LABEL = re.compile(r"(?:^|(?<=\s))[A-Za-z가-힣]{2,12}\d?\s*[:：-]\s*(?=\S)")
_ATOM_RX = re.compile(_BLEND_ATOM, re.I)


def _blend_spans(run: str) -> list[tuple[int, int, str, str]]:
    """덩이를 부위 이름에서 가른 (시작, 끝, 부위 이름, 열쇠)들. 숫자가 깨진 꼬리(「568%」)가 열쇠를 흔들지 않게 부위마다 센다."""
    labels = {m.start(): re.sub(r"[\s:：-]+$", "", m.group()).strip().casefold() for m in _BLEND_LABEL.finditer(run)}
    cuts = sorted({0} | set(labels))
    return [(a, b, labels.get(a, ""), _boiler_key(run[a:b])) for a, b in zip(cuts, cuts[1:] + [len(run)])
            if len(_ATOM_RX.findall(run[a:b])) >= 1]


def _blend_segments(run: str) -> list[str]:
    return [k for a, b, _, k in _blend_spans(run) if len(_ATOM_RX.findall(run[a:b])) >= 2]


def blend_templates(items: list[tuple[str, bool]], min_n: int = 20, share: float = 0.25) -> set[str]:
    """한 매장의 (원문 설명, 잡화인가) 들에서 매장 틀인 혼용률 부위 덩이 열쇠들."""
    texts: dict[str, set[bool]] = collections.defaultdict(set)
    for t, acc in items:
        if t:
            texts[t].add(bool(acc))
    if len(texts) < min_n:
        return set()
    cnt: collections.Counter = collections.Counter()
    kinds: dict[str, set[bool]] = collections.defaultdict(set)
    for t, accs in texts.items():
        for k in {k for m in _BLEND_RUN.finditer(t) for k in _blend_segments(m.group())}:
            cnt[k] += 1
            kinds[k] |= accs
    lim = max(min_n, share * len(texts))
    return {k for k, c in cnt.items() if c >= lim and kinds[k] == {True, False}}


def _fibers(s: str) -> set[str]:
    out = set()
    for a in _ATOM_RX.findall(s or ""):
        w = re.sub(r"[\d%()]+", " ", a).split()
        out.add(_fiber(" ".join(w)) or (_fiber(w[-1]) if w else None) or (w[-1].lower() if w else ""))
    return out - {""}


_BLEND_LEAD_RX = re.compile(r"(?i)(?:(?<![A-Za-z가-힣])(?:fabric|material|소재|원단|혼용률|shell|겉감)\s*[:：]?\s*)?"
                           r"(?:(?<![A-Za-z])(?:merino|organic|recycled|lambs|virgin|supima|pima)\s*)?$")


_BLEND_TAIL = re.compile(rf"(?:\s*[,/+]?\s*(?:{FIBER})[A-Za-z ]{{0,12}}?\s*\d{{1,5}}\s?%)+", re.I)


def _pairs(s: str) -> set[tuple[str, str]]:
    out = set()
    for a in _ATOM_RX.findall(s or ""):
        w = re.sub(r"[\d%()]+", " ", a).split()
        f = _fiber(" ".join(w)) or (_fiber(w[-1]) if w else None) or (w[-1].lower() if w else "")
        pct = re.search(r"\d{1,3}", a)
        if f and pct:
            out.add((f, pct.group()))
    return out


def drop_blend_template(text: str, keys: set[str], acc: bool) -> str:
    """매장 틀 혼용률을 지운다(blend_templates 주석의 세 경우). 틀은 열쇠가 맞은 부위부터 이어지는 부위들이다 —
    같은 부위 이름이 다시 나오면 거기서 상품 제 혼용률이 시작된다(「SHELL : NYLON 100% SHELL : COTTON 46% …」)."""
    if not keys or not text:
        return text
    cut: list[tuple[int, int]] = []
    for m in _BLEND_RUN.finditer(text):
        spans = _blend_spans(m.group())
        seed = next((i for i, x in enumerate(spans) if x[3] in keys), None)
        if seed is None:
            continue
        end, seen = seed, {spans[seed][2]}
        while end + 1 < len(spans) and spans[end + 1][2] and spans[end + 1][2] not in seen:
            end += 1
            seen.add(spans[end][2])
        a = m.start() + spans[seed][0]
        # 덩이 앞에 남는 이름표 · 꾸밈말도 같이 — noice 「fabric :MERINO WOOL 50%…」의 「fabric :MERINO」
        lead = _BLEND_LEAD_RX.search(text[:a])
        cut.append((lead.start() if lead else a, m.start() + spans[end][1]))
    for a, b in reversed(cut):
        # 숫자가 깨진 꼬리(the-coldest-moment 「, POLYURETHANE 1020%」)는 덩이 문법에 안 맞아 덩이 밖에 남는다 — 같이 지운다
        tail = _BLEND_TAIL.match(text, b)
        if tail:
            b = tail.end()
        tpl = text[a:b]
        own = _pairs(text[:a] + "\n" + text[b:])
        broken = bool(re.search(r"\d{4,}\s?%", tpl)) or any(
            sum(int(x) for x in re.findall(r"(\d{1,3})\s?%", tpl[x0:x1])) > 110 for x0, x1, _, _ in _blend_spans(tpl))
        # 같은 설명에 섬유나 비율이 다른 혼용률이 따로 있으면 틀이 남의 것이다(noice 「혼용률 : NYLON 48% WOOL 32% …」 옆의
        # 「MERINO WOOL 50% NYLON 30% …」 — 섬유는 같고 비율이 다르다)
        if acc or broken or (own and not own <= _pairs(tpl)):
            text = text[:a] + " " + text[b:]
    return text


# ── 같은 매장 다른 상품의 이름 · 값 목록 ────────────────────────────────────────────
# 「함께 보면 좋은 상품」「RECOMMEND ITEM」「You may also like」 위젯이 상품 이름과 값을 줄줄이 달고 들어온다
# (텍스트 QA 2026-10-27 — 가격 789편 · 추천 목록 490편 · 다른 상품 이름 609편):
#     mango-many-please 「NILAH LAYERED TEE_PALE PINK KRW 49,000 KRW 49,000 NILAH LAYERED TEE_MUTE CREAM KRW 49,000 …」
#     nick-nicole 「… WASHING DENIM MINI SKIRT_BLUE 78,000원 46,800원 CORDUROY TWO TUCK BERMUDA PANTS_BROWN …」
#     vunque 「STYLING ITEM [아울렛] Groove Leather Strap (그루브 레더 스트랩) Black 87,000원 145,000원 …」
# 값은 _PRICE 로 그 자리에서 잘리지만 이름은 옷 낱말(TEE · SKIRT)을 달고 있어 설명으로 남았다. 위젯에 서는 이름은 **그 매장의
# 다른 상품 이름**이다 — 값 바로 앞에 그 매장 상품 이름이 서 있으면 이름과 값을 같이 지운다. 아는 이름이 없으면 값만 지운다.
_PRICE_TOK = re.compile(r"(?:(?:KRW|₩)\s?\d{1,3}(?:,\s?\d{3})+(?:\.\d+)?|\d{1,3}(?:,\s?\d{3})+\s?(?:원|won)?)"
                        r"(?:\s*\(?\s*\d{1,2}\s?%\s*\)?)?", re.I)


def _name_norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def name_index(names) -> dict[str, list[str]]:
    """매장 상품 이름들 → 끝 다섯 글자로 찾는 색인(긴 이름부터)."""
    idx: dict[str, list[str]] = collections.defaultdict(list)
    for n in {_name_norm(x) for x in names if x}:
        if len(n) >= 6:
            idx[n[-5:]].append(n)
    for v in idx.values():
        v.sort(key=len, reverse=True)
    return idx


def strip_listed(text: str, idx: dict[str, list[str]] | None) -> str:
    """값 앞에 선 같은 매장 상품 이름과 그 값들을 지운다. 통화 표시가 없는 수(「39,000 39,000」)는 이름이 앞설 때만."""
    if not idx or not text:
        return text
    spans: list[tuple[int, int]] = []
    for m in _PRICE_TOK.finditer(text):
        # 빈칸을 접어 이름과 맞춰 보되 지울 자리는 원문에서 센다 — 원문의 세 칸 빈칸은 _SPLIT 의 경계라 접으면 안 된다
        # (frizmworks 「… (해외배송 가능상품)   은은한 광택감이 …」가 안내 조각과 한 덩이가 돼 첫 문장을 잃었다)
        pre = text[max(0, m.start() - 200):m.start()]
        pre_n = _name_norm(pre)
        name = next((n for n in idx.get(pre_n[-5:], ()) if pre_n.endswith(n)), None)
        if name:
            i, need = len(pre), len(name.replace(" ", ""))
            while i > 0 and need > 0:
                i -= 1
                if not pre[i].isspace():
                    need -= 1
            spans.append((m.start() - len(pre) + i, m.end()))
        elif re.match(r"(?:KRW|₩)|.*(?:원|won)", m.group(), re.I) or (spans and m.start() - spans[-1][1] <= 2):
            spans.append((m.start(), m.end()))
    if not spans:
        return text
    out, last = [], 0
    for a, b in sorted(spans):
        if a > last:
            out.append(text[last:a])
        out.append(" ")
        last = max(last, b)
    out.append(text[last:])
    return "".join(out)


def drop_name_lines(text: str, names: set[str], own: set[str] = frozenset()) -> str:
    """같은 매장 다른 상품의 이름(_boiler_key)으로만 된 줄을 뺀다 — 추천 목록 머리를 뗀 뒤 남는 이름
    (「RECOMMEND ITEM 블리치 블루 체크 셔츠 [블루]」 begin202 · 「코디 및 추천제품 모노 그레인 스톤 네크리스 [BLACK]」 blayer)과
    색 형제 이름을 잇달아 적은 줄(heritagefloss 「CREST WASHED CORDUROY PANTS (W) / Brown CREST … / Blue …」)."""
    if not names or not text:
        return text
    by_head: dict[str, list[str]] = collections.defaultdict(list)
    for n in set(names) | set(own):
        if len(n) >= 8:
            by_head[n[:8]].append(n)

    def only_names(ln: str) -> bool:
        k = _boiler_key(ln)
        if k in names:
            return True
        if _HANGUL.search(ln) and _NOTE_END.search(ln):
            return False
        rest, i, hit = [], 0, False
        while i < len(k):
            n = next((n for n in sorted(by_head.get(k[i:i + 8], ()), key=len, reverse=True) if k.startswith(n, i)), None)
            if n:
                i, hit = i + len(n), True
            else:
                rest.append(k[i])
                i += 1
        return hit and len(rest) <= 3 and k not in own   # 제 이름 한 줄은 앱이 뺀다(is_name_only 주석)
    return "\n".join(ln for ln in text.split("\n") if not only_names(ln)).strip()


# 카페24 스펙 칸의 짧은 설명 — 넘버링은 설명 칸에 후기 위젯·반품 안내만 있고 진짜 설명
# (「시곗줄을 연상시키는 체인 브레이슬릿입니다」)은 여기에만 있다(2026-09-24).
SPEC_DESC_KEYS = ("상품간략설명", "상품요약정보", "간략설명", "상품 간략설명")


# 카페24 JSON-LD 설명은 태그를 빈칸 없이 벗겨 낸다 — <br> 자리의 낱말이 붙는다(coor 「… 원단 사용내부에 면 혼방 배색소매 및 …
# 연출 가능후드 스트링 …」 약 1,000편). 같은 글이 HTML(간략설명 칸 · 본문)에는 빈칸을 갖고 있다. 두 글의 빈칸 아닌 글자가
# 같으면 HTML 쪽에만 있던 경계를 빈칸으로 되살린다 — 글자는 하나도 바꾸지 않는다. 줄로 넣지 않는다: 줄로 가르면 짧은 불릿 하나하나가
# 옷 낱말 거르개(says_clothes)에 걸려 「덩가리 트러커 형태를 재해석한 디자인」(coor) 같은 특징을 잃었다(전후 표본 2026-10-28).
def respace(glued: str, ref: str) -> str:
    """ref 의 빈칸 경계를 glued 에 되살린다. 글자열이 맞지 않으면 glued 그대로."""
    if not glued or not ref:
        return glued
    rn = re.sub(r"\s+", "", ref)
    gn = re.sub(r"\s+", "", glued)
    if len(rn) < 20:
        return glued
    pos = gn.find(rn)
    if pos < 0:
        # 본문에는 주문 칸·실측도 붙는다. 설명 전체가 본문 안에 있을 때도 같은 글의 경계만 쓴다(2026-10-28).
        offset = rn.find(gn) if len(gn) >= 20 else -1
        if offset < 0:
            return glued
        pos = -offset
    gaps, j = set(), 0
    for i, ch in enumerate(ref.strip()):
        if ch.isspace():
            if j:
                gaps.add(pos + j)
        else:
            j += 1
    out, k = [], 0
    for ch in glued:
        if not ch.isspace():
            if k in gaps and out and not out[-1].isspace():
                out.append(" ")
            k += 1
        out.append(ch)
    return "".join(out)


def best(description: str, mined: dict | None = None, acc: bool = False,
         spec: dict | None = None, mat: list | None = None, names: dict | None = None) -> tuple[str, str]:
    """보여 줄 설명과 출처. 소재뿐이면 간략설명/그림의 구체적 특징도 비교한다. mat 은 clean 주석.
    names 는 같은 매장 상품 이름 색인(name_index) — clean 주석."""
    if spec:
        description = respace(description, "\n".join(str(spec[k]) for k in SPEC_DESC_KEYS if spec.get(k)))
    shop = clean(description, acc, mat=mat, names=names)
    candidates = [(shop, "shop")]
    if has_product_feature(shop):
        return shop, "shop"
    if spec:
        brief = clean("\n".join(str(spec[k]) for k in SPEC_DESC_KEYS if spec.get(k)), acc, mat=mat, names=names)
        candidates.append((brief, "shop"))
        if has_product_feature(brief):
            text = "\n".join(dedup_lines([ln for ln in shop.splitlines() if _PCT_FIBER.search(ln)] + brief.splitlines()))
            return cap_complete(text), "shop"
    picture = from_mined(mined, mat)
    if has_product_feature(picture):
        # 매장에만 있는 부위별 성분도 함께 보존한다.
        shop_pct = [ln for ln in shop.splitlines() if _PCT_FIBER.search(ln)]
        pic = picture.splitlines()
        # mat 에 맞춘 머리줄은 매장 글 쪽에 이미 있다 — 그림 쪽 머리줄을 또 싣지 않는다(같은 혼용률을 영문 · 한글로 두 번)
        if mat and shop_pct and pic and canon_pairs(pic[0]) and canon_pairs(pic[0]) <= canon_pairs(shop_pct[0]) | {
                (_MAT_EQ.get(str(f), str(f)), float(v)) for g in mat if isinstance(g, dict) for f, v, *_ in (g.get("v") or [])}:
            pic = pic[1:]
        text = "\n".join(dedup_lines(shop_pct + pic))
        return cap_complete(text), "ocr"
    return next(((t, source) for t, source in candidates + [(picture, "ocr")] if t), ("", ""))


def has_product_feature(text: str) -> bool:
    """혼용률/안내/표제뿐인 후보만 빈약하다고 본다. 길이나 한글 여부로 고르지 않는다."""
    for line in (text or "").splitlines():
        if _CARE_CONTEXT.search(line) or (_NOTE_CUE.search(line) and _NOTE_END.search(line) and not _PERFORMANCE.search(line)):
            continue
        remainder = _FIBER_PCT.sub("", line)
        remainder = re.sub(rf"\d{{1,3}}\s*%\s*(?:{FIBER})(?![A-Za-z가-힣])", "", remainder, flags=re.I)
        remainder = re.sub(r"\b(?:shell|lining|fabric|material|main|color|black|white|navy|grey|length|long|short)\b|"
                           r"겉감|안감|혼용률|소재|원단", "", remainder, flags=re.I)
        if says_clothes(remainder, acc=True):
            return True
    return False


# ── 특징 · 안내 나누기 (앱 세션 부탁 2026-09-24) ─────────────────────────────────
# 앱이 설명 탭을 「특징」(한 줄에 하나)과 「알아 두세요」(작게)로 나눠 그린다. 앱이 모든 매장에 같은 규칙을
# 걸면 매장 글 버릇이 달라 틀리는 곳이 생겨서 창고가 나눠 싣는다: descs 한 줄에 f(특징 줄) · n(안내 문장).
# 안내는 **말투만으로 가르지 않는다** — 「비대칭 포켓은 수납성을 극대화 시킬 수 있습니다」(cayl)는 공손체지만
# 특징이다. 공손체로 끝나면서 주의·권장·차이 같은 안내 말이 든 문장, 또는 ※·* 로 시작하는 문장만 안내다.
_NOTE_END = re.compile(r"(?:니다|세요|십시오|주세요|바랍니다|드립니다|해요|어요|아요)[.!~)\]]*\s*$")
# 안내 낱말은 좁게 — 「변형이 가능한 … 아우터입니다」(sinoon)처럼 옷 이야기에도 나오는 말(변형·보관·확인)은 뺐다.
# 「특유의 · 고유의」 안의 「유의」는 안내가 아니다 — 포에토 「메리노 울 특유의 포근하고 …」 소개가 안내로 갔다(코덱스 011).
_NOTE_CUE = re.compile(r"주의|(?<![특고])유의|권장|권해|삼가|피해\s?주|금지|마세요|말아\s?주|양해|부탁|바랍니다|불량|교환|반품|"
                       r"오차|개체\s?차|편차|차이가\s?(?:있|발생|날|생)|발생할\s?수|생길\s?수|다를\s?수\s?있|상이할\s?수|"
                       r"세탁|드라이\s?[클크]|이염|물\s?빠짐|보풀|측정|실측")
_NOTE_HEAD = re.compile(r"^\s*[※*]")
_SENT_CUT = re.compile(r"(?<![Ff]rom\.)(?<=[.!?])\s+|(?:(?<=니다)|(?<=세요)|(?<=십시오))\s+")


_BILINGUAL_PAIRS = {
    "oversizedfit": ("오버사이즈핏", "오버사이즈드핏"),
    "oversizefit": ("오버사이즈핏",),
    "backflapdetail": ("후면플랩디테일",),
    "knittexturefabricpanels": ("니트텍스처원단패널",),
    "cutoutseamdetailonstrap": ("스트랩컷아웃솔기디테일",),
    "1backpocket": ("후면포켓1개",),
    "slimsilhouetteeasytolayer": ("슬림한실루엣으로레이어링에용이",),
    "biowashedcottonfabric": ("바이오워싱코튼원단사용",),
    "collaborationgraphicappliqueembroidery": ("콜라보그래픽아플리케자수",),
    "chestpocketwithinternalearphonehole": ("전면부가슴포켓내부이어폰구멍",),
    "backpocketcollaborationgraphiclabel": ("뒤포켓콜라보그래픽라벨",),
    "contrastdetailatthesleevehem": ("소매밑단배색디테일",),
    "knitusingtexturedyarnandweavestructure": ("조직감이느껴지는원사와짜임을적용한니트",),
    "fitadjustmentusingthestretchofribfabric": ("립원단의신축성을활용한핏조절",),
    "wingpaneldetailconstruction": ("윙패널디테일구조",),
    "colorblockdetailnearfrontarmholes": ("전면부암홀부근배색디테일",),
    "madefromdyedcottonjersey": ("다잉코튼저지소재", "다잉된코튼저지소재로제작"),
    "madefromdyedandcottonjersey": ("다잉된코튼저지소재로제작",),
    "astandcollar": ("스탠드카라디자인",),
    "straighthem": ("스트레이트헴",),
    "strighthem": ("스트레이트헴",),
    "ribbedhemandcuffsinsidetheneck": ("넥안쪽과밑단소매단립디테일",),
    "ykkzipper": ("ykk지퍼",),
    "ykk2wayzipper": ("ykk2way지퍼",),
    "dartpintuckdetail": ("다트핀턱디테일",),
    "pocketfrontzipperpanellayeredmaterialdetail": ("포켓및전면지퍼패널레이어드소재디테일",),
    "backpanelsignatureembroideryseam": ("후면패널시그니처자수솔기",),
    "graphicprintontheback": ("후면에그래픽프린트",),
    "lowrisebaggysilhouettewoolslacks": ("로우라이즈배기실루엣울슬랙스",),
    "rhombusmetallabeldetail": ("롬버스메탈라벨디테일",),
}


def dedup_bilingual(lines: list[str]) -> list[str]:
    norm = lambda s: re.sub(r"[\W_]+", "", s.casefold())
    ko = {norm(s) for s in lines if _HANGUL.search(s)}
    return [s for s in lines if not any(k in ko for k in _BILINGUAL_PAIRS.get(norm(s), ()))]


def split_fn(text: str) -> tuple[list[str], list[str]]:
    """(특징 줄들, 안내 문장들). 줄 안에 특징과 안내가 섞이면 문장 단위로 가른다.
    원문 불릿/문장 경계를 지키며 수식어 뒤에서 기계적으로 끊지 않는다."""
    feats: list[str] = []
    notes: list[str] = []
    text = _display_blocks(text or "")
    text = re.sub(r"(?<=\S)(?=(?:소재|원단|혼용률)\s*[:：])", "\n", text)
    for line in text.split("\n"):
        line = tidy_line(line)
        if not line:
            continue
        for sent in _SENT_CUT.split(line):
            sent = tidy_line(sent)
            if not sent:
                continue
            if (_CARE_CONTEXT.search(sent)
                    or (_NOTE_END.search(sent) and _NOTE_CUE.search(sent) and not _PERFORMANCE.search(sent))
                    or (_NOTE_HEAD.match(sent) and _NOTE_CUE.search(sent))):
                notes.append(re.sub(r"^\s*[*]\s*", "", sent))
            else:
                # 단어 뒤 빈칸은 문장 경계가 아니다(구조 안에서, 연출 가능한 등).
                feats.append(sent)
    feats = dedup_bilingual(dedup_lines(feats))
    notes = dedup_lines(notes)
    return feats, notes


def is_name_only(text: str, name: str) -> bool:
    """설명이 상품 이름 줄뿐인가 — 「Half sleeve puff cardigan in Pink」. 앱은 이름을 상세 맨 위에 크게 쓰므로
    설명이 그 한 줄뿐이면 빈 설명으로 내보낸다(앱 세션 2026-09-24, 전수 931편)."""
    # 이름 뒤에 실제 성분이 붙은 줄까지 이름으로 버리지 않는다.
    if _FIBER_PCT.search(text or "") or _REVERSE_FIBER_PCT.search(text or ""):
        return False
    norm = lambda s: re.sub(r"[\s\W_]+", "", (s or "").lower())
    nm = norm(name)
    lines = [norm(x) for x in (text or "").split("\n") if norm(x)]
    if not nm or not lines:
        return False
    return all(x == nm or (len(x) >= 4 and (x in nm or nm in x) and min(len(x), len(nm)) / max(len(x), len(nm)) >= 0.6)
               for x in lines)


# 잡화 상품의 설명이 옷 한 벌을 소개한다 — low-classic 양말 두 켤레(10543 · 10544)의 상세 「Details」 칸에 봄버 자켓 글이
# 들어 있다(「세미-오버 핏 봄버 자켓 / 워시드 코튼 소재 / …」). 매장 페이지 자체가 틀렸다(라이브 확인 2026-10-27) — 틀린 글보다
# 빈 설명이 낫다. 줄 **끝**이 옷 이름인 줄(그 줄의 주어가 옷)만 본다 — 「자켓 위에 크로스로 매치하기 좋은 백」처럼 옷을 곁들여
# 말하는 잡화 설명은 끝이 잡화 이름이라 안 걸린다. 이름이나 설명에 잡화 낱말이 하나라도 있으면 두지 않는다.
_GARMENT_END = re.compile(r"(?:봄버|블루종|자켓|재킷|코트|점퍼|패딩|팬츠|슬랙스|셔츠|블라우스|니트|가디건|원피스|스커트|티셔츠)"
                          r"(?:\s*입니다\.?)?\s*$")


def describes_garment(text: str, name: str) -> bool:
    """잡화 상품의 설명이 옷을 소개하는가(위 주석). 이름에 옷 낱말이 있으면(갈래가 틀렸을 수 있다) 보지 않는다."""
    if not text or _GARMENT_WORD.search(name or ""):
        return False
    lines = [ln for ln in text.split("\n") if ln and not _FIBER_PCT.search(ln)]
    if not lines or any(_HARD_ACC.search(ln) or _SOCKS.search(ln) for ln in lines):
        return False
    return bool(_GARMENT_END.search(lines[0]) or _GARMENT_HEAD.match(lines[0]))


# 불릿을 빈칸으로 이어 붙인 한 줄(「세미-오버 핏 봄버 자켓 워시드 코튼 소재 …」)은 줄 끝이 아니라 **머리**에 옷 이름이 선다.
# 핏 말(「… 핏 봄버 자켓」) 뒤의 옷 이름만 본다 — 「니트 소재의 머플러」처럼 소재로 쓰인 옷 낱말은 안 걸린다.
_GARMENT_HEAD = re.compile(r"^.{0,20}?(?:핏|fit)\s.{0,10}?(?:봄버|블루종|자켓|재킷|코트|점퍼|패딩|팬츠|슬랙스|셔츠|블라우스|니트|가디건|원피스|스커트|티셔츠)"
                           r"(?=\s)(?!\s*(?:위|와|과|에|이랑|하고|코디|매치|안|속|밖|함께|레이어드))")


_SOCKS = re.compile(r"(?i)socks?|양말|삭스|타이츠|스타킹")
_GARMENT_WORD = re.compile(r"(?i)봄버|블루종|자켓|재킷|코트|점퍼|패딩|팬츠|슬랙스|셔츠|블라우스|니트|가디건|원피스|스커트|"
                           r"jacket|coat|jumper|pants|trousers|shirt|blouse|knit|cardigan|dress|skirt|tee\b")
