"""텍스트 → 12축 아이템 태그. data/vocab_aliases.json 하나만 읽는 규칙 기반 매칭.

무엇을 읽나: data/products_full.csv(상품 우주 — 크롤러가 걸러 낸 27k건)에 crawl/<slug>.jsonl 의
description·spec 과 crawl/ocr/<slug>.jsonl 의 ocr_text 를 붙인다. 이미지밖에 없던 매장은 OCR 글이
곧 설명이다. 결정적(LLM 없음)이라 OCR 이 더 들어오면 그냥 다시 돌린다.

결과: data/product_tags_full.json — source_url → {brand_slug, category, source_quality, tags, text_sources}.
layer-web scripts/build-discover-products.mjs 가 읽는 product_tags_seed.json 과 같은 모양.

규칙(vocab_aliases.json _context_rules 를 코드로 옮긴 것):
  · 긴 표현 우선(longest-first) + 매칭한 구간 마스킹 — '카고포켓'이 '포켓'을, '세미크롭'이 '크롭'을 먹지 않게.
  · 한글 1~2글자 별칭('면','울','마','진','리브','다운')은 왼쪽이 한글이 아닐 때만(1글자는 오른쪽도).
    '측면'·'서울'·'올리브'·'버튼다운'이 소재로 잡히는 것을 막는다. 영문은 단어 경계(\b).
  · _text_blocklist(시어링, 레이어드 스타일링…)는 본문 매칭 전에 마스킹.
  · color 는 본문이 아니라 name + representative_color + spec 의 색상 값에서만. _color_blocklist 먼저 마스킹.
  · n부: '소매'가 따르면 sleeve_length.칠부소매, 팬츠면 length.버뮤다(버뮤다·카프리 명시어가 있으면 그 값).
  · '여유로운 실루엣': 상의 루즈핏, 하의 루즈핏+와이드.
  · 혼방: 소재가 하나만 잡혔는데 '혼방'이 있으면 폴리에스터를 더한다.
  · 단색: 매칭 아닌 추론 — source_quality ok 이고 pattern 이 비면 단색.
  · _bottoms_only(세미와이드·로우라이즈·하이웨이스트)는 하의·미분류에만.
  · Skirts 의 기장은 미니·미디·맥시·롱기장만(크롭·쇼츠·버뮤다는 상의·바지의 말이다).
    pants_type 은 Pants·Denim(·미분류)만. 가방·신발·액세서리는 옷 축(넥라인·소매·핏·기장) 없음.

사용:
    py scripts/tag_items.py                 # 전부
    py scripts/tag_items.py --brands kirsh dunst --report
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CRAWL = DATA / "crawl"
OCR = CRAWL / "ocr"
BROWSER = CRAWL / "browser"
VOCAB = DATA / "vocab_aliases.json"
OUT = DATA / "product_tags_full.json"

# 한 옷에 같이 붙을 수 없는 값. audit.py 의 CONTRADICT 와 같은 목록이다 — 감사기는 세고
# 여기서는 이름을 보고 한쪽을 지운다. 한쪽을 고치면 다른 쪽도 같이 고쳐야 한다.
CONTRADICT = [
    ("sleeve_length", "반팔", "롱슬리브"), ("sleeve_length", "슬리브리스", "롱슬리브"),
    ("sleeve_length", "슬리브리스", "반팔"),
    ("length", "크롭", "맥시"), ("length", "크롭", "롱기장"), ("length", "쇼츠", "맥시"),
    ("silhouette", "슬림핏", "오버핏"), ("silhouette", "슬림핏", "루즈핏"),
    ("silhouette", "타이트", "오버핏"),
    ("pattern", "단색", "스트라이프"), ("pattern", "단색", "체크"), ("pattern", "단색", "카모"),
]

AXES = ["neckline", "sleeve_length", "silhouette", "length", "pants_type", "material",
        "finish_wash", "design_element", "construction", "pattern", "hardware", "function", "color"]
GARMENT_AXES = {"neckline", "sleeve_length", "silhouette", "length", "pants_type"}
NON_GARMENT = {"Accessories", "Bags", "Shoes"}
GARMENT_CATEGORIES = {"Tops", "Pants", "Outerwear", "Knitwear", "Shirts", "Skirts", "Denim", "Dresses"}
# 옷 전용 축(넥라인·소매·실루엣·기장·바지종류)을 지울 품목. NON_GARMENT 보다 넓다 —
# 모자와 주얼리가 빠져 있어서 캡에 「레귤러핏」 59개, 발라클라바에 넥라인 「후드」 37개,
# 브로치에 「슬리브리스」가 붙어 있었다(합계 243개, 2026-09-06 실측).
# 보온성·통기성 갈래에는 NON_GARMENT 를 그대로 쓴다 — 기모 비니는 보온성이 맞다.
NO_GARMENT_AXES = NON_GARMENT | {"Headwear", "Jewelry"}
PANTS = {"Pants", "Denim", ""}
BOTTOMS = {"Pants", "Denim", "Skirts"}
# 목선·소매가 없는 품목. 하의와 잡화가 여기 든다(잡화는 NO_GARMENT_AXES 로도 지워진다).
# 가죽 잡화 매장의 공용 A/S 안내문이 금속 부속에까지 소재를 물려주고 있었다.
# vunque 「Metal Chain Handle Strap」 본문에서 「가죽」이 나오는 자리는 전부 그 안내문이고
# (「가죽 본연의 특성 …」·「가죽 손상의 경우 A/S 불가」), 상품 이름은 메탈 체인이라고 말한다.
# 안내문 자체는 못 지운다 — vunque 559벌 가운데 500여 벌은 그 문장이 유일한 가죽 근거이고
# 실제로 가죽 가방이다(2026-09-07 확인). 이름이 금속 부속이라고 말하는 것만 뺀다.
METAL_PART = re.compile(r"(?:메탈|metal|스틸|steel)\s*(?:위빙\s*)?체인|체인\s*(?:스트랩|핸들|strap|handle)|"
                        r"chain\s*(?:strap|handle)|불렛\s*체인|bullet\s*chain", re.I)
LEATHER_NAME = re.compile(r"가죽|leather|스웨이드|suede|누벅|레더", re.I)

# 청바지에 달린 가죽 라벨 조각이 「소재 = 가죽」이 되던 것 48벌.
#   afterpray 「코티드 와이드 데님 진」 … 「리벳 장식과 벨트 후면부 소가죽 라벨 탭」
# 붙여쓰기 예외(GLUE_PREFIX)로 「소가죽」을 읽게 되면서 함께 들어온 오탐이다. 이 옷들은
# 원문 어디에도 다른 가죽 이야기가 없다 — 증거가 라벨 조각 하나뿐이다. 가죽으로 걸러
# 청바지가 나오면 안 된다(메탈 체인 부속을 뺀 것과 같은 종류다, b90f427).
# 낱말 하나만 지운다 — 「라벨」은 부자재로 남아야 한다. 겉감이 가죽인 가방·지갑·구두
# (matin-kim 「겉감 - 소가죽 100%」 등 164벌)와 가죽을 배색으로 쓴 옷(frizmworks 「어깨
# 부분에는 천연 양가죽으로 배색하여」)은 그대로다.
# 판독기가 「가슴단면」을 「가 슴 단 면」으로 띄워 놓으면 마지막 「면」이 소재 면(코튼)이 된다 —
# 2자 별칭 규칙이 앞 글자가 한글이 아니면(공백) 통과시키기 때문이다. 「단면」은 치수를 재는
# 자리(단면 = 한쪽 폭)이지 원단이 아니다. 판매중 옷에서 181벌이 이 한 글자로 코튼이 됐다
# (2026-09-08, 근거 스캔). 「단」과 「면」 사이에 공백이 있는 꼴은 실제 글에 없다 — 그 「면」만 지운다.
# 「버튼다운」·「히든 다운 버튼」의 다운은 카라 모양(button-down)이지 충전재 다운이 아니다. 그런데
# 셔츠·스웨터 52벌이 그 낱말 하나로 material/다운(오리털)이 됐다 — 52벌 다 구스·덕·충전재 같은
# 진짜 근거는 없었다(2026-09-08). 「버튼」은 남긴다 — 버튼다운 셔츠에 단추는 실제로 있다.
BUTTON_DOWN = re.compile(r"(버튼\s*)(다운)|(다운)(\s*버튼)|(button[\s-]*)(down)", re.I)


def _mask_button_down(m: re.Match) -> str:
    g = m.groups()
    if g[0] is not None:   # 버튼 다운
        return g[0] + " " * len(g[1])
    if g[2] is not None:   # 다운 버튼
        return " " * len(g[2]) + g[3]
    return g[4] + " " * len(g[5])  # button down


OCR_SPLIT_DANMYEON = re.compile(r"단\s+면(?![가-힣])")

LEATHER_TRIM = re.compile(r"(?:소|양|합성|인조|에코|재생|송아지|천연)?가죽\s*(?:라벨|탭|패치|파이핑|트리밍|와펜)")

NECKLESS = {"Pants", "Denim", "Skirts"}
# 품목 이름이 곧 소매를 말하는 옷 — 맨투맨·후디·자켓/코트는 사실상 전부 긴소매다.
# 글로 소매를 아는 2,588벌에 대 보니 97.53% 가 롱슬리브였다(틀림 64: 반팔 46 · 퍼프소매 8 ·
# 슬리브리스 7). 니트 76.7% · 티셔츠 62.5% · 셔츠 48.2% 는 쓰지 않는다 — 반팔 니트·반팔
# 셔츠가 흔해서 짐작이 서지 않는다(2026-09-06).
# 이름에 베스트·슬리브리스·반팔이 들어가면 손대지 않는다 — 그때는 이름이 이미 말해 준다.
# 소재가 곧 기능인 것 — 다운·기모(플리스)·시어링은 따뜻하라고 쓰는 소재고, 메쉬는
# 통하라고 쓴다. 매장이 그 말을 굳이 적지 않을 뿐이다: 다운 1,362벌 가운데 보온성이
# 붙은 것은 12.9%, 기모 421벌 중 31.8%, 메쉬 351벌 중 34.5%뿐이었다(2026-09-06).
# 치마·원피스 이름의 「mini」·「미니」는 기장이다. 어휘에는 「mini skirt」처럼 붙은 꼴만
# 있어서 「MINI PLEATS SKIRT」·「PLEATS MINI CHECKED SKIRT」가 빠졌다. 이 두 품목에서는
# 낱말 순서와 무관하게 기장이다 — 기장을 아는 219벌에 대 보니 219벌 모두 미니였다.
# 다른 품목에는 쓰지 않는다: 「Mini Logo Fitted Long Sleeve T-Shirt」·「Mini Pocket
# Denim Pants」의 mini 는 로고와 주머니를 꾸미는 말이다(2026-09-06).
MINI_NAME = re.compile(r"(?<![a-z])mini(?![a-z])|미니(?!멀)", re.I)
SKIRTY = {"Skirts", "Dresses"}

WARM_MATERIALS = {"다운", "기모", "시어링"}
# 스판덱스는 함량이 적혀 있을 때만 신축성으로 본다. 숫자를 찾은 1,326벌 중 87%가 3% 이상
# 이었지만 2% 이하도 173벌 있었다 — 우븐 셔츠에 2% 든 스판은 늘어난다고 하기 어렵다.
# 숫자를 못 찾은 2,797벌은 확인할 길이 없으니 손대지 않는다.
SPAN_PCT = re.compile(r"(?:스판덱스|스판|폴리우레탄|elastane|spandex)\s*[:：]?\s*(\d{1,2}(?:\.\d)?)\s*%", re.I)

LONG_SLEEVE_KIND = re.compile(
    r"sweat\s?shirts?|맨투맨|스웨트셔츠|hoodie|hoody|후디|jackets?|자켓|재킷|"
    r"coats?|코트|blouson|블루종|parka|파카|점퍼", re.I)
# 매장이 소매를 이름에서 「L/S」·「S/S」로만 밝히는 일이 있다. 본문에는 딴 소매가 적혀
# 있어서(매장이 다른 상품 설명을 복사해 둔다) 롱슬리브이면서 반팔인 옷이 생겼다 —
# frizmworks 「OG Vintage dyeing l/s tee」 본문에 「반팔 티셔츠입니다」, espionage
# 「Jungle Fatigue S/S Outer Shirt」 본문에 롱슬리브. 이름이 이긴다.
# 계절 표시와 헷갈리면 안 된다 — 「S/S 26 COLLECTION」의 S/S 는 봄여름이라 뒤에 숫자가
# 오면 안 본다.
NAME_LS = re.compile(r"(?<![a-z0-9])l\s*/\s*s(?![a-z0-9])", re.I)
NAME_SS = re.compile(r"(?<![a-z0-9])s\s*/\s*s(?!\s*\d)(?![a-z0-9])", re.I)
NOT_LONG_SLEEVE = re.compile(
    r"vest|베스트|조끼|sleeveless|슬리브리스|민소매|나시|반팔|half\s?sleeve|"
    r"short\s?sleeve|숏슬리브|s/s|캡슬리브", re.I)

SLEEVED = {"Tops", "Shirts", "Knitwear", "Outerwear", "Dresses"}
# 치마에 쓸 수 있는 기장 값. 크롭·세미크롭·쇼츠·버뮤다·카프리는 상의·바지의 말이다.
SKIRT_LENGTH = {"미니", "미디", "맥시", "롱기장"}
SHORT_TEXT = 80
# spec 표에서 태깅에 쓸 만한 키만 — 사이즈 실측(chest/hem)·배송 표(ems/ups)는 뺀다
SPEC_KEY = re.compile(r"소재|material|fabric|composition|혼용|원단|색상|color|colour|세탁|care|간략설명|디테일|detail|핏|fit|상품명|설명", re.I)
COLOR_KEY = re.compile(r"색상|colou?r", re.I)
HANGUL = "가-힣"
CSS_RX = re.compile(r"\{[^{}]{0,80}:[^{}]{0,80}\}|border-(?:bottom|top|left|right)|\d+px solid")
NBU_RX = re.compile(r"([5-8])\s*부\s*(소매|기장|팬츠|바지)?")
LOOSE_RX = re.compile(r"여유\s?(?:로운|있는|로이)\s?(?:실루엣|핏|fit)", re.I)


def _is_hangul(s: str) -> bool:
    return bool(s) and all("가" <= c <= "힣" for c in s)


# 2자 한글 별칭 앞에 붙어도 뜻이 변하지 않는 색 낱말. 매장이 「그린카모」·「베이지체크」처럼
# 색과 무늬를 붙여 적는다.
COLOR_PREFIX = ("그린|블랙|화이트|블루|레드|네이비|그레이|베이지|브라운|카키|아이보리|핑크|"
                "퍼플|옐로우|오렌지|민트|차콜|크림|와인|버건디|실버|골드|라이트|다크|딥")


# 앞말에 붙여 쓰는 옷 부위 낱말. 색 예외와 같은 자리인데, 이쪽은 낱말마다 따로 적는다 —
# 두 글자 한글은 남의 낱말 꼬리에 너무 쉽게 걸린다.
#
# 왜 필요한가: 매장은 「아웃포켓」·「허리밴드」·「자개단추」·「소가죽」처럼 붙여 쓴다. 앞이
# 한글이면 막는 방벽 때문에 이런 진짜 디테일이 통째로 안 읽혔다 — 이 꼴로만 적힌 상품이
# 8,092벌이다(2026-09-07 실측).
#
# 왜 방벽을 그냥 못 걷어내는가: 같은 셈에서 걸린 것 가운데 대부분이 남의 낱말이었다.
#   슬리브·롱슬리브·숏슬리브 → 「리브」 1,820  ·  「~하시어」 → 「시어」 312
#   케어라벨 → 「라벨」 2,739  ·  브레이슬릿 → 「슬릿」 27  ·  꽈배기 → 「배기」 25
#   버튼다운·아름다운 → 「다운」 60  ·  올리브 → 「리브」 72  ·  아트워크 → 「워크」 248
# 그래서 「무엇 뒤에 오면 뜻이 그대로인가」를 실제로 나온 앞말에서만 골라 적는다.
# 틀린 태그는 없는 태그보다 나쁘다 — 앱 상세 표에 사람이 그대로 본다.
GLUE_PREFIX = {
    "포켓": "아웃|인|빅|사이드|히든|웰트|카고|앞|뒤|가슴|배색|플랩|지퍼|패치|슬랜트",
    "밴드": "허리|고무|밑단|소매|목",
    "단추": "자개|소뿔|조개|앞|뒤|금속|나무",
    "여밈": "앞|뒤|옆|지퍼|단추",
    "트임": "옆|뒤|앞|밑|목|소매",
    "자수": "로고|직|가슴|앞|뒤|기계|손",
    "가죽": "소|양|합성|인조|에코|재생|송아지",
    "카라": "오픈|셔츠|피터팬|윙|스탠드|테일러드",
    "체인": "키|메탈|볼",
    "조절": "길이|허리|끈|어깨",
    "크롭": "세미",
    # 「슬리브」·「올리브」의 리브를 막으면서 「소매리브」는 받는다 — 앞말이 두 글자 이상이라
    # 갈라진다. 「워싱」은 「덤블워싱」·「텀블워싱」(세탁 안내 159벌)을 일부러 뺀다.
    "리브": "소매|밑단|목|넥|허리",
    # 「버튼다운」은 카라 종류지만 그 셔츠에도 단추는 있다 — 막지 않는다(120벌 확인, 2026-09-08).
    "버튼": "스냅|택|더블|싱글|자개|메탈|나무|앞|뒤|프론트",
    "워싱": "가먼트|스톤|빈티지|인디고|피그먼트|워터|아이스|블리치|오버다이",
}


# 뒤에 이 글자가 붙으면 뜻이 뒤집히는 낱말. 「코튼 롱 슬리브리스 티셔츠」는 민소매인데
# 「롱 슬리브」가 앞부분에 그대로 들어 있어서 롱슬리브·슬리브리스 둘 다 붙었다(3벌,
# 2026-09-08). 영어 쪽은 이미 `(?![a-z0-9])` 로 막혀 있어 'long sleeveless' 가 안 걸린다 —
# 세 글자 넘는 한글 별칭에만 경계가 없었다. 한글은 조사가 붙어 다녀서(「롱슬리브를」)
# 뒤를 통째로 막을 수는 없고, 뜻이 뒤집히는 글자만 짚어 막는다.
BLOCK_SUFFIX = {
    "롱슬리브": "리스",
    "롱 슬리브": "리스",
}


def compile_alias(alias: str) -> re.Pattern:
    a = re.escape(alias.lower())
    if alias in BLOCK_SUFFIX:
        return re.compile(rf"{a}(?!{BLOCK_SUFFIX[alias]})")
    if _is_hangul(alias) and len(alias) == 1:
        return re.compile(rf"(?<![{HANGUL}]){a}(?![{HANGUL}])")
    if _is_hangul(alias) and len(alias) == 2:
        # 앞이 한글이면 막는다 — 「측면」의 면, 「서울」의 울, 「올리브」의 리브를 걸러 온 규칙이다.
        # 다만 색 이름이 앞에 붙는 것은 막으면 안 된다: 「그린카모」·「베이지체크」·「블랙진」은
        # 색 + 그 낱말이라 뜻이 그대로다. 이름에 그렇게 적는 매장이 있어 22벌이 무늬를 잃고
        # 있었다(그린카모 9 · 베이지체크 3 …, 2026-09-06). 색 낱말 뒤는 허용한다.
        pre = COLOR_PREFIX
        if alias in GLUE_PREFIX:                 # 옷 부위 낱말 뒤도 허용한다(위 GLUE_PREFIX 주석)
            pre = f"{pre}|{GLUE_PREFIX[alias]}"
        return re.compile(rf"(?<![{HANGUL}]){a}|(?:{pre}){a}")
    if re.fullmatch(r"[a-z0-9 /\-]+", alias.lower()):
        # 복수형 s 를 받는다 — 「Archive Long Sleeves」가 「long sleeve」에 안 걸려
        # 상품명에 sleeve 723 · long 616 이 미등록으로 남아 있었다(2026-09-05).
        return re.compile(rf"(?<![a-z0-9]){a}s?(?![a-z0-9])")
    return re.compile(a)




# 상세 설명 뒤(또는 사이)에 매장이 「함께 보는 상품」을 이름+가격으로 붙인다. 그 이름이
# 이 옷의 태그가 됐다 — badblood MA-1 자켓이 옆의 「솔리드 복서 - 블랙/스트라이프」 때문에
# 단색이면서 스트라이프가 되고, dunst 블레이저가 옆의 「H-LINE MAXI SKIRT」 때문에 맥시가
# 됐다. 상품 12,493벌(33%)이 이런 글을 달고 있다(2026-09-05).
#
# 「가격이 처음 나오는 데서 자른다」는 못 쓴다 — 자기 가격 블록이 글 한가운데(중앙값 39%
# 지점)에 있어 진짜 설명 25,740건이 함께 잘린다. 그래서 자르지 않고 **가격과 그 앞의
# 이름만 도려낸다**. 앞으로 예순 자까지 되짚되 문장 경계(마침표·「다 」·「요 」·줄바꿈·
# 가운뎃점)에서 멈추므로 진짜 문장은 남는다(네 매장 표본에서 한 글자도 안 잘렸다).
_PRICE_RUN = re.compile(
    r"(?:(?:krw|won)\s*\d{1,3},\d{3}|\d{1,3},\d{3}\s*(?:krw|won|원))"
    r"(?:\s*(?:\(\s*\d+%\s*\))?\s*(?:krw|won)?\s*\d{1,3},\d{3}\s*(?:krw|won|원)?)*", re.I)
_SENT_END = re.compile(r"[.。!?\n|·•▪]|다\s|요\s")

# 통화 표시 없이 값만 적는 매장이 있다 — depound 「slim fit t-shirt - navy 58,000」,
# mardi-mercredi·matin-kim·the-coldest-moment 의 「함께 보는 상품」 블록이 그렇다.
# 낱개로 보면 값인지 아닌지 알 수 없어서(「1,000회 세탁」) 셋 이상 잇달아 있을 때만 값으로 본다.
_PRICE_BARE = re.compile(r"(?<![\d,.])\d{1,3},\d{3}(?![\d,.])")
_CUR_NEAR = re.compile(r"(?:krw|won|원)", re.I)


# 손님 후기와 문의 글. 매장이 상품 설명 칸에 그것을 함께 담아 온다 — siyazu 는 설명 666자가
# 통째로 후기+Q&A+가격줄이라 상품 설명이 아예 없었다(783벌, 2026-09-06). 후기는 사이즈·핏·색을
# 말하므로 그대로 두면 손님의 감상이 옷의 태그가 된다(「커서 스몰사이즈로 교환했는데도 크네요」).
#
#   Review 14 만족 [1] 네이버 페이 구매자 | 23.12.24 13 좀 긴듯한데 … [1] 네이버 페이 구매자 | 21.02.01
#   Q&A write all 10 문의합니다. 장**** | 22.10.26 9 고객님 답변 드립니다. 시야쥬 | 22.10.26
#
# 한 줄은 <말> [번호] <이름> | <날짜> 꼴이라 그 앞의 말까지 함께 지운다.
REVIEW_LINE = re.compile(
    r"[^.!?\n]{0,80}\[\d+\]\s*(?:[가-힣]{2,4}|네이버\s*페이\s*구매자|[A-Za-z*]{2,12})\s*\|\s*\d\d\.\d\d\.\d\d\s*\d*")
QNA_LINE = re.compile(
    r"[^.!?\n]{0,40}(?:문의합니다|고객님 답변 드립니다)[^|]{0,30}\|\s*\d\d\.\d\d\.\d\d\s*\d*")
REVIEW_HEAD = re.compile(r"(?:^|\s)(?:Review|리뷰)\s+\d+\s|(?:^|\s)Q&A\s+write\s+all\s+\d+\s", re.I)


def strip_reviews(text: str) -> str:
    """후기·문의 글을 걷어낸다. 상품 설명이 아니라 손님의 말이다."""
    t = REVIEW_LINE.sub(" ", text or "")
    t = QNA_LINE.sub(" ", t)
    t = REVIEW_HEAD.sub(" ", t)
    return t


# 상세 이미지에 「PRODUCT GUIDE」 눈금자를 넣는 매장이 있다. OCR 이 그 눈금의 이름을
# 통째로 읽어 온다 — 이 옷의 핏이 아니라 자의 눈금 이름이다.
#
#   PRODUCT GUIDE
#   핏     타이트  슬림  레귤러  세미 와이드  와이드      ← 이 옷은 이 중 하나인데 다 적힌다
#   촉감   부드러움  보통  뻣뻣함
#
# 그래서 blr 「Waffle Mix Washed Sweat Pants」가 슬림핏이면서 오버핏이었다. 눈금자가 있는
# 상품이 1,602벌이다(matin-kim 536 · badblood 462 · nick-nicole 449 · blr 121, 2026-09-07).
# 한 줄에 핏 낱말이 셋 이상 잇달아 나오면 그건 옷 이야기가 아니라 자다. OCR 오독을 감안해
# 낱말 사이에 잡스러운 글자 몇 개는 봐준다(「레글러」·「벗뱃함」처럼 눈금 이름도 깨져 온다).
# 자의 눈금 이름은 두 갈래로 온다.
#  · 한글 눈금은 그림에서 읽어 오느라 낱말 사이에 OCR 쓰레기가 낀다
#    (「타이트 슬림 alee] 세미 와이드 와이드」 — 레귤러가 alee] 로 깨졌다).
#  · 영문 눈금은 글자로 깔끔하게 온다(frizmworks 「SLIM | CROP REGULAR TAPERED | ANKLE WIDE」).
# 그래서 한글 쪽만 낱말 사이 잡글자를 봐주고, 영문 쪽은 구분기호만 허락한다. 영문에 글자를
# 허락하면 진짜 설명글이 잘린다 — 「Relaxed fit with a wide leg and a slim taper」.
# 자에는 「오버핏」이 아니라 「오버」로만 적히기도 한다(blr 「타이트 슬림 레글러 세미 오버 오버」).
_FIT_KO = (r"(?:타이트|슬림\s*핏|슬림핏|슬림|레귤러|레글러|세미\s*와이드|세미|와이드|"
           r"오버\s*핏|오버핏|오버|루즈\s*핏|루즈핏|루즈)")
_FIT_EN = (r"(?:regular|slim|tapered|straight|relaxed|loose|oversized?|wide|skinny|baggy|"
           r"crop(?:ped)?|ankle|semi|tight)")
SCALE_BAR = re.compile(
    rf"{_FIT_KO}(?:[^\n가-힣]{{0,12}}{_FIT_KO}){{2,}}"
    rf"|{_FIT_EN}(?:[^\n가-힣A-Za-z0-9]{{1,8}}{_FIT_EN}){{2,}}"
    rf"|(?:착용감|촉감|두께감?|비침|신축성)\s*[^\n가-힣]{{0,10}}{_FIT_KO}"
    rf"(?:[^\n가-힣]{{0,8}}{_FIT_KO})+", re.I)

# 「슬림해 보이는」은 이 옷의 핏이 아니라 입은 사람이 어떻게 보이는지다. 오히려 넉넉한 옷에
# 자주 적힌다 — haiq 「타이트 하지 않고 넉넉한 폭으로 … 다리 라인이 슬림해 보이도록」,
# 「넉넉한 박시 핏이 허벅지 라인을 여유 있게 감싸 자연스럽게 슬림해 보이는 효과」.
# 그 낱말만 지운다(141벌, 2026-09-07).
SLIM_LOOK = re.compile(r"슬림(?=(?:해|하게)\s*보이)")
# 실루엣 낱말이 이 옷의 핏을 말하지 않는 자리들. 감사기의 실루엣 모순 62건을 문장까지
# 열어 모았다(2026-09-07).
#   「4. 폴리 백 제작 시 서로 달라붙는 것을 방지하기 위하여 미량의 슬림제가 들어 있습니다」
#       → 포장 안내문의 화학물질이 슬림핏이 됐다. 한 브랜드에 38벌, 그 브랜드 모순의 대부분이다.
#   「세로로 잔잔하게 흐르는 골지 조직은 시각적으로 슬림한 효과를 주며」
#       → 눈에 그렇게 보인다는 말이지 이 옷의 핏이 아니다(이미 있는 「슬림해 보이」와 같은 갈래).
#   「슬림한 티셔츠나 슬리브리스와는 산뜻하고 미니멀한 분위기」
#       → 같이 입을 남의 옷이다.
FIT_NOISE = re.compile(r"슬림(?=제)"
                       r"|슬림(?=한?\s*효과)"
                       r"|(?:슬림|루즈|오버|타이트)(?=한\s*[가-힣]{2,6}(?:나|이나)\s)")
# 「어느 사이즈를 고르면 어떤 핏이 되는가」는 이 옷의 핏이 아니라 고르는 안내다.
#   「* 유니섹스 ( s size – 슬림핏 / m size – 슬림핏 남녀공용 / l, xl size – 루즈핏 )」  67벌
#   「여성의 경우 슬림한 핏으로는 xs-s, 루즈핏으로는 m 사이즈를 추천드리며」              10벌
# 한 벌에 슬림핏과 루즈핏이 함께 붙는 가장 흔한 까닭이다. 어느 한쪽을 고를 수 없으니
# 둘 다 지운다 — 틀린 태그보다 없는 태그가 낫다.
FIT_SIZE_GUIDE = re.compile(
    r"(?:xxs|xs|s|m|l|xl|xxl|2xl|3xl|\d)\s*size\s*[-–—]\s*(?:슬림|루즈|오버|레귤러|타이트)[가-힣]*"
    r"|(?:슬림|루즈|오버|타이트)한?\s*핏?으로는", re.I)

# 자는 늘 양 끝을 다 적는다 — 좁은 쪽과 넓은 쪽이 한 줄에 같이 있어야 자다.
# 이 조건이 없으면 진짜 설명글이 잘린다: 「a slim, tapered, straight silhouette」은
# 핏 낱말이 셋 잇달아 나오지만 한 옷을 말하는 것이고, 좁은 쪽만 있다(2026-09-07).
_NARROW = re.compile(r"타이트|슬림|tight|slim|skinny", re.I)
_WIDE = re.compile(r"와이드|오버|루즈|wide|oversized?|loose|baggy|relaxed", re.I)


def strip_scale_bar(text: str) -> str:
    """상세 이미지의 핏 눈금자와 「슬림해 보이는」을 걷어낸다. 둘 다 이 옷의 핏이 아니다."""
    def cut(m):
        g = m.group(0)
        return " " if (_NARROW.search(g) and _WIDE.search(g)) else g
    t = SLIM_LOOK.sub(" ", SCALE_BAR.sub(cut, text or ""))
    t = FIT_SIZE_GUIDE.sub(lambda m: " " * len(m.group(0)), t)
    return FIT_NOISE.sub(" ", t)


# 매장이 「이 옷에 무엇을 같이 입으면 좋다」를 적는다. 거기 적힌 옷은 이 상품이 아니다.
#   haiq  「쌀쌀한 저녁에는 슬리브리스나 티셔츠와 레이어드해 가디건처럼 연출할 수 있습니다」
#   haiq  「간절기에는 나시나 얇은 긴팔들과 레이어링하고」
#   grove 「비침이 있어 슬리브리스, 티셔츠 등 다양한 아이템과 매칭 시 매력적인 아이템입니다」
#   kirsh 「나시티, 긴팔 등으로 여러 스타일링 가능」
# 그래서 반팔 니트가 슬리브리스이고 후드 아노락이 민소매였다.
# 문장을 통째로 버리지 않는다 — 그 안에 이 옷 이야기(홑겹·비침·기장)도 함께 있다.
# 「무엇이나」·「무엇, 무엇 등」으로 남을 부르는 자리의 그 낱말만 지운다(2026-09-07).
_WEAR_WORD = r"(?:슬리브리스|sleeveless|민소매|나시티|나시|반팔|긴팔|롱슬리브)"
STYLE_OR = re.compile(rf"{_WEAR_WORD}(?=(?:나|이나)\s)", re.I)
STYLE_LIST = re.compile(rf"{_WEAR_WORD}(?=\s*[,·]\s*[^.\n]{{0,24}}?등[\s으로])")
# 매장이 「짝이 되는 다른 상품과 세트로 입으세요」를 적는다. 그 짝은 이 옷이 아니다(2026-09-07).
#   grove 「julian sleeveless와 세트로 착용할 수 있습니다」   → 반팔 티셔츠가 슬리브리스가 됐다
#   grove 「leaf sleeveless 세트 착용 가능」                 → 긴팔 가디건이 슬리브리스가 됐다
#   grove 「rina sleeveless 와 세트 착용 가능」
STYLE_SET = re.compile(rf"{_WEAR_WORD}(?=\s*(?:와|과)?\s*세트)", re.I)
# 「무엇과 레이어드해」도 같다 — 겹쳐 입을 남의 옷 이름이다.
#   haiq 「간절기에는 긴팔 이너와 레이어드해 포인트를 더할 수도 있습니다」 → 반팔 티가 롱슬리브가 됐다
STYLE_LAYER = re.compile(rf"{_WEAR_WORD}(?=\s*(?:이너|아우터|셔츠|니트|가디건|티셔츠)?\s*(?:와|과)\s*레이어[드딩])", re.I)


def strip_styling_suggestion(text: str) -> str:
    """「~와 같이 입으세요」에서 남의 옷 이름을 지운다."""
    t = text or ""
    for rx in (STYLE_OR, STYLE_LIST, STYLE_SET, STYLE_LAYER):
        t = rx.sub(" ", t)
    return t


# 매장은 「무엇이 아니다」도 적는다. 그 낱말은 이 옷에 있는 것이 아니라 없는 것이다.
#   frizmworks 「레귤러핏 기반으로 제작되었으며, 루즈하지 않고 깔끔하게 떨어지는 기장감으로」
#   → 루즈핏 이 붙었다. 매장은 루즈하지 *않다*고 적었는데 루즈핏 이 된 것이다.
#   흔한 꼴: 「안감 없는 홑겹」·「신축성이 없는 원단」·「절개 없이 통으로」·「비치지 않는 두께」
# 부정문 안에 태그 낱말이 있는 상품이 1,616벌이다(2026-09-07 셈).
# 문장을 통째로 버리지 않는다 — 부정 앞의 그 낱말만 지운다. 뒤에 이 옷 이야기가 이어진다.
NEGATED = re.compile(r"([가-힣]{2,7})(?:하지|지|하게)\s*않"
                     r"|([가-힣]{2,7})\s*없(?:이|는|어|고|음|다)")


def strip_negated(text: str) -> str:
    """「~하지 않」·「~ 없」 바로 앞의 낱말을 지운다. 그건 이 옷에 없는 것이다."""
    def cut(m):
        g = m.group(1) or m.group(2)
        return m.group(0).replace(g, " " * len(g), 1)
    return NEGATED.sub(cut, text or "")


def strip_other_products(text: str, back: int = 60) -> str:
    """값이 나오는 자리 앞 60자를 문장 끝까지 되짚어 도려낸다 — 거기 남의 상품 이름이 있다.

    이걸로 원문 글자의 6%가 잘린다. 남는 새는 것은 167벌뿐이다(감사기 「설명에 남의 상품이
    섞였다」, 2026-09-06). 남는 것은 이런 꼴이다:
        the-coldest-moment "Made In China Styled With TCM dot flower T (blue) ₩45,000"
        lmood              "연관 상품 브룩 하이넥 니트 집업 ELECTRIC BLUE ₩128,000"

    재 보고 되돌린 잣대: 「Styled With·연관 상품」 같은 표시를 만나면 그 뒤를 통째로 버리기.
    새는 것은 167 → 0 이 되지만 태그 7,226개가 같이 사라진다. 그 표시가 글 한가운데
    있기 때문이다 — 1,931벌에서 표시 위치 중앙값이 글의 27% 지점이고, 1,097벌은 30% 앞에
    있다. 매장은 추천 상품 블록을 가운데 끼워 넣고 그 뒤에 다시 제 상품의 소재·관리법을
    적는다. 167을 고치려고 7,226을 버릴 수는 없다.
    """
    if not text:
        return text
    spans = [(m.start(), m.end()) for m in _PRICE_RUN.finditer(text)]
    # 통화 표시 없는 값은 셋 이상 모여 있을 때만 값으로 본다(위 _PRICE_BARE 주석)
    bare = [(m.start(), m.end()) for m in _PRICE_BARE.finditer(text)
            if not _CUR_NEAR.search(text[m.end():m.end() + 6])
            and not _CUR_NEAR.search(text[max(0, m.start() - 6):m.start()])]
    if len(bare) >= 3:
        spans = sorted(set(spans + bare))
    if not spans:
        return text
    out, last = [], 0
    for start, end in spans:
        if start < last:
            last = max(last, end)
            continue
        win_from = max(last, start - back)
        win = text[win_from:start]
        k = 0
        for mm in _SENT_END.finditer(win):
            k = mm.end()
        cut = win_from + k
        if cut >= last:
            out.append(text[last:cut])
        last = end
    out.append(text[last:])
    return "".join(out)


class Tagger:
    def __init__(self, vocab: dict):
        self.vocab = vocab
        self.mine_only = set(vocab.get("_mine_alias_only", []))
        self.blocklist = [b.lower() for b in vocab.get("_color_blocklist", [])]
        self.text_blocklist = [b.lower() for b in vocab.get("_text_blocklist", [])]
        self.bottoms_only = [tuple(x.split(".", 1)) for x in vocab.get("_bottoms_only", [])]
        # 옷의 소재가 금속·보석일 수는 없다 — 아래 tag() 주석 참고
        self.not_garment_material = set(vocab.get("_not_garment_material", []))
        # 축 전체를 한 목록으로 — 길이 내림차순으로 매칭하고 매칭 구간을 마스킹한다
        text_rules, color_rules = [], []
        # 색은 수집기 어휘 하나만 쓴다. 예전에는 여기 25색을 따로 적어 두었는데, 그것은
        # 사실 「거친 이름」이라 차콜 2,037벌이 그레이로, 크림 753벌이 아이보리로 뭉개졌다
        # (색상 칸이 찬 36,964벌 중 6,101벌, 2026-09-05). 이제 값은 수집기의 63색을 쓰고,
        # 예전 25색은 _color_rollup(세밀→거친)으로 남겨 앱이 두 층을 다 쓸 수 있게 한다.
        vocab = dict(vocab)
        try:
            import crawl_cafe24 as _cc
            vocab["color"] = {k: list(vs) for k, vs in _cc.COLOR_VOCAB.items()}
        except Exception:
            pass
        for ax in AXES:
            for value, aliases in vocab[ax].items():
                names = list(aliases) if value in self.mine_only else [value] + list(aliases)
                for a in names:
                    (color_rules if ax == "color" else text_rules).append((len(a), ax, value, compile_alias(a)))
        self.text_rules = sorted(text_rules, key=lambda r: -r[0])
        self.color_rules = sorted(color_rules, key=lambda r: -r[0])

    @staticmethod
    def _scan(rules, text: str) -> dict[str, set]:
        """긴 낱말이 짧은 낱말을 먹는다 — 단, 같은 축 안에서만.

        예전에는 축을 가리지 않고 먹었다. 그래서 「카고 팬츠」에 pants_type 어휘를 더하자
        construction 의 「카고포켓」 409벌이 사라졌다(2026-09-05). 한 낱말이 두 가지를
        동시에 말하는 일은 흔하다 — 카고는 바지 종류이면서 주머니고, 밴딩은 바지 종류이면서
        허리 만듦새다(사람 지적). 축마다 따로 가려야 둘 다 남는다.
        「가먼트 워싱」이 「워싱」을 먹는 것은 같은 축 안이라 그대로 지켜진다.
        """
        text = text.lower()
        hits: dict[str, set] = defaultdict(set)
        by_ax: dict[str, list] = defaultdict(list)
        for r in rules:
            by_ax[r[1]].append(r)
        for ax, rs in by_ax.items():
            masked = list(text)
            cur = text
            for _, _, value, rx in rs:
                found = False
                for m in rx.finditer(cur):
                    found = True
                    for i in range(m.start(), m.end()):
                        masked[i] = "\x00"
                if found:
                    hits[ax].add(value)
                    cur = "".join(masked)
        return hits

    def tag(self, category: str, name: str, body: str, color_text: str, quality: str,
            sleeve_cm: float | None = None) -> dict[str, list]:
        text = f"{name}\n{strip_negated(strip_other_products(strip_scale_bar(strip_styling_suggestion(strip_reviews(body)))))}".lower()
        for b in self.text_blocklist:  # '시어링'→시어, '레이어드 스타일링'→레이어드 같은 오탐을 먼저 지운다
            text = text.replace(b, " " * len(b))
        text = LEATHER_TRIM.sub(lambda m: m.group(0).replace("가죽", "  "), text)
        text = OCR_SPLIT_DANMYEON.sub(lambda m: m.group(0).replace("면", " "), text)
        text = BUTTON_DOWN.sub(_mask_button_down, text)
        hits = self._scan(self.text_rules, text)

        # n부 — 소매면 칠부소매, 팬츠면 버뮤다(명시어 우선)
        for m in NBU_RX.finditer(body):
            tail = body[m.end():m.end() + 6]
            if m.group(2) == "소매" or tail.startswith("소매"):
                hits["sleeve_length"].add("칠부소매")
            elif category in PANTS:
                window = body[max(0, m.start() - 40):m.end() + 40]
                if "카프리" in window or "capri" in window.lower():
                    hits["length"].add("카프리")
                else:
                    hits["length"].add("버뮤다")
        if LOOSE_RX.search(body):
            hits["silhouette"].add("루즈핏")
            if category in BOTTOMS:
                hits["silhouette"].add("와이드")
        # 「혼방」이라는 낱말만 보고 폴리에스터를 넣던 규칙을 뺀다(2026-09-05).
        # 「부드러운 리오셀 혼방 소재」(badblood)에 폴리에스터가 붙었다 — 무엇과 섞였는지
        # 페이지는 말하지 않는데 특정 섬유를 단정한 것이다. 324벌이 그 꼴이었다.
        # 소재는 사람이 옷을 고르는 근거라, 빠진 것보다 틀린 것이 나쁘다.

        # color — 본문이 아니라 이름·대표색·스펙 색상에서만
        ct = color_text.lower()
        for b in self.blocklist:
            ct = ct.replace(b, " " * len(b))
        for ax, vals in self._scan(self.color_rules, ct).items():
            hits[ax] |= vals

        # 축 게이트
        # 치마도 기장을 가진다 — 오히려 가장 중요한 속성이다. 예전엔 축을 통째로 버려서
        # 「string maxi skirt」·「WOOL MIDI PLEATED SKIRT」·「페이크 스웨이드 미니 스커트」가
        # 기장 없이 남았다(1,056벌, 2026-09-05). 상의·바지 쪽 값만 걸러 낸다.
        if category == "Skirts":
            hits["length"] = {v for v in hits.get("length", set()) if v in SKIRT_LENGTH}
            if not hits["length"]:
                hits.pop("length", None)
        if category not in PANTS:
            hits.pop("pants_type", None)
        if category in NO_GARMENT_AXES:
            for ax in GARMENT_AXES:
                hits.pop(ax, None)
        # 넥라인·소매는 상의류에만 선다. 바지·스커트·데님에 붙은 것은 코디 문장이나 이름의
        # 다른 말에서 왔다 — 「더블니 스웨트 팬츠」에 후드(9999archive 의 협업 이름 「후디진호」),
        # 「belted tuck pants」에 슬리브리스, 「BRUSHED STRAIGHT JEANS」에 카라(2026-09-06).
        # 원피스·셋업은 상의 쪽에 둔다 — 목선과 소매가 있다.
        if METAL_PART.search(name) and not LEATHER_NAME.search(name):
            hits.get("material", set()).difference_update({"가죽", "천연가죽"})
        if category in NECKLESS:
            hits.pop("neckline", None)
            hits.pop("sleeve_length", None)
        if category not in BOTTOMS | {""}:
            for ax, val in self.bottoms_only:  # 하의 전용 값이 코디 문장으로 상의에 붙는 것 방지
                hits.get(ax, set()).discard(val)
        # 옷(상의·하의·아우터·니트·셔츠·스커트·데님·드레스)의 소재가 메탈·진주·황동·유리일 수는 없다.
        # 「ykk metal buttons」「mother of pearl buttons」가 material/메탈·진주가 됐다 — 부자재 설명이
        # 소재 축에 앉은 것이다. 판매중·품절 옷에서 메탈 927벌(927벌 다 부자재 태그도 함께 있었다) ·
        # 진주 104 · 황동 73 · 유리 5(2026-09-08 무작위 감사에서 40벌 중 3벌이 이 꼴). 반지·키링 같은
        # 잡화와 품목 빈칸은 건드리지 않는다 — 명시한 옷 품목에서만 뺀다.
        if category in GARMENT_CATEGORIES and self.not_garment_material:
            for val in self.not_garment_material:
                hits.get("material", set()).discard(val)
        # 폴로카라·스프레드카라·오픈카라·스탠드카라·차이나카라·숄카라는 다 카라다. 세부 카라만 있고
        # 「카라」가 없는 옷이 578벌(2026-09-08: 스프레드 207 · 오픈 163 · 폴로 133 · 스탠드 73 · 차이나 11
        # · 숄 8) — 「카라」로 걸러 보면 이들이 빠진다. 세부 값이 맞으면 카라는 확실하니 함께 붙인다.
        nk = hits.get("neckline")
        if nk and any(v != "카라" and v.endswith("카라") for v in nk):
            nk.add("카라")
        # 데님 칸에 든 바지는 청바지다 — 이름에 「데님」이라고 안 적은 223벌이 있다.
        if category == "Denim":
            hits["pants_type"].add("진")
        if category in SKIRTY and MINI_NAME.search(name):
            hits["length"].add("미니")
        if category not in NON_GARMENT:
            mats = hits.get("material") or set()
            if mats & WARM_MATERIALS:
                hits["function"].add("보온성")
            if "메쉬" in mats:
                hits["function"].add("통기성")
            mp = SPAN_PCT.search(body)
            if mp and float(mp.group(1)) >= 3:
                hits["function"].add("신축성")
        # 단색은 글에서 찾지 않고 없음으로 안다. 글에 적힌 「단색」·「솔리드」·「무지」는
        # 이 옷의 무늬가 아니라 견주는 말이거나 라인 전체를 말하는 문장이었다:
        #   espionage 「멀리서 보면 단색 같지만 가까이서 볼 때 입체감 있는」 → 실은 그리드 체크
        #   dunst    「솔리드 컬러 및 스트라이프 컬러로 제작되어」        → 이 벌은 스트라이프
        # 그래서 33벌이 단색이면서 스트라이프·체크였다(2026-09-07).
        hits.get("pattern", set()).discard("단색")
        if quality == "ok" and not hits.get("pattern"):
            hits["pattern"].add("단색")
        # 소매 길이는 매장이 글로 안 적는다 — 소매가 빈 상의 12,248벌 중 12,170벌(99.4%)이
        # 원문 어디에도 없다(2026-09-05). 대신 우리에겐 실측 소매길이가 있다.
        # 롱슬리브 중앙 61cm · 반팔 중앙 21.6cm 로 뚜렷이 갈린다. 가운데(27~54cm)는 비워 두고
        # 양 끝만 쓴다 — 글에 아무 말도 없을 때만. 글로 아는 2,709벌에 대 보니 반팔↔롱슬리브
        # 혼동은 46건(98.1%)이고, 나머지 틀림은 슬리브리스·퍼프소매인데 그것들은 글에 적혀 있어
        # 애초에 여기까지 오지 않는다.
        if category in SLEEVED:
            if NAME_LS.search(name):
                hits["sleeve_length"] -= {"반팔", "슬리브리스", "칠부소매"}
                hits["sleeve_length"].add("롱슬리브")
            elif NAME_SS.search(name):
                hits["sleeve_length"] -= {"롱슬리브", "슬리브리스", "칠부소매"}
                hits["sleeve_length"].add("반팔")
        if sleeve_cm is not None and not hits.get("sleeve_length") and category in SLEEVED:
            if sleeve_cm <= 26:
                hits["sleeve_length"].add("반팔")
            elif sleeve_cm >= 55:
                hits["sleeve_length"].add("롱슬리브")
        # 실측도 없고 글에도 없을 때만 품목 이름으로 짐작한다 — 반드시 실측 갈래 다음이다.
        # 처음에 이 블록을 실측보다 앞에 두었더니 「아웃포켓 하프 블루종」(소매 실측 25.5cm)이
        # 롱슬리브가 됐다. 실측이 이름보다 낫다(2026-09-06, 46벌).
        if (not hits.get("sleeve_length") and category in SLEEVED
                and LONG_SLEEVE_KIND.search(name) and not NOT_LONG_SLEEVE.search(name)):
            hits["sleeve_length"].add("롱슬리브")
        self._name_wins(hits, name)
        return {ax: sorted(hits[ax]) for ax in AXES if hits.get(ax)}

    def _name_wins(self, hits: dict, name: str) -> None:
        """한 축에 서로 반대인 값이 둘 다 붙었는데 상품 이름이 한쪽만 말하면, 이름을 따른다.

        본문에는 이 옷 얘기가 아닌 것이 섞인다. 가장 흔한 것이 사이즈 안내다 —
        badblood 「PEACE NOT WAR 루즈핏 티」의 본문에 「S size - 슬림한 핏 / L size -
        레귤러」가 있어서 루즈핏과 슬림핏이 둘 다 붙었다. 옷은 하나고 이름이 루즈핏이라 한다.

        실측으로 넣는 소매는 이 규칙에 안 걸린다 — 그 갈래는 축이 비었을 때만 도는지라
        모순이 생기지 않는다(위 두 블록의 `not hits.get("sleeve_length")`).

        343건 가운데 92건이 이렇게 풀린다(2026-09-06 실측). 이름이 둘 다 말하거나
        아무 말도 안 하면 손대지 않는다 — 지어내지 않는다.
        """
        low = (name or "").lower()
        for ax, a, b in CONTRADICT:
            got = hits.get(ax) or set()
            if a not in got or b not in got:
                continue
            ina = any(x in low for x in self.alias_of(ax, a))
            inb = any(x in low for x in self.alias_of(ax, b))
            if ina and not inb:
                got.discard(b)
            elif inb and not ina:
                got.discard(a)

    def alias_of(self, axis: str, value: str) -> list[str]:
        """어휘에 적힌 별칭 + 값 자신, 전부 소문자."""
        al = (self.vocab.get(axis) or {}).get(value) or []
        return [x.lower() for x in [value, *al]]


_SLEEVE: dict[str, float] | None = None


def sleeve_of(url: str) -> float | None:
    """실측 소매길이(가장 작은 사이즈 것). data/product_sizes.json 에서 읽는다 —
    워크플로에서 size_from_ocr 이 tag_items 보다 먼저 돌아 늘 최신이다."""
    global _SLEEVE
    if _SLEEVE is None:
        _SLEEVE = {}
        p = DATA / "product_sizes.json"
        if p.exists():
            try:
                for u, e in json.loads(p.read_text(encoding="utf-8")).items():
                    v = [x for x in ((e or {}).get("sizes") or {}).get("소매길이", []) if x is not None]
                    if v:
                        _SLEEVE[u] = min(v)
            except json.JSONDecodeError:
                pass
    return _SLEEVE.get(url)


_HAN_RUN = re.compile(r"[가-힣]{3,}")
_HAS_PCT = re.compile(r"\d\s*%")
_HAN_ANY = re.compile(r"[가-힣]+")
_TOK_NUM = re.compile(r"^[0-9]+(?:[.,][0-9]+)?[.,)]?$")
_TOK_ALPHA = re.compile(r"^[A-Za-z]{3,}$")
_TOK_HAN = re.compile(r"^[가-힣]{2,}$")


def _gibberish(line: str) -> bool:
    """사진 위를 판독기가 훑어 만든 쓰레기 줄인가."""
    if _HAN_RUN.search(line) or _HAS_PCT.search(line):
        return False                      # 세 글자 이상 이어진 한글이나 「면 100%」는 진짜 글
    junk = 0
    for t in line.split():
        t = t.strip("()[]{}「」『』\"'")
        if not t or _TOK_HAN.match(t) or _TOK_ALPHA.match(t) or _TOK_NUM.match(t):
            continue
        junk += 1
        if junk >= 2:
            return True
    return False


def denoise_ocr(text: str) -> str:
    """판독기 글에서 쓰레기 줄의 한글만 지운다.

    상세 그림의 절반은 사진이라 판독기가 그 위에서 낱자를 주워 온다:
      「fak oo eg 9 : perc : 「 s : a 077 였0 별 = ¥ 나시 ag 7 + i 00: ~) ee oa the」
    여기 섞인 「나시」 때문에 카고 팬츠·파카·데님 캡이 슬리브리스가 됐다 —
    슬리브리스가 붙은 1,340벌 가운데 137벌(10.2%)이 이 한 낱말만 근거였다(2026-09-06).
    한글 별칭은 한두 글자짜리가 168개라 이런 줄에 늘 걸린다.

    줄 단위로 본다. 세 글자 이상 이어진 한글이 있거나 「… 100%」가 있으면 진짜 글로 보고
    그대로 둔다. 아니면서 알아볼 수 없는 토막이 둘 이상이면 그 줄의 한글만 지운다 —
    영문과 숫자는 남긴다(사이즈 표의 「S 48 53」이 여기 걸리지만 치수는 다른 script 가
    원문에서 읽으므로 영향이 없다). 「지퍼 포켓」·「1. 지퍼 여밈」처럼 깨끗한 짧은 줄은
    토막이 없어 살아남는다.
    """
    if not text:
        return text
    # 스펙 그림의 「눈금」: 「신축성 600 0 없음 보통 있음 비침 …」처럼 라벨 뒤에 선택지 셋이 다 적혀 있고
    # 표시(●)는 판독기가 못 읽는다. 어느 칸에 표시됐는지 모르면서 라벨 「신축성」만 보고 기능 태그를
    # 붙였다 — 한 매장의 100벌, 그중 94벌은 그 블록 말고 신축성 근거가 없었다(2026-09-08 감사에서
    # 기능 태그 40개 중 3개). 라벨과 선택지를 통째로 지운다. 「신축성 있음」·「신축성 약간 있음」처럼
    # 값이 하나만 적힌 줄은 그대로 둔다(서로 다른 선택지가 둘 이상일 때만).
    text = _SPEC_SCALE.sub(_blank_scale, text)
    out = []
    for l in text.split("\n"):
        if _ENGLISH_FIT_BAR.match(l) or _FIT_VS_FIT.match(l):
            continue                      # 「slim | crop regular tapered | ankle wide」 — 영문 핏 눈금자 줄
                                          # 「flare fit vs wide fit」 — 핏 비교 설명 그림의 캡션(한 매장 13벌, 두 핏이 다 붙었다)
        if _gibberish(l):
            l = _HAN_ANY.sub(" ", l)
        if _latin_garbage(l):
            continue
        out.append(l)
    return "\n".join(out)


# ── 영문 잡음 줄 ─────────────────────────────────────────────────────────────
# 위 규칙은 쓰레기 줄의 **한글**만 지웠다. 영문은 남겼는데, 판독기 잡음이 네 글자 이하 영문 낱말을
# 우연히 만들어 태그가 된다: 「ee ken oe se aa dart aoe perera eet」의 dart 가 다트(절개)로,
# 「nanda nur vaan aged 4 iv」의 aged 가 에이징으로. 판매중 옷에서 dart 27/27 · aged 57/57 이
# 전부 이 꼴이고, span·rib·lace·fur·belt·slit·mesh·midi·polo 까지 천 개 넘는 태그가 잡음이다
# (2026-09-08 근거 스캔).
#
# 그런데 「pure new wool」·「*ykk 2way zipper & parts」·「cotton 95% span 5%」는 라벨 사진의 진짜
# 글이라 영문을 통째로 버리면 안 된다. 그래서 **사전**으로 가른다 — 매장 HTML 설명글에 다섯 벌
# 넘게 나온 세 글자 이상 영문 낱말이 「아는 낱말」이다(3,784개). 판독 줄의 영문 토막 가운데 아는
# 낱말이 둘 이상이고 절반 이상이면 진짜 글, 아니면 잡음으로 보고 줄을 버린다. 두 글자 토막(ee·oe·aa)은
# 잡음의 재료라서 모르는 것으로 센다. 한글이 두 글자 이상 이어진 줄과 「95%」가 있는 줄은 재지 않고
# 둔다 — 「앞 중심의 무게감이 있는 ykk 사의 2way 지퍼」가 영문만 재면 잡음으로 보였다.
#
# 표본 재본 결과(0.5 기준 줄 삭제 시뮬레이션): dart 93% · aged 85% · belt 81% · slit 80% 지워짐,
# wool 2% · crop 1% · slim 3% · knit 2% 만 지워짐(그것도 「iia ae a wool &」 같은 줄).
# 라벨과 선택지 사이, 선택지와 선택지 사이에 다른 한글이 끼면 눈금표가 아니라 「신축성 있음 / 안감 없음」
# 같은 값 나열이다 — 한글이 아닌 것(OCR 찌꺼기 0·O·@·-·줄바꿈)만 건너뛴다.
_SPEC_SCALE = re.compile(r"(신축성|안감|기모)(?:[^가-힣]{0,40}?)((?:(?:없음|보통|있음)[^가-힣]{0,12}){2,})")
_SCALE_TOK = re.compile(r"없음|보통|있음")


def _blank_scale(m: re.Match) -> str:
    if len(set(_SCALE_TOK.findall(m.group(2)))) < 2:
        return m.group(0)
    return "".join(c if c == "\n" else " " for c in m.group(0))


_ENGLISH_FIT_BAR = re.compile(
    r"^[\s|./]*(?:(?:slim|regular|tapered|wide|crop|ankle|loose|over\s?sized?|relaxed|straight|flare|skinny|"
    r"standard|fit|semi|normal|long|short)[\s|./]*){3,}$", re.I)
_FIT_VS_FIT = re.compile(r"^\s*[a-z-]+\s+fit\s+vs\.?\s+[a-z-]+\s+fit\s*$", re.I)
_TOK2 = re.compile(r"[a-z]{2,}")
_TOK3 = re.compile(r"[a-z]{3,}")
_HAN_RUN2 = re.compile(r"[가-힣]{2,}")
_PCT = re.compile(r"\d\s*%")
_WHITE2 = {"of", "to", "in", "on", "at", "by", "or", "xs", "xl", "cm", "mm", "oz", "no", "up", "us", "uk", "eu", "kr"}
_LEX: set | None = None


def ensure_lexicon() -> set:
    """매장 HTML 설명글에서 영문 사전을 한 번 만든다(다섯 벌 넘게 나온 세 글자 이상 낱말)."""
    global _LEX
    if _LEX is not None:
        return _LEX
    df: Counter = Counter()
    for p in sorted(CRAWL.glob("*.jsonl")):
        if p.name.startswith("_"):
            continue
        for d in load_latest(p).values():
            words = set(_TOK3.findall(((d.get("description") or "") + " " + (d.get("detail_text") or "")
                                       + " " + (d.get("name") or "")).lower()))
            for w in words:
                df[w] += 1
    _LEX = {w for w, n in df.items() if n >= 5}
    return _LEX


def _latin_garbage(line: str) -> bool:
    if _HAN_RUN2.search(line) or _PCT.search(line):
        return False
    toks = _TOK2.findall(line.lower())
    if not toks:
        return False
    lex = ensure_lexicon()
    if len(lex) < 500:
        # 사전이 없거나 너무 작으면(크롤 파일이 안 보이는 자리에서 돌 때) 영문을 통째로 지우게 된다 —
        # 그럴 땐 이 규칙을 끈다. 잡음을 남기는 쪽이 진짜 글을 다 버리는 쪽보다 낫다.
        return False
    known = sum(1 for t in toks if (len(t) >= 3 and t in lex) or t in _WHITE2)
    return known < 2 or known / len(toks) < 0.5


def load_latest(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        latest[str(d["product_no"])] = d
    return latest


def spec_texts(spec) -> tuple[str, str]:
    """(태깅용 본문, 색상 텍스트)"""
    if not isinstance(spec, dict):
        return "", ""
    body, color = [], []
    for k, v in spec.items():
        if not isinstance(v, str) or len(v) > 400:
            continue
        if COLOR_KEY.search(k):
            color.append(v)
        if SPEC_KEY.search(k):
            body.append(f"{k}: {v}")
    return "\n".join(body), " ".join(color)


def quality_of(body: str) -> str:
    """CSS 가 섞였다고 글이 없는 것은 아니다.

    매장이 설명 안에 <style> 블록을 넣어 두면 진짜 글은 그 뒤에 온다 —
    「details.fa { border-top: 1px solid #e5e5e5; … }」 다음에
    「▪ Material: Cotton 100% ▪ Color: Washed Black ▪ Size (…)」가 이어진다.
    그런데 본문 어디든 CSS 가 보이면 css_fragment 로 낙인을 찍고 있었다. 621벌이 그렇게
    묶였는데 594벌은 CSS 를 걷어 내면 50자 넘는 글이 남고, 376벌은 400자가 넘는다.
    단색 추론이 「quality == ok」일 때만 도는 탓에 500벌이 패턴을 통째로 못 받았다
    (badblood 102 · siyazu 82 · far-from-what 72 · mardi-mercredi 64, 2026-09-06).

    CSS 를 걷어 내고 남은 글로 판정한다. 남은 것이 짧으면 그때 css_fragment 다."""
    clean = CSS_RX.sub(" ", body)
    letters = re.sub(rf"[^{HANGUL}A-Za-z]", "", clean)
    if len(letters) >= SHORT_TEXT:
        return "ok"
    return "css_fragment" if CSS_RX.search(body) else "too_short"


COLOR_TAIL = re.compile(
    r"[\s_\-]+(?:black|white|ivory|beige|navy|grey|gray|brown|charcoal|khaki|olive|cream|"
    r"blue|green|pink|red|burgundy|melange.*)$", re.I)


_SHELL_W = 8      # 낱말 몇 개 묶음으로 「같은 글」을 셀지
_JS_CSS = re.compile(r"\bvar\s+\w+\s*=|\{\s*margin\s*:|font-family\s*:", re.I)


def strip_shell(brw: dict[str, str], items: list[dict]) -> tuple[int, int]:
    """브라우저 글에서 매장 껍데기를 낱말 묶음 단위로 걷어낸다.

    drop_boilerplate 는 글 전체가 같을 때만 버린다. 그런데 브라우저 글은 상품 글 앞뒤로 메뉴
    (「긴팔티셔츠 반팔/슬리브리스 데님팬츠」)·장바구니 단추·꼬리말·「함께 보면 좋은 상품」 목록이
    붙어 와서 글마다 조금씩 다르다. 브랜드마다 브라우저 글의 25~93% 가 이런 껍데기였고(2026-09-08,
    20개 매장 실측), 한 매장의 후디에 반팔·슬리브리스·니트·데님·가죽이 메뉴에서 붙었다.

    같은 8낱말 묶음이 그 브랜드 상품 max(5, 10%) 벌 이상에 나오고, 그 상품들의 이름 줄기(색 뗀 것)가
    둘 이상이면 껍데기 후보다 — 색만 다른 같은 옷의 설명은 줄기가 하나라 남는다.

    **가장자리만 뺀다.** 껍데기는 글의 앞(메뉴)과 뒤(장바구니·꼬리말·「함께 보면 좋은 상품」·케어 안내)에
    붙고, 상품 글은 가운데 있다. 가운데에서 반복되는 글은 브랜드가 시리즈마다 같이 쓰는 진짜 사양이다 —
    처음 판이 전부를 뺐더니 한 가방 브랜드 80벌의 로고·버클·코튼, 한 신발 브랜드의 소가죽·안감, 한 매장의
    YKK 30벌이 사라졌다(after6 측정). 가장자리만 빼면 그 셋은 0 이 사라지고 메뉴 198 낱말은 그대로 빠진다.
    자바스크립트·CSS 를 담아 온 기록(「var sAuthSSLDomain = …」)은 통째로 버린다.
    돌려주는 것: (덮어서 뺀 낱말 수, 통째로 버린 기록 수).
    """
    dropped = 0
    for u in [u for u, t in brw.items() if _JS_CSS.search(t[:600])]:
        brw.pop(u, None)
        dropped += 1
    if len(brw) < 5:
        return 0, dropped
    stem = {r["source_url"]: COLOR_TAIL.sub("", (r["name"] or "").strip().lower()) for r in items}
    toks_of = {u: t.split() for u, t in brw.items()}
    seen: dict[str, set] = {}
    for u, toks in toks_of.items():
        for i in range(max(0, len(toks) - _SHELL_W + 1)):
            seen.setdefault(" ".join(toks[i:i + _SHELL_W]), set()).add(u)
    need = max(5, int(0.1 * len(brw)))
    shell = set()
    for w, us in seen.items():
        if len(us) < need:
            continue
        if len({stem.get(x, x) for x in us}) < 2:
            continue      # 색만 다른 같은 옷 — 설명이 같은 게 맞다
        shell.add(w)
    if not shell:
        return 0, dropped
    removed = 0
    for u, toks in toks_of.items():
        mark = [False] * len(toks)
        for i in range(max(0, len(toks) - _SHELL_W + 1)):
            if " ".join(toks[i:i + _SHELL_W]) in shell:
                for j in range(i, i + _SHELL_W):
                    mark[j] = True
        # 앞에서 이어진 표시 구간과 뒤에서 이어진 표시 구간만 뺀다
        a = 0
        while a < len(mark) and mark[a]:
            a += 1
        b = len(mark)
        while b > a and mark[b - 1]:
            b -= 1
        mark = [i < a or i >= b for i in range(len(toks))]
        if any(mark):
            removed += sum(mark)
            kept = " ".join(t for t, m in zip(toks, mark) if not m)
            if kept.strip():
                brw[u] = kept
            else:
                brw.pop(u, None)
    return removed, dropped


def drop_boilerplate(brw: dict[str, str], items: list[dict]) -> int:
    """브라우저가 설명글 대신 매장 껍데기를 담아 온 것을 버린다.

    브라우저 수집은 탭을 눌러 설명을 펴는데, 안 펴진 채로 화면에 있던 글을 담는 수가 있다.
    그러면 상품마다 똑같은 글이 붙는다.

      insilence 31벌  「뒤로 메뉴 닫기 남성복 카테고리 … 긴팔티셔츠 반팔/슬리브리스 …」  (메뉴)
      low-classic 14벌 「Details 주문 - 주문 후 품절 재고는 취소 처리될 수 있습니다 …」   (배송 안내)

    메뉴가 붙은 탓에 「울 개버딘 스트럭쳐 팬츠」에 반팔·슬리브리스·롱슬리브가 다 붙었다.
    insilence 는 남은 태그 모순 249건 가운데 45건을 혼자 이고 있었다(2026-09-06).

    다만 「같은 글이 여러 벌에 붙었다」만으로는 못 가른다 — 색만 다른 같은 옷은 설명이
    정말로 같다(213벌이 그렇다). 그래서 이름에서 색을 뗀 줄기나 품목이 여럿일 때만 버린다.
    """
    same: dict[str, list[str]] = {}
    for u, t in brw.items():
        if len(t) > 40:
            same.setdefault(t, []).append(u)
    info = {r["source_url"]: (COLOR_TAIL.sub("", (r["name"] or "").strip().lower()), r["category"])
            for r in items}
    n = 0
    for t, us in same.items():
        if len(us) < 3:
            continue
        # 목록에 남은 것만 본다 — 같은 글이 붙은 열넷 가운데 둘만 상품일 수 있다
        # (low-classic 의 배송 안내가 그렇다: 카디건 하나와 키링 하나).
        known = [info[u] for u in us if u in info]
        if len(known) < 2:
            continue
        if len({k[0] for k in known}) == 1 and len({k[1] for k in known}) == 1:
            continue          # 색만 다른 같은 옷 — 설명이 같은 게 맞다
        for u in us:
            brw.pop(u, None)
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    print(f"영문 사전 {len(ensure_lexicon()):,}개 낱말 ({time.time() - t0:.0f}s)")
    tagger = Tagger(json.loads(VOCAB.read_text(encoding="utf-8")))
    rows = list(csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig")))
    if args.brands:
        rows = [r for r in rows if r["brand_slug"] in set(args.brands)]
    by_brand: dict[str, list] = defaultdict(list)
    for r in rows:
        by_brand[r["brand_slug"]].append(r)

    out: dict[str, dict] = {}
    q_count, src_count = Counter(), Counter()
    ax_count, cat_count = Counter(), Counter()
    for slug, items in sorted(by_brand.items()):
        crawl = load_latest(CRAWL / f"{slug}.jsonl")
        ocr = load_latest(OCR / f"{slug}.jsonl")
        # 브라우저로 거둔 설명글 — 탭 안에 있어 requests 로는 빈 껍데기만 오는 매장이 있다.
        # perenn 은 설명글이 8자였는데 탭을 누르고 내려서 받으니 40벌이 치수·소재까지 들어
        # 있었다(2026-09-04). 주소로 맞춘다 — product_no 는 브라우저 기록에 없을 수 있다.
        brw: dict[str, str] = {}
        bp = BROWSER / f"{slug}.jsonl"
        if bp.exists():
            for l in bp.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    b = json.loads(l)
                    t = (b.get("description") or "").strip()
                    if b.get("source_url") and len(t) > len(brw.get(b["source_url"], "")):
                        brw[b["source_url"]] = t
        # strip_shell 을 먼저 — 껍데기 빈도는 메뉴만 담긴 기록까지 세어야 문턱(10%)을 넘는다. 그 기록들은
        # drop_boilerplate 가 버릴 것들인데, 먼저 버리면 메뉴가 섞인 나머지 기록이 문턱 아래로 떨어져
        # 후디의 메뉴가 그대로 남았다(after6b 에서 확인, 2026-09-08).
        strip_shell(brw, items)
        drop_boilerplate(brw, items)
        for r in items:
            d = crawl.get(str(r["product_no"]), {})
            o = ocr.get(str(r["product_no"]), {})
            desc = d.get("description") or ""
            dt = d.get("detail_text") or ""
            if dt and dt[:200] != desc[:200]:
                desc = desc + "\n" + dt   # JSON-LD 요약과 본문 글이 다르면 둘 다 읽는다(2026-09-03)
            sbody, scolor = spec_texts(d.get("spec"))
            otext = denoise_ocr(o.get("ocr_text") or "")
            btext = brw.get(r["source_url"], "")
            if btext and btext[:200] != desc[:200]:
                pass          # 브라우저 글은 따로 붙인다 — 원래 글과 겹치면 아래에서 무시된다
            else:
                btext = ""
            body = "\n".join(t for t in (desc, sbody, otext, btext) if t)
            sources = [s for s, t in (("json-ld" if d.get("description_source") == "json-ld" else "html", desc), ("spec", sbody), ("ocr", otext), ("browser", btext)) if t]
            quality = quality_of(body)
            # 상품 이름은 색을 고르는 데 쓰지 않는다. 수집기의 pick_color 가 이미 이름을
            # 읽되 「끝 괄호 → 이름 → 설명 → 스펙」 순서를 지켜 representative_color 를
            # 정해 두었다. 이름을 다시 통째로 훑으면 그 순서가 무너진다 — 수집기 어휘로
            # 갈아탄 뒤 kirsh 「CHERRY」 상품이 전부 레드가 되어 75 → 602 로 뛰었다
            # (체리는 이 브랜드의 마스코트지 옷 색이 아니다, 2026-09-05).
            color_text = " ".join(t for t in (r.get("representative_color", ""), scolor) if t)
            tags = tagger.tag(r["category"], r["name"], body, color_text, quality,
                              sleeve_cm=sleeve_of(r["source_url"]))
            out[r["source_url"]] = {
                "brand_slug": slug, "category": r["category"], "source_quality": quality,
                "text_sources": sources, "tags": tags,
            }
            q_count[quality] += 1
            for s in sources:
                src_count[s] += 1
            cat_count[r["category"] or "(없음)"] += 1
            for ax in tags:
                ax_count[ax] += 1

    # --brands 로 몇 곳만 돌렸으면 나머지 브랜드의 태그를 지우면 안 된다. 예전엔 통째로
    # 덮어써서 「--brands kirsh」 한 번에 파일이 38,341벌 → 1,796벌이 됐다. 그 순간에
    # Actions 가 커밋했으면 36,545벌의 태그가 사라졌을 것이다(2026-09-05에 실제로 밟았다).
    n_run = len(out)
    if args.brands:
        keep = set(args.brands)
        prev = {}
        if Path(args.out).exists():
            try:
                prev = json.loads(Path(args.out).read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                prev = {}
        merged = {u: e for u, e in prev.items() if (e or {}).get("brand_slug") not in keep}
        merged.update(out)
        print(f"  --brands 로 {len(keep)}곳만 돌렸다 — 나머지 {len(merged) - len(out)}벌은 그대로 둔다")
        out = merged
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    n = n_run             # 축 커버리지는 이번에 돌린 만큼으로 잰다(--brands 일 때 헷갈리지 않게)
    print(f"상품 {len(out)} (이번에 돌린 것 {n}) → {args.out}")
    print("품질:", dict(q_count))
    print("본문 출처:", dict(src_count))
    print("축 커버리지:")
    for ax in AXES:
        print(f"  {ax:15s} {ax_count[ax]:6d} ({ax_count[ax] / n:5.1%})")
    if args.report:
        val = {ax: Counter() for ax in AXES}
        for e in out.values():
            for ax, vs in e["tags"].items():
                val[ax].update(vs)
        for ax in AXES:
            print(f"\n[{ax}]", ", ".join(f"{v} {c}" for v, c in val[ax].most_common(12)))


if __name__ == "__main__":
    main()
