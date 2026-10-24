#!/usr/bin/env python3
"""앱이 읽을 모양으로 데이터를 내보낸다 — 얇은 목록 하나 + 브랜드 조각 여럿.

통째로 넣으면 38MB 다(상품 + 태그 + 사이즈, 사진 목록 제외). 지금 앱은 카탈로그를
정적 import 로 읽어(shoppableProducts.ts) 시작할 때 통째로 파싱하므로 그대로는 못 넣는다.
그래서 둘로 가른다.

  products_index.json   목록·검색·필터가 도는 데 필요한 최소 필드만. 37,811벌 9.1MB(gzip 1.6MB).
  brands/<slug>.json    그 매장 상품 전부 + 태그 + 사이즈. 평균 574KB, 제일 큰 곳 1.9MB.
  brands.json           매장 목록(이름·상품 수·대표 사진).

상세 화면은 그 상품의 브랜드 조각만 받으면 된다. 사진은 URL 만 넣는다 — 이미지는 매장
CDN 에서 온다(look_photos.json 33MB 는 앱에 넣지 않는다).

이 모양은 제안이다. 데이터를 받을 구조를 다른 세션에서 짜고 있으므로, 그쪽이 정한 이름과
그릇에 맞춰 바꾼다. 바꿀 자리는 thin() 과 full() 둘뿐이다.

    python scripts/export_app_data.py --out ../layer-web/src/app/data/catalog
    python scripts/export_app_data.py --out /tmp/app --dry     # 크기만 재 본다
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
from datetime import datetime, timezone
from collections import Counter, defaultdict
from urllib.parse import urlsplit
from pathlib import Path

import photo_attrs
import product_desc
import haeyo
import size_from_ocr
import tag_items
from item_hier import HIER   # 품목 계층(갈래 → 큰 품목 → 세부) — catalog.json 에 싣는다. build_vocab 을 거치면 크롤러 의존성이 딸려 온다

WITH_DESC = False
# 그림에서 읽어 둔 상품 글 — (brand_slug, product_no) → detail_from_ocr 이 뽑아 둔 줄.
# 매장 글이 아무 말도 안 할 때 이걸로 메운다(설명이 빈 29,083벌 중 22,214벌이 여기 있다).
MINED: dict[tuple, dict] = {}
# 같은 옷의 다른 색을 한 묶음으로 묶는 번호. 매장은 색마다 상품을 따로 올린다 —
# badblood 「Everyday Scoop Neck Long Sleeve T-Shirt」는 여덟 색이 여덟 상품이다.
# 앱 상세 화면의 「색상 · N」 칩 줄이 이 번호로 형제를 찾는다.
# 열쇠는 size_from_ocr.color_base 를 그대로 쓴다 — 형제에게 사이즈를 물려줄 때 쓰는 것과
# 같은 잣대여야 앱이 보여 주는 형제와 우리가 치수를 물려준 형제가 어긋나지 않는다.
COLOR_GROUP: dict[str, int] = {}
# 기준별 「비슷한 옷」(similar_axes.json) 의 상품 비트 — 줄의 ax(1 색·무늬 · 2 모양 · 4 소재). 상품 id 열쇠.
AX_FLAGS: dict[str, int] = {}
# 사진이 말하는 무늬 · 색(photo_attrs.json, source_url 열쇠)과 사진 그래픽 확신(photo_pred.json) — main 이 채운다.
# pt 는 예전에 similar_axes 의 비트(f & 8)를 옮겨서, 기준별 이웃이 없는 상품은 무늬가 있어도 비었다(아래 pattern_flag).
PHOTO_AT: dict[str, dict] = {}
PHOTO_PRED: dict[str, dict] = {}
PATTERN_FIX: dict[str, int] = {}   # data/pattern_fix.csv — 사람이 사진으로 확인한 pt(photo_attrs.load_pattern_fix)
# 사람이 확정한 색(color_fix.csv) — 무엇보다 앞선다. build-csv 가 대표색에 이미 넣지만, 상품표를 다시 만들기 전에 더한
# 줄(2026-10-28 사진 손 검증 4,336줄 등)도 바로 앱에 나가게 여기서도 먼저 본다. 사진 추정으로 덮지 않는다.
COLOR_FIXED: dict[tuple[str, str], str] = {}
# 상품 무드(pmood) — 상품 id → {무드: 2|1}
PMOOD: dict[str, dict[str, int]] = {}
# 남성 옷에는 안 붙이는 무드 — 앱도 거르지만(2026-10-17) 데이터에서부터 뺀다(꽃무늬 바지에 로맨틱 177벌)
MALE_NO_MOOD = {"페미닌", "걸리시", "로맨틱"}
SIM_MIN = 0.70    # 비슷한 옷 점수 문턱 — 이 아래는 내보내지 않는다(사람 답: 0.70 아래는 아홉 벌에 한 벌꼴로만 골랐다, 대표님 결정 2026-10-06)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


# ── 목록 한 줄 ────────────────────────────────────────────────────────────────
# 앞판은 열쇠를 다 글자로 적고 주소·사진주소를 통째로 넣어 11만 벌이 36.3MB(gzip 5.8MB)
# 였다 — 한 벌 328바이트다. 셋을 고쳐 40바이트로 줄였다:
#
#   ① 브랜드·갈래를 번호로. 번호표는 catalog.json 에만 둔다(브랜드가 늘어도 index 는 그대로)
#   ② 사진 주소에서 **매장마다 가장 긴 공통 앞머리**를 뗀다. 115,934장이 100% 접힌다
#      (아낀 글자 4.9MB). 슬러그로는 못 만든다 — 도메인에 슬러그가 든 건 58%뿐이고
#      83곳은 공용 CDN(cafe24img.poxo.com·ecimg.cafe24img.com)이며, 경로가
#      /web/product/ 로 시작하는 것도 62%뿐이다(나머지는 /pushbuttonspace/web/ 처럼
#      매장 계정명이 낀다). 그래서 앞머리를 매장마다 catalog.json 에 적는다.
#   ③ 상세에서만 쓰는 것(주소·갤러리·태그·치수·설명)은 빼고 브랜드 조각으로 미뤘다
#
# 자리 배열로 바꾸면 4% 더 줄지만(gzip 37B/벌) 읽기 어려워지는 값이 더 크다 —
# gzip 이 반복되는 열쇠를 이미 먹는다. 그래서 **짧은 열쇠 객체**로 둔다(양쪽 합의 2026-09-20).
# ── 스펙표에 무엇을 그리나 (사람 결정 2026-09-20) ─────────────────────────────
# 안 그릴 것을 11만 벌치 나르지 않는다.
#   neckline  25%  하이넥·터틀넥 — 사진이 말한다
#   hardware  57%  지퍼·단추·YKK — 옷을 고를 때 안 본다
#   function  28%  신축성·보온성·경량
#
# `finish_wash` 는 **빼지 않고 가른다.** 처음엔 빈도(31%)만 보고 빼자고 했는데, 앱 쪽이
# 짚었다 — 청바지의 「디스트로이드」는 그 옷이 무릎 찢긴 청바지라고 말해 주는 **유일한
# 줄**이다. 잣대는 빈도가 아니라 붙었을 때 할 말이 있느냐다.
#
# 그래서 눈에 보이는 값은 design_element 로 옮기고 공정 이름은 걷는다. 규칙을 양쪽이
# 따로 들면 한쪽이 낡으므로 **여기 한 군데**에만 둔다(이 저장소가 그걸로 세 번 데였다).
# 창고 어휘 35값을 전부 가른다 — 열둘만 보고 고르면 나머지가 조용히 샌다.
DROP_AXES = {"neckline", "hardware", "function"}

# 여러 공정이 같은 결을 낸다 — 사람 눈에는 「워싱」 하나다
WASH_AS_ONE = {"가먼트워싱", "바이오워싱", "노말워싱", "핸드메이드워싱", "피그먼트워싱",
               "솔트워싱", "더티워싱", "폴딩워싱", "샌드워시"}
# 보이는 것 — 디자인 줄로 옮긴다
FINISH_VISIBLE = {"워싱", "블리치", "빈티지가공", "에이징", "데미지", "페이딩", "디스트로이드",
                  "페인팅", "코팅", "크링클", "엠보싱", "주름가공", "기모", "피치기모", "브러시드"}
# 공정 이름 — 옷을 보고 알 수 없고, 알아도 살지 말지가 안 바뀐다.
# 방수·발수·방풍은 기능이라 function 과 같이 뺀다(그쪽을 살리기로 하면 여기서도 살린다).
FINISH_PROCESS = {"후가공", "흡습가공", "텐타가공", "덤블가공", "실켓가공", "수축방지",
                  "링클프리", "가먼트다잉", "방수", "발수", "방풍"}


def fold_finish(t: dict) -> dict:
    """가공 축을 디자인 줄로 접는다 — 보이는 것만, 워싱은 하나로."""
    fin = t.get("finish_wash") or []
    if not fin:
        return {k: v for k, v in t.items() if k not in DROP_AXES}
    moved = []
    for x in fin:
        x = "워싱" if x in WASH_AS_ONE else x
        if x in FINISH_PROCESS:
            continue
        # 모르는 값은 통과시킨다 — 어휘가 자라는 축이라 버리면 새 말이 조용히 사라진다
        if x in FINISH_VISIBLE or x not in FINISH_PROCESS:
            if x not in moved:
                moved.append(x)
    out = {k: v for k, v in t.items() if k not in DROP_AXES and k != "finish_wash"}
    if moved:
        des = list(out.get("design_element") or [])
        out["design_element"] = des + [x for x in moved if x not in des]
    return out


# ── 무드 ────────────────────────────────────────────────────────────────────
#
# 앱 쪽 지적(2026-09-20): 무드를 아는 매장이 195곳 중 49곳뿐이라 「스트릿」을 눌러도
# 카탈로그의 4분의 1만 보인다. 옛 앱 씨앗 64곳을 되재 보니 **한 브랜드 안에서 무드가
# 늘 하나뿐**이었다 — 상품 무드는 처음부터 브랜드 무드였다. 그러니 상품에 붙이지 않고
# 브랜드 줄에 싣는다.
#
# 값은 이미 `brands_seed.csv` 의 `mood_tags` 에 279곳 전부 있다(사람이 손으로 정했다).
# 앱 씨앗 49곳과 견주면 47곳이 그대로 같다 — 저기서 온 값이 맞다.
#
# 다만 창고 어휘가 14낱말이고 앱 어휘가 9낱말이다. 남는 다섯을 그냥 버리면 10곳이
# 무드 없는 채로 남아 **고치려던 구멍이 작아질 뿐 그대로다.** 그래서 접는다.
# 접는 자리는 브랜드가 아니라 **낱말**이다 — 매장 이름을 코드에 적지 않는다.
# 2026-10-02 코덱스 050 리뷰: 앱 어휘가 그새 14낱말이 됐다(layer-web src/app/lib/quickTagOptions.ts QUICK_TAG_STYLES,
# 2026-08-30 — AGENTS.md 「스타일 어휘 — 단일 출처」). 접으면 온보딩에서 「스포티」를 고른 사람과 스포티 매장이 안 이어진다.
# 그래서 접지 않고 14낱말 그대로 낸다. 창고 어휘와 같다.
MOOD_APP = ["미니멀", "스트릿", "캐주얼", "걸리시", "빈티지", "시크", "클래식", "페미닌", "아방가르드", "로맨틱",
            "Y2K", "워크웨어", "스포티", "고프코어"]
MOOD_FOLD: dict[str, str] = {}


def fold_mood(raw: str) -> list[str]:
    """브랜드 무드를 앱 어휘로 옮긴다 — 모르는 말은 버린다(틀린 값보다 없는 값이 낫다)."""
    out: list[str] = []
    for x in (raw or "").replace("|", ",").split(","):
        x = MOOD_FOLD.get(x.strip(), x.strip())
        if x in MOOD_APP and x not in out:
            out.append(x)
    return out


def pmood_fix(g: dict, bg: dict) -> dict:
    """상품 무드 보정 — 사람 확인 96벌 기준."""
    g = dict(g)
    if bg.get("시크") == 2:
        g.setdefault("시크", 1)
        if not bg.get("캐주얼") and g.get("캐주얼") == 1:
            g.pop("캐주얼")
    if bg.get("스트릿") == 2:
        g.setdefault("스트릿", 1)
    return g


# 「허리께 · 가슴께」 — 바로 안 읽힌다(대표님 2026-10-17)
KKE = re.compile(r"(가슴|허리선|허리|골반|엉덩이|무릎|종아리|발목|허벅지|중간|어깨|배꼽)께")


def chip_mood(v: dict, old: list[str]) -> list[str]:
    """칩: 2점 전부 + 1점으로 셋까지."""
    g = {k: w for k, w in (v.get("g") or {}).items() if k in MOOD_APP}
    key = lambda k: (-g[k], old.index(k) if k in old else 99, MOOD_APP.index(k))
    two = sorted([k for k in g if g[k] >= 2], key=key)
    one = sorted([k for k in g if g[k] == 1], key=key)
    return two + one[:max(0, 3 - len(two))]


GENDER_CODE = {"WOMENSWEAR": "W", "MENSWEAR": "M", "UNISEX": "U"}


def img_prefix(urls: list[str]) -> str:
    """그 매장 사진 주소들의 가장 긴 공통 앞머리 — `/` 까지만 자른다."""
    urls = [u for u in urls if u]
    if not urls:
        return ""
    lo, hi = min(urls), max(urls)
    i = 0
    while i < len(lo) and i < len(hi) and lo[i] == hi[i]:
        i += 1
    head = lo[:i]
    head = head[:head.rfind("/") + 1] if "/" in head else head
    # 그 매장 주소가 하나뿐이면 「공통 앞머리」가 주소 **전체**가 된다. 그러면 꼬리가
    # 비어 앱이 주소를 못 만들거나(앞서 셋이 그랬다), 되살리려고 꼬리에 통째로 적으면
    # 앞머리 + 주소로 **두 번 붙는다**. 앞머리는 언제나 주소보다 짧아야 한다.
    while head and any(head == u for u in urls):
        head = head[:head[:-1].rfind("/") + 1]
    return head


AX_MIN = 3        # 기준별 목록도 「비슷한 옷」과 같은 규칙 — SIM_MIN 미만을 빼고 3벌 미만이면 그 기준을 안 싣는다(대표님 규칙을 탭에도, 2026-10-07)


def ax_pairs(nb: list, aidx: list, ok_ids: set) -> list:
    """similar_axes.json 의 [번호, 점수×1000, …] → [[이웃 id, 점수], …] — 앱에 나가는 이웃 · 점수 SIM_MIN 이상만."""
    return [[aidx[j], min(1.0, max(0.0, sc / 1000))] for j, sc in zip(nb[0::2], nb[1::2])
            if 0 <= j < len(aidx) and aidx[j] in ok_ids and sc / 1000 >= SIM_MIN]


# ── 데님 워싱 단계 dw (앱 세션 부탁 2026-10-09 — 상·하의 색 조합 가이드가 데님을 연청 · 중청 · 진청 · 생지 · 흑청으로 나눠 말한다) ──
# 데님 옷(데님 칸 · 데님팬츠 · 대표 소재 데님인 옷)에만. 대표색 → 이름 → 옵션(모두 한 단계일 때만) 차례로 낱말을 찾고,
# 모르면 비운다(추측 금지). 「블루」 · 「인디고」 · 「네이비」만으로는 단계를 모른다. 「raw edge · raw hem」은 밑단 가공이라 생지가 아니다.
# 「SNAKE BONE WASHED」 · 「TWO TONE WASHING」의 one wash, 「슬로우 데님」의 로우는 낱말 조각이라 생지가 아니다(2026-10-27).
DW_RULES = (
    ("흑청", r"흑청|washed\s*black|faded\s*black|워시드\s*블랙|페이디드\s*블랙|black\s*(?:wash|fade)|블랙\s*워싱"),
    ("생지", r"생지|(?<![a-z])raw(?!\s*-?\s*(?:edge|hem|cut|finish))(?![a-z])|rigid|unwashed|논워시|non[\s-]*wash|(?<![가-힣])로우\s*(?:인디고|데님|진)|원워시|(?<![a-z])one[\s-]*wash|rinse|린스"),
    ("연청", r"연청|light\s*(?:blue|wash|indigo|denim|vintage)|라이트\s*(?:블루|워시|인디고|데님|빈티지)|ice\s*blue|아이스\s*블루|bleach|블리치|sky\s*blue|스카이\s*블루|pale\s*blue|faded\s*blue|lt\.?\s*blue|l/blue"),
    ("중청", r"중청|mid(?:dle|ium)?\s*(?:blue|wash|indigo|denim|vintage)|미드\s*(?:블루|워시|인디고|빈티지)|미디엄\s*(?:블루|워시|인디고)|m/blue"),
    ("진청", r"진청|dark\s*(?:blue|wash|indigo|denim|navy|vintage)|다크\s*(?:블루|워시|인디고|데님|네이비|빈티지)|deep\s*(?:blue|indigo)|딥\s*(?:블루|인디고)|d/blue|dk\.?\s*blue"),
    ("블랙", r"(?<![a-z])black(?![a-z])|블랙"),
    ("화이트", r"(?<![a-z])white(?![a-z])|화이트|ecru|에크루|ivory|아이보리|cream|크림|off[\s-]*white"),
    # 그레이 · 차콜 데님은 검정 데님이 바랜 것 — 흑청이다(사진 대조 2026-10-27: 「그레이」 데님 12벌 중 10벌이 회흑색 워싱, 판매중
    # 약 120벌이 컬러로 서 있었다 · blr-111 · plac-15384 · noirer-1416). 「진 그레이 블루」처럼 파랑 낱말이 같이 있으면 파란 데님이라
    # 단계를 모른다 — wash_of 가 거른다(_DW_BLUE).
    ("흑청", r"(?<![a-z])(?:grey|gray|charcoal)(?![a-z])|그레이|차콜|d/gr[ae]y"),
    # 영문은 낱말 경계 — 「colored」의 red 가 레드로 걸려 인디고 원워시가 컬러가 됐다(amomento-4195). 「sand wash」는 가공 이름이다.
    ("컬러", r"(?<![a-z])(?:pink|brown|khaki|green|beige|red|olive|purple|yellow|mocha|camel|mint)(?![a-z])|(?<![a-z])sand(?!\s*-?\s*wash)(?![a-z])|"
            r"핑크|브라운|카키|그린|베이지|레드|올리브|퍼플|옐로우|모카|카멜|샌드(?!\s*워)|민트"),
)
DW_RX = [(k, re.compile(rx, re.I)) for k, rx in DW_RULES]
DW_CATS = {"Outerwear", "Tops", "Shirts", "Knitwear", "Pants", "Denim", "Skirts", "Dresses"}
# 파란 데님 낱말 — 그레이 · 색 낱말과 같이 적힌 「그레이 블루」 · 「인디고 레드」(alyss-323 — 브라운 틴트 진청)는 파란 데님에 물을 들인
# 것이라 흑청 · 컬러가 아니다. 단계는 모르니 비우고 사진 판정에 맡긴다.
_DW_BLUE = re.compile(r"(?<![a-z])(?:blue|indigo|navy)(?![a-z])|블루|인디고|네이비|청(?![가-힣])", re.I)
# 매장이 색 이름으로 쓰는 단계 낱말 — not4nerd 는 파란 데님 색 이름이 늘 「Deep Blue · Deep Indigo」다(라이트 블루와 짝). 사진 대조 8벌이
# 모두 연청 · 중청 빈티지 워싱이었다(판매중 44벌이 진청으로 서 있었다, 2026-10-27). 그 매장에서는 그 낱말로 단계를 정하지 않는다.
DW_COLOURWAY = {"not4nerd": re.compile(r"(?i)deep\s*(?:blue|indigo)")}


_DW_SOFT = {len(DW_RULES) - 2, len(DW_RULES) - 1}   # 그레이 → 흑청 · 컬러 — 파랑 낱말이 같이 있으면 안 믿는다


def wash_of(text: str, ctx: str = "") -> str:
    """text 의 단계 낱말. ctx 는 파랑 낱말을 같이 볼 글(대표색의 그레이를 이름의 「blue grey」와 견준다 — facade-pattern-3872)."""
    for i, (k, rx) in enumerate(DW_RX):
        if rx.search(text or ""):
            return "" if i in _DW_SOFT and _DW_BLUE.search(f"{text} {ctx}") else k
    return ""


_WORN: dict | None = None
_WORN_GARMENT = {"Outerwear", "Tops", "Shirts", "Knitwear", "Pants", "Denim", "Skirts", "Dresses"}


def worn(r: dict) -> int:
    """대표 사진이 착용컷인가(1) — scripts/worn_photo.py 가 잰 옷 분할 넓이로(앱 세션 부탁 2026-10-11, 브랜드 명패 · 카드에 착용컷 먼저).
    사람 부위(팔 · 다리 0.8% 넘게, 머리카락 · 얼굴 1.3% 넘게)가 보이거나, 멀리서 찍은 전신 컷(윗옷 · 하의가 각 3% 넘고 얼굴 ·
    머리카락이 조금이라도, 옷이면 신발이라도)이면 착용컷. 신발은 살색 신발을 다리로 칠하므로 다리 6% 넘게만.
    표본 157장 사람 눈 대조: 정밀도 100% · 재현율 97%(놓친 것은 얼굴 · 팔다리 없는 허리 · 치마 컷). 잰 사진이 지금 대표 사진과
    다르면(매장이 바꿈) 0.
    2026-10-27: 글-사진 착용 점수(scripts/worn_zs.py → data/worn_zs.json)와 합친다 — 아래 WORN_ZS_* 주석. 점수 파일이 없으면 예전 그대로."""
    global _WORN, _WORN_ZS, _WORN_ZS_IMAGES
    if _WORN is None:
        _WORN = {}
        for p in sorted((DATA / "worn_photo").glob("*.jsonl")):
            for l in p.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    o = json.loads(l)
                    _WORN[o["u"]] = (o["img"], o["fr"])
    if _WORN_ZS is None:
        try:
            scores = json.loads((DATA / "worn_zs.json").read_text(encoding="utf-8"))
            _WORN_ZS = scores.get("items") or {}
            _WORN_ZS_IMAGES = scores.get("images") or {}
        except (OSError, ValueError):
            _WORN_ZS = {}
    img, fr = _WORN.get(r["source_url"], ("", None))
    z = _WORN_ZS.get(r["source_url"])
    zimg = _WORN_ZS_IMAGES.get(r["source_url"])
    if zimg and zimg != r.get("image_url"):
        z = None
    # 대표 사진이 바뀐 증거가 있으면 사진 주소 없는 구판 점수로 되살리지 않는다(2026-10-28).
    if fr is not None and img != r.get("image_url") and not zimg:
        z = None
    if fr is None or img != r.get("image_url"):
        return int(z is not None and z >= WORN_ZS_ALONE)
    seg = _worn_seg(fr, r.get("category"))
    if z is None:
        return seg
    if z < WORN_ZS_VETO and fr.get("2", 0) + fr.get("11", 0) < 0.15:
        return 0      # 얼굴 · 머리카락이 사진의 15%를 넘게 칠해졌으면 얼굴 클로즈업이다 — 점수가 낮아도 둔다(miseki-seoul 비니 착용컷)
    body = sum(fr.get(k, 0) for k in ("2", "9", "10", "11", "12", "13", "14", "15"))
    return int(seg or z >= WORN_ZS_SURE or (z >= WORN_ZS_BODY and body >= 0.004))


# 글-사진 착용 점수(scripts/worn_zs.py)와 합치는 문턱 — 사진 대조 QA(2026-10-27)의 눈 대조 표본으로 골랐다(구간별 표본 · 맞은 수).
#   분할 판정 1 인데 점수 < 0.1     → 0  (44장 중 43장이 흰 배경 모자 · 장갑 · 가방 · 납작한 옷 — 판매중 약 770벌. 무작위 재검 34장 중
#                                       30장이 맞고 넷은 모델 컷이었다 — 임베딩이 옛 대표 사진의 것으로 보인다. 얼굴 · 머리카락이 크게
#                                       칠해진 컷은 빼고 끈다)
#   분할 판정 0 인데 점수 ≥ 0.7     → 1  (72장 중 70장이 허리 아래 바지 컷 · 목 · 손 클로즈업 — 약 640벌)
#   분할 판정 0, 점수 0.5 ~ 0.7    → 분할 모델이 몸(머리카락 · 얼굴 · 팔 · 다리 · 신발)을 0.4% 넘게 칠했으면 1 (24장 중 23장)
#   분할 판정 0, 점수 0.3 ~ 0.5    → 그대로 0 (36장 중 21장만 착용컷이라 못 믿는다)
#   분할 판정 1, 점수 0.1 ~ 0.3    → 그대로 1 (헤드리스 스튜디오 착용컷이 많다 — 0.1~0.2 구간 26장 중 17장)
#   분할 모델이 안 잰 사진(측정 안 된 매장 · 대표 사진이 바뀐 상품) → 점수 ≥ 0.3 이면 1 (71장 중 70장), 0.15~0.3 은 36장 중 2장뿐이라 0
WORN_ZS_VETO, WORN_ZS_SURE, WORN_ZS_BODY, WORN_ZS_ALONE = 0.1, 0.7, 0.5, 0.3
_WORN_ZS: dict | None = None
_WORN_ZS_IMAGES: dict = {}


def _worn_seg(fr: dict, category: str | None) -> int:
    """옷 분할 넓이만으로 본 착용컷 판정(2026-10-11 규칙 그대로)."""
    g = lambda *ks: sum(fr.get(str(k), 0) for k in ks)  # noqa: E731
    hf, arm, leg, shoe = g(2, 11), g(14, 15), g(12, 13), g(9, 10)
    if category == "Shoes":
        return int(hf >= 0.013 or arm >= 0.008 or leg >= 0.06)
    # 옷이 아닌 상품(가방 · 벨트 · 모자 …)은 사람이 입은 옷도 보여야 한다 — 살색 가죽 벨트를 팔로 칠한 것(1차 결과 눈 대조 2026-10-11)
    if category not in _WORN_GARMENT and g(4, 5, 6, 7) < 0.03:
        return 0
    return int(arm + leg >= 0.008 or hf >= 0.013
               or (g(4) >= 0.03 and g(5, 6) >= 0.03
                   and (hf >= 0.004 or (shoe >= 0.005 and category in _WORN_GARMENT))))


_DW_PHOTO: dict | None = None


def dw_photo(url: str) -> str:
    """이름 · 색 · 옵션 낱말로 못 정한 데님의 사진 판정(scripts/denim_wash_photo.py — 교차검증 정밀도 90% 넘는 것만)."""
    global _DW_PHOTO
    if _DW_PHOTO is None:
        try:
            _DW_PHOTO = json.loads((DATA / "denim_wash_photo.json").read_text(encoding="utf-8")).get("items") or {}
        except (OSError, ValueError):
            _DW_PHOTO = {}
    return (_DW_PHOTO.get(url) or {}).get("w", "")


def _one_wash(texts: list[str], ctx: str = "") -> tuple[str, bool]:
    """여러 토막이 말하는 단계 — (한 단계면 그것, 서로 다른 단계가 섞였는가)."""
    # 단계를 모르는 파랑(「블루 · 인디고 · 네이비」)도 한 갈래로 센다 — 「블루·블랙」은 파란 옷과 검은 옷을 같이 판다는 말이다
    ws = {wash_of(t, ctx) or ("?" if _DW_BLUE.search(t) else "") for t in texts if t}
    ws.discard("")
    if "?" in ws:
        return "", len(ws) > 1
    return (ws.pop() if len(ws) == 1 else ""), len(ws) > 1


def denim_wash(name: str, rep_color: str, options: str, slug: str = "") -> str:
    """대표색 → 이름 → 옵션(모두 한 단계일 때만). 모르면 빈칸.
    대표색이 여러 색(「블루·블랙」 · 「화이트·블랙·그레이」)이면 색마다 단계를 보고 갈리면 대표색으로 정하지 않는다 — 여러 색을 파는
    옷이라 사진이 어느 색인지 모른다(wednesday-oasis-1039 「_2Color」 파란 치마 · gongdreen-335 연청 청바지가 블랙이었다, 2026-10-27).
    이름도 그 매장의 색 이름 낱말(DW_COLOURWAY)은 지우고 본다."""
    cw = DW_COLOURWAY.get(slug)
    if cw:
        name, options = cw.sub(" ", name or ""), cw.sub(" ", options or "")
    w, mixed = _one_wash([c.strip() for c in re.split(r"[·,/]", rep_color or "")], name)
    if w and w != "컬러":
        return w
    w2 = wash_of(name)
    if w2:
        return w2
    if w:
        return w
    if mixed:
        return ""
    opts = [o for o in re.split(r"\s*\|\s*", options or "") if o]
    w3, mixed = _one_wash(opts)
    return w3 if not mixed else ""


# ── 하의 디테일 dt (앱 세션 부탁 2026-10-10) — 세부품목은 한 상품에 하나라 「데님 카고」는 데님팬츠로만 보인다. 디테일은 따로 싣는다.
# 근거는 **상품명**과 세부품목 이름만. 상세 글에서 뽑은 pants_type 태그는 안 쓴다 — 「slacks」에 벌룬, 「WIDE-LEG JEANS」에 카고가
# 붙는 등 이름에 없는 태그 283(카고) · 125(벌룬)벌 중 표본이 대부분 틀렸다(2026-10-10 점검).
DT_RULES = (("카고", r"카고|cargo"), ("카펜터", r"카펜터|carpenter"), ("퍼티그", r"퍼티그|파티그|fatigue"),
            ("파라슈트", r"파라슈트|parachute"), ("벌룬", r"벌룬|balloon"))
DT_RX = [(k, re.compile(rx, re.I)) for k, rx in DT_RULES]
DT_OF_SUB = {"카고팬츠": "카고", "카펜터팬츠": "카펜터", "퍼티그팬츠": "퍼티그", "파라슈트팬츠": "파라슈트", "벌룬팬츠": "벌룬"}
DT_CATS = {"Pants", "Denim", "Skirts"}


def bottom_details(name: str, subtype: str) -> list[str]:
    # 「NO FATIGUE」는 라인 이름이다 — 품목에서 뺀 퍼티그가 디테일 필터로 다시 붙지 않게(2026-10-28).
    name = re.sub(r"(?i)(?<![a-z])(?:no|anti)[\s_-]*fatigue(?![a-z])", " ", name or "")
    got = [k for k, rx in DT_RX if rx.search(name or "")]
    s = DT_OF_SUB.get(subtype or "")
    if s and s not in got:
        got.append(s)
    return [k for k, _ in DT_RULES if k in got]


# ── 대표소재 ma — 그 소재가 장식으로만 조금 든 옷(앱 세션 · 사용자 2026-10-10: 「그 소재가 룩의 주인공인가」) ──
# 앱 garmentUnits.ts partialMaterial 과 같은 규칙: 소재 말 바로 뒤에 탭 · 포인트 · 트리밍 · 슬리브 · 배색 · 요크 · 커프스(퍼가 아니면 칼라도)가
# 오는 옷(「B-15 레더 탭 쉐르파 봄버」 · 「코듀로이 포인트 셔츠」 · 「퍼 트리밍 조거」), 그리고 몸판 울 · 소매 가죽인 바시티(이름에 가죽 말이
# 없거나 울이 같이 적힘). 믹스드 레더 · 퍼 후드 · 퍼 칼라처럼 그 소재가 주인공인 옷은 그대로 둔다. 걸리면 혼용률(mat)의 겉감 · 몸판 첫 섬유
# (그것도 같은 소재면 그대로), 없으면 그 소재가 아닌 다음 소재, 그것도 없으면 빈칸.
_MA_WORD = {"가죽": "레더|가죽|leather", "인조가죽": "레더|가죽|leather", "스웨이드": "스웨이드|suede", "데님": "데님|denim",
            "트위드": "트위드|tweed", "코듀로이": "코듀로이|corduroy|골덴", "퍼": "퍼|fur|시어링|shearling|쉐르파|sherpa",
            "린넨": "린넨|리넨|linen", "나일론": "나일론|nylon"}
_MA_ACCENT = r"트리밍|trim|포인트|point|탭|tabs?\b|슬리브(?!리스)|sleeves?\b|배색|요크|yoke|커프스?|cuffs?"
_MA_COLLAR = r"(?:칼라|카라)(?!리스)|collar(?!less)"
_LEATHER_WORD = re.compile(r"(?i)레더|가죽|leather|램스킨|lamb|카우|cow|고트|goat|쉽스킨|sheep")
_BODY_PART = re.compile(r"^(?:겉감|몸판|본체|몸통|앞판|shell|body)\s*1?$", re.I)
_SAME_FIBER = {"가죽": ("가죽", "램스킨", "스웨이드", "폴리우레탄"), "인조가죽": ("가죽", "폴리우레탄", "인조")}


def partial_material(ma: str, sub: str, name: str) -> bool:
    w = _MA_WORD.get(ma)
    if not w or not name:
        return False
    after = _MA_ACCENT if ma == "퍼" else f"{_MA_ACCENT}|{_MA_COLLAR}"
    if re.search(f"({w})[\\s-]*({after})", name, re.I):
        return True
    if ma in ("가죽", "인조가죽") and sub == "바시티":
        return not _LEATHER_WORD.search(name) or bool(re.search(r"(?i)울|wool", name))
    return False


def _partial_ma(r: dict, mats: list, mat: list | None) -> str:
    ma = mats[0] if mats else ""
    if not ma or r.get("category") not in DW_CATS or not partial_material(ma, r.get("subtype") or "", r.get("name") or ""):
        return ma
    same = _SAME_FIBER.get(ma, (ma,))
    body = next((g for g in (mat or []) if isinstance(g, dict) and _BODY_PART.match(str(g.get("p") or "").strip())), None)
    if body and body.get("v"):
        f = str(body["v"][0][0])
        return ma if any(x in f for x in same) else f
    return next((m for m in mats[1:] if m != ma and not any(x in m for x in same)), "")


# ── 대표소재 ma 는 겉감에서 가장 많은 섬유(qa27 2026-10-27) ─────────────────────────────────────────
# 소재 태그(tags.material)의 첫 칸을 그대로 썼다. 그 차례는 「이름에 적힌 소재 → 혼용률 → 글」이라(tag_items.order_materials —
# 이름의 소재가 거의 맞는다는 사람 판단 2026-09-23) 필터 · 유사도에는 맞지만 「대표」로는 어긋났다(전수):
#   · 보조 섬유가 대표 3,532벌 — 「CASHMERE WOOL V-NECK KNIT」 울 90 · 캐시미어 10 → 캐시미어, 「울 유넥 탑」 레이온 81 · 울 12 → 울,
#     「SPORTY … SWEATSHIRTS」 폴리 92 · 스판 8 → 스판덱스(drawfit · generalidea · till-i-die · facade-pattern …)
#   · 혼용률에 아예 없는 섬유 764벌 — wednesday-oasis 208벌이 전부 레이온(혼용률은 코튼 100 등), the-coldest-moment 견본의 코튼.
# 그래서 섬유 이름인 ma 는 혼용률 **겉감 쪽 부위**(빈칸 · 겉감 · 원단 · 몸판)에서 비율이 가장 큰 섬유로 바꾼다. 안감 · 배색 ·
# 시보리 · 충전재는 이기지 못한다. 가죽 겉감이면 가죽이 대표다(안감 섬유가 아니다). 겉감 부위가 없으면(안감만 · 세트 구성품만)
# 손대지 않는다. 섬유가 아닌 겉보기 소재(니트 · 데님 · 스웨이드 · 퍼 · 코듀로이 · 기모 …)와 다운(충전재지만 그 옷을 찾는 말)은
# 혼용률로 못 가리므로 그대로 둔다 — 페이크 스웨이드는 폴리에스터 100 이 맞다.
# 인조가죽은 겉보기 소재로 둔다 — 혼용률은 「폴리우레탄 60 · 폴리에스터 40」(코팅 · 바탕천)이라 섬유로 고르면 바탕천이 대표가 된다.
_FIBER_MA = (tag_items.FIBERS - {"다운"}) | {"라미", "야크", "앙고라", "카멜"}
_LEATHER_MA = tag_items.REAL_LEATHER | {"인조가죽"}
_SHELL_ORDER = (re.compile(r"^(?:겉감|몸판|본판|메인)\s*1?$"), re.compile(r"^$"), re.compile(r"^원단\s*1?$"))
_MA_SKIP = {"기타", "깃털"}


def shell_part(mat: list | None) -> dict | None:
    groups = [g for g in (mat or []) if isinstance(g, dict) and g.get("v")]
    for rx in _SHELL_ORDER:
        g = next((g for g in groups if rx.match(str(g.get("p") or "").strip())), None)
        if g:
            return g
    return None


def shell_top(mat: list | None) -> str:
    g = shell_part(mat)
    if not g:
        return ""
    best, bv = "", -1.0
    for f, v, *_ in g["v"]:
        f = str(f)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if f not in _MA_SKIP and v > bv:          # 같은 비율이면 먼저 적힌 것
            best, bv = f, v
    # 반 넘는 폴리우레탄은 코팅(인조가죽 · PU 필름)이라 섬유 대표로 안 쓴다 — 아래 _FIBER_MA 에 없어 ma 를 그대로 둔다
    return "스판덱스" if best == "폴리우레탄" and bv < 50 else best


def main_material(r: dict, t: dict, mat: list | None) -> str:
    mats = t.get("material") or []
    ma = _partial_ma(r, mats, mat)
    top = shell_top(mat)
    if not top or top not in _FIBER_MA:
        return ma
    if not ma:
        return top                    # 소재 태그는 못 정했어도 혼용률 겉감은 있다
    if ma not in _FIBER_MA:
        return ma                     # 겉보기 소재 · 다운
    if ma in _LEATHER_MA and top in _LEATHER_MA:
        return ma                     # 「가죽」 · 「소가죽」 — 같은 가죽(앱 필터 칸이 다르다)
    if ma in _LEATHER_MA and _LEATHER_WORD.search(r.get("name") or ""):
        # 이름이 가죽 옷이라는데 겉감이 섬유면 대개 페이크 레더의 바탕천이다 — 「LEATHER HALF COAT」 폴리 100(nick-nicole 7666),
        # 「크랙 레더 자켓」 레이온 55 · 스판 45(drawfit 2424). 겉보기 소재(가죽)가 대표다. 이름에 가죽 말이 없는 가방의 가죽
        # (vunque 매장 글의 「천연가죽」 — 혼용률은 나일론 100)은 혼용률을 따른다.
        return ma
    return top


def _num(v) -> int:
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def sale_ok(r: dict) -> bool:
    """할인가는 크게 깎여도 믿는다 — kirsh 「윈드브레이커」 159,000 → 30,000 은 매장이 「81% Sale」로 적은 진짜 값(2026-10-18 확인)."""
    return _num(r.get("sale_price")) > 0


def list_ok(r: dict) -> bool:
    """정가가 지금 값의 다섯 배를 넘으면 엉뚱한 칸을 읽은 것(thug-club 899,016,453 ↔ 590,000)."""
    l, p = _num(r.get("list_price")), max(sell_price(r), _num(r.get("price")))
    return l > 0 and not (p and l > 5 * p)


def sell_price(r: dict) -> int:
    """지금 사는 값 — 할인가(믿을 만할 때) · 정가 · 값 순. 정가가 터무니없으면 값."""
    if sale_ok(r):
        return _num(r.get("sale_price"))
    l, p = _num(r.get("list_price")), _num(r.get("price"))
    if l and p and l > 5 * p:
        return p
    return l or p


def index_color(r: dict, tags: dict) -> str:
    fixed = COLOR_FIXED.get((r.get("brand_slug"), str(r.get("product_no"))))
    if fixed:
        return fixed
    colors = [x.strip() for x in (r.get("representative_color") or "").split("·") if x.strip()]
    if len(colors) == 1:
        return photo_color(r, colors[0])
    if len(colors) > 1:
        from crawl_cafe24 import pick_color
        named = pick_color(r.get("name") or "", "", options=colors)
        if named in colors:
            return named
        # 이름이 색을 안 고르면 대표 사진이 보이는 판매색(옷만, photo_attrs.color_among)
        if ((r.get("category_code") or "") in photo_attrs.GARMENT_CODES
                and photo_attrs.photo_safe(r.get("name") or "")):
            return photo_attrs.color_among(colors, PHOTO_AT.get(r.get("source_url") or ""))
        return ""
    fallback = tags.get("color") or []
    if len(fallback) == 1:
        return photo_color(r, fallback[0])
    # 색을 어디서도 못 읽은 상품만 사진으로 채운다(grove 790/915 · loeuvre 173 · bmuette 71 이 비어 색 필터에서 빠졌다).
    # 「[3COLOR]」 · 「(2color)」처럼 여러 색을 한 목록에 파는 상품은 사진이 한 색만 보이니 채우지 않는다.
    if fallback or re.search(r"\d\s*(?:colou?rs?|컬러|색상)|멀티|multi", r.get("name") or "", re.I):
        return ""
    return photo_color(r, "", empty_ok=True)


def named_colors(name: str) -> list[str]:
    """이름의 색 자리(끝 괄호 · 「_」 · 「 - 」 뒤, 없으면 이름 전체)에 낱말 단위로 적힌 색들, 앞에서부터."""
    from crawl_cafe24 import match_color
    m = (re.search(r"[(\[]([^)\]]{1,30})[)\]]\s*$", name or "")
         or re.search(r"(?:_|\s[-–/]\s*)([^_()\[\]/–-]{1,30}?)\s*$", name or ""))
    seg = m.group(1) if m else (name or "")
    got: list[str] = []
    for w in re.split(r"[\s/&+,·]+", seg):
        c = match_color(w, compound=False) if w else ""
        if c and c != "멀티" and c not in got:
            got.append(c)
    return got


def photo_color(r: dict, co: str, empty_ok: bool = False) -> str:
    """사진 선별기로 대표색을 고치거나(옷만) 빈 대표색을 채운다 — 문턱은 photo_attrs.color_photo 주석(눈 검증 2026-10-27).
    매장이 여러 색을 함께 파는 상품(대표색 「블랙·화이트」)은 여기 오지 않는다 — 판매색 안에서만 고른다(color_among)."""
    if ((r.get("category_code") or "") not in photo_attrs.GARMENT_CODES
            or not photo_attrs.photo_safe(r.get("name") or "")
            or (r.get("brand_slug"), str(r.get("product_no"))) in COLOR_FIXED):
        return co
    e = PHOTO_AT.get(r.get("source_url") or "")
    if not e or (not co and not empty_ok):
        return co
    pair = named_colors(r.get("name") or "")
    alt = next((c for c in pair if c != co), "") if co in pair and len(pair) == 2 else ""
    return photo_attrs.color_photo(co, e, alt=alt) or co


def pattern_flag(r: dict, t: dict) -> int:
    """pt — 무늬 있음 1. 판정은 photo_attrs.pattern_keys(이름 무늬 낱말 > 사진 > 본문 태그). 잡화는 이름만 본다."""
    u = r.get("source_url") or ""
    code = r.get("category_code") or ""
    garment = code in photo_attrs.GARMENT_CODES or code == "suiting"
    ks = photo_attrs.pattern_keys(r.get("name") or "", t,
                                  photo=PHOTO_AT.get(u) if code in photo_attrs.GARMENT_CODES else None,
                                  graphic=(PHOTO_PRED.get(u) or {}).get("graphic", 0), garment=garment,
                                  brand=r.get("brand_slug") or "")
    ks = photo_attrs.apply_pattern_fix(f'{r["brand_slug"]}-{r["product_no"]}', ks, PATTERN_FIX)
    return 1 if ks else 0


# ── 앱에 보이는 이름(n) ───────────────────────────────────────────────────────
# 매장 이름 칸에는 옷 이름 말고 판매 안내가 붙어 온다(텍스트 QA 2026-10-27 — 판매중 목록 12,638벌 · 125곳):
#     badblood  「[w-hat26-017] 미스틱 빅패치 볼캡 - 레드블랙」          품번 1,339
#     dunst     「ADVENTURE BUCKET HAT OLIVE KHAKI_UDHE6E102K1」        품번 1,314
#     grove     「JESS KNIT [3COLOR]」                                  색 개수 342
#     vunque    「[아울렛] Dual Pants Skirt (듀얼 팬츠 스커트) Navy」     420
#     juntae-kim「… TOTE BAG (시즌오프 25% off, 회원가입 시 30% off 자동적용)」
#     leire     「[10/29 예약배송] 에센셜 플리스 조거 팬츠 [브라운]」 · nick-nicole 「[서예지 PICK] …」
# **보이는 이름만** 씻는다. 상품표의 name 은 그대로다 — 색 형제 묶기(size_from_ocr.color_base: 품번이 있으면 품번이
# 알맹이다) · 갈래 · 태그가 원래 이름을 읽는다. 색 이름(「- 블랙」「(BLACK)」)은 어느 색인지 가르는 말이라 둔다.
# 상품 상태를 말하는 괄호(리퍼브 · B품 · 샘플)도 둔다 — 사는 사람이 알아야 한다.
_NAME_PROMO = re.compile(
    r"(?<!\d)\d{1,2}\s*%\s*(?:off|할인|sale|세일|dc)\b|할인|쿠폰|아울렛|\boutlet\b|시즌\s?오프|특가|"
    r"타임\s?딜|라스트\s?(?:찬스|피스|원)|\blast\s?(?:one|chance|piece)\b|파이널\s?세일|final\s?sale|\bsale\b|(?<![가-힣])세일(?![가-힣])|"
    r"런칭\s?프로모션|limited\s?offer|무료\s?배송|회원\s?가입|적립|"
    r"예약|선\s?주문|pre-?order|프리\s?오더|발송|배송|출고|발매|입고\s?예정|reserved\s?delivery|\d{1,2}\s*월\s*\d{1,2}\s*일|"
    r"re-?stock|re-?order|리오더|재입고|재오픈|재진행|\d+\s?차(?![가-힣])|sold\s?out|품절|"
    r"(?<![A-Za-z])pick(?![A-Za-z])|착용\s*$|^\s*(?:best|new|hot|신상|인기|md\s?추천)\s*$|"
    r"(?<!\d)\d{1,2}\s*colou?rs?\b|(?<!\d)\d{1,2}\s*col\b|\d{1,2}\s*컬러|(?<!\d)\d{1,2}\s*색(?![가-힣])", re.I)
# 대괄호에서만 판촉인 꼴 — 「[30%]」「[3rd]」「[10/29]」. 둥근 괄호의 「M.Grey(1%)」(heritagefloss) · 「CAP(456th)」
# (outstanding) · 「(1/2)」는 이름의 일부다.
_NAME_PROMO_SQ = re.compile(r"^\s*(?:-?\d{1,2}\s*%|\d+\s?(?:st|nd|rd|th)|\d{1,2}\s*[/.]\s*\d{1,2})\s*$", re.I)
_NAME_KEEP = re.compile(r"리퍼|refurb|b\s?품|b-?grade|하자|샘플|sample", re.I)
_NAME_BRACKET = re.compile(r"\[([^\[\]]{1,60})\]|\(([^()]{1,60})\)")
_NAME_COLOR_COUNT = re.compile(r"(?<!\d)\d{1,2}\s*(?:colou?rs?\b|col\b|컬러|색(?![가-힣]))", re.I)
# 괄호 없이 끝에 붙는 꼬리 — 「_2color」「 3colors」「- 5 COLOR」「_3rd Restock」「 / 10월 Restock」「… 174,000」
_NAME_TAIL = re.compile(
    r"[\s_/-]+(?:\d+\s*colou?rs?|\d+\s*col|\d+\s*컬러|(?:\d{1,2}월\s*)?(?:\d+(?:st|nd|rd|th)\s+)?re-?stock(?:\s+\d{1,2}[./]\d{1,2})?|"
    r"(?:\d+(?:st|nd|rd|th)\s+)?re-?order|리오더|재입고|\d{1,3}(?:,\d{3})+\s*원?)\s*$", re.I)
_NAME_HEAD = re.compile(r"^\s*(?:\d+(?:st|nd|rd|th)\)\s*|\d+\s?차\s+)", re.I)          # haiq 「4th) a tria bag」 · 「5차 Washed …」
_NAME_STARRED = re.compile(r"\*\s*(?:\d+(?:st|nd|rd|th)\s*)?re-?stock\s*\*", re.I)    # haleine 「… *2nd RESTOCK*」
# 품번 — 대문자 · 숫자가 두 번 넘게 갈마드는 일곱 자 넘는 덩이(UDHE6E102K1 · TISU01OP02 · RMTWG23R14 · 2GMUSK09 · P0000DUF).
# 「N-3B」「MA-1」「T44」「EXP-50S」 같은 모델 이름은 짧아서 안 걸린다.
_NAME_CODE = re.compile(r"(?<![A-Za-z0-9-])([A-Z0-9]{2,}(?:-[A-Z0-9]{2,}){0,2})(?![A-Za-z0-9-])")
_NAME_BADBLOOD = re.compile(r"\[[a-z]-[a-z]+\d{2}-\d{3}\]\s*")                       # badblood 「[w-jkt25-013]」
_NAME_LEAD_CODE = re.compile(r"^(\s*(?:\[[^\]]*\]\s*)*)[A-Z]{2,4}\d{3,5}\s+(?=[A-Za-z가-힣])")   # siyazu 「SIOT4198 …」
_NAME_LOWER_CODE = re.compile(r"\s+[a-z]{3}\d{3,4}[a-z](?=\s*\(|\s*$)")              # andersson-bell 「aaa507w(BLACK)」
_NAME_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍඀-෿♡♥❤★☆]")


def _name_code(tok: str) -> bool:
    t = tok.replace("-", "")
    if len(t) < 7 or not t.isalnum() or not t.isascii() or t.upper() != t:
        return False
    flips = sum(1 for a, b in zip(t, t[1:]) if a.isdigit() != b.isdigit())
    # 줄표로 셋 넘게 이은 품번(ava-molli 「ADS-SKB301-20」)은 갈마듦이 한 번이어도 품번이다
    return sum(c.isdigit() for c in t) >= 2 and sum(c.isalpha() for c in t) >= 2 and (flips >= 2 or tok.count("-") >= 2)


def _name_bracket(m: re.Match) -> str:
    inner = m.group(1) if m.group(1) is not None else m.group(2)
    # 색 목록과 개수가 한 괄호에 있으면 개수만 뗀다 — horlisun 「(beige, grey, black / 4 colors)」(2026-10-28).
    colors = _NAME_COLOR_COUNT.sub("", inner).strip(" /,·_-")
    if colors and colors != inner and not _NAME_PROMO.search(colors):
        return f"[{colors}]" if m.group(1) is not None else f"({colors})"
    if (_NAME_PROMO.search(inner) or (m.group(1) is not None and _NAME_PROMO_SQ.match(inner))) \
            and not _NAME_KEEP.search(inner):
        return " "
    toks = inner.split()
    t0 = re.sub(r"[-_]", "", toks[0]) if toks else ""
    # 괄호 하나를 통째로 차지한 짧은 품번 — 「(WT315)」(haleine) · 「(W25-SH05)」(wednesday-oasis).
    # 글자가 넷 이하일 때만 — 「(SILVER925)」는 소재다.
    loose = (len(toks) == 1 and len(t0) >= 5 and t0.isascii() and t0.isalnum() and t0.upper() == t0
             and sum(c.isdigit() for c in t0) >= 2 and 2 <= sum(c.isalpha() for c in t0) <= 4)
    lead = len(toks) > 1 and re.fullmatch(r"[A-Z]{2,4}-\d{3,5}", toks[0])        # doucan 「(MRC-5722 PTC YE)」
    if toks and (loose or lead or _name_code(toks[0])):
        rest = " ".join(toks[1:])
        if not rest:
            return " "
        return f"({rest})" if m.group(2) is not None else f"[{rest}]"
    return m.group(0)


def display_name(name: str) -> str:
    """앱 목록 · 검색 · 상세 머리에 보일 이름. 판촉 · 배송 · 차수 · 색 개수 · 셀럽 Pick · 품번 · 이모지를 뗀다."""
    raw = name or ""
    n = _NAME_EMOJI.sub(" ", raw)
    n = _NAME_STARRED.sub(" ", _NAME_BADBLOOD.sub("", n))
    n = _NAME_LOWER_CODE.sub(" ", _NAME_LEAD_CODE.sub(r"\1", n))
    for _ in range(3):
        n2 = _NAME_BRACKET.sub(_name_bracket, n)
        if n2 == n:
            break
        n = n2
    n = _NAME_CODE.sub(lambda m: " " if _name_code(m.group(1)) else m.group(0), n)
    for _ in range(3):
        n2 = _NAME_TAIL.sub("", _NAME_HEAD.sub("", n))
        if n2 == n:
            break
        n = n2
    if n == raw:
        return raw
    n = re.sub(r"\s*\[\s*\]|\s*\(\s*\)", " ", n)
    n = re.sub(r"\s+_", "_", n)                    # 「… set [KNFW04CD03]_Ivory」 → 「… set_Ivory」
    n = re.sub(r"^[\s_/\-|·,:]+|[\s_/\-|·,:]+$", "", re.sub(r"\s+", " ", n)).strip()
    # 다 떼고 색 이름이나 글자 몇 개만 남으면 원래 이름을 둔다 — 이름이 품번뿐인 상품(「19SSPT29 BEIGE」 beyond-closet ·
    # 「SCAT-300」 doffjason)은 품번이 곧 이름이다.
    base = size_from_ocr.color_base(n)
    if (len(re.findall(r"[A-Za-z가-힣]", n)) < 3
            or (len(n.split()) <= 3 and len(re.findall(r"[A-Za-z가-힣]", base or "")) < 3)
            or n.startswith("+") or re.fullmatch(r"[A-Za-z]+\s+[xX×+]\s+[A-Za-z]+", n)):
        return raw
    return n


def thin_row(r: dict, tags: dict, bi: dict, ci: dict, pref: dict) -> dict:
    """목록 한 줄. 열쇠 뜻은 catalog.json 의 "fields" 에 적어 둔다."""
    t = (tags.get(r["source_url"]) or {}).get("tags") or {}
    u = r.get("image_url") or ""
    pre = pref.get(r["brand_slug"], "")
    row = {
        "i": f'{r["brand_slug"]}-{r["product_no"]}',
        "b": bi[r["brand_slug"]],
        "n": display_name(r["name"]),       # 보이는 이름만 씻는다(display_name 주석) — 상품표 name 은 그대로
        # 지금 사는 값. 할인 중이면 할인가다 — 앱이 아직 「op」를 모르는 판에서도 매장 값과 맞게(코덱스 050 리뷰:
        # p 를 정가로 바꾸면 앱이 sp 를 읽기 전까지 할인 상품이 모두 정가로 보인다). 줄 그을 정가는 아래 「op」.
        # 재생성 전 상품표(list_price · sale_price 열 없음)는 예전 값 그대로.
        "p": sell_price(r),
        "m": u[len(pre):] if pre and u.startswith(pre) else u,
        "c": ci[r.get("category_code") or "other"],
        "t": r.get("subtype") or "",
        "g": GENDER_CODE.get(r.get("gender_target"), "U"),
        "s": 1 if r.get("status") == "ON_SALE" else 0,
    }
    # 할인 전 정가 — 할인 중일 때만(사람 2026-10-02 「정가 줄긋고 할인가 표시」). 목록에서 내려간 상품(delisted)은 뺀다 —
    # 마지막으로 본 날의 할인이라 지금도 그런지 알 길이 없다(품절 할인 11,498벌 중 807벌, 표본 페이지는 404). 목록에 있는
    # 품절 상품은 남긴다 — 표본 13벌 모두 페이지가 아직 줄 그은 정가와 할인가를 그대로 보여 준다(2026-10-27).
    if r.get("sale_price") and r.get("list_price") and list_ok(r) and not r.get("delisted"):
        row["op"] = int(r["list_price"])
    # 빈 값은 아예 안 적는다 — 11만 번 반복되면 그것만으로 수백 KB다
    # 판매색 목록의 순서는 대표 사진 색의 근거가 아니다.
    rc = index_color(r, t)
    for k, v in (("co", rc),
                 ("ma", main_material(r, t, (tags.get(r["source_url"]) or {}).get("mat"))),
                 ("se", r.get("season") or ""),
                 ("cg", COLOR_GROUP.get(r["source_url"], 0)),
                 ("dw", (denim_wash(r.get("name") or "", r.get("representative_color") or "", r.get("options") or "", r.get("brand_slug") or "")
                         or dw_photo(r["source_url"]))
                  if r.get("category") in DW_CATS and (r.get("category") == "Denim" or r.get("subtype") == "데님팬츠"
                                                       or (t.get("material") or [""])[0] == "데님") else ""),
                 ("dt", bottom_details(r.get("name") or "", r.get("subtype") or "") if r.get("category") in DT_CATS else []),
                 ("ax", AX_FLAGS.get(f'{r["brand_slug"]}-{r["product_no"]}', 0) & 7),
                 ("pt", pattern_flag(r, t)),
                 ("wn", worn(r)),
                 ("pm", [MOOD_APP.index(k) for k, v in PMOOD.get(f'{r["brand_slug"]}-{r["product_no"]}', {}).items()
                         if v >= 2 and not (r.get("gender_target") == "MENSWEAR" and k in MALE_NO_MOOD)]),
                 ("pw", [MOOD_APP.index(k) for k, v in PMOOD.get(f'{r["brand_slug"]}-{r["product_no"]}', {}).items()
                         if v == 1 and not (r.get("gender_target") == "MENSWEAR" and k in MALE_NO_MOOD)])):
        if v:
            row[k] = v
    st = sorted(style_tags(t), key=lambda x: (TAG_DF[x], x))[:TAG_MAX]
    if st and TAG_ID:
        row["tg"] = [TAG_ID[x] for x in st]
    return row


# ── 스타일 태그 번호 (앱 Discover 추천의 스타일 점수용, 앱 세션 부탁 2026-10-01) ─────────────────
# 점수는 태그를 IDF 가중으로 겹쳐 본다 — 흔한 태그는 거의 안 쓰이니 **드문 것부터** 많아야 여섯. 말은 catalog.tags 에 한 번만,
# 줄에는 번호만(11만 줄이라 글자로 실으면 무겁다). 번호는 흔한 태그일수록 작게 — 자릿수가 줄어 압축 전 크기도 준다.
# 축: 디자인 요소 · 실루엣 · 구성 · 기장 · 무늬(「단색」 뺌) · 둘째 소재(대표 소재는 ma 로 이미 있다).
TAG_AXES = ("design_element", "silhouette", "construction", "length", "pattern")
TAG_MAX = 6
TAG_DF: Counter = Counter()
TAG_ID: dict[str, int] = {}


def style_tags(t: dict) -> list[str]:
    got: list[str] = []
    for ax in TAG_AXES:
        for x in t.get(ax) or []:
            if x and x != "단색" and x not in got:
                got.append(x)
    for x in (t.get("material") or [])[1:2]:
        if x and x not in got:
            got.append(x)
    return got


KNOWN_T: set[str] = set()   # 창고 품목(subtype)에 쓰이는 말 — main 이 채운다(size_entry 주석)


TOP_CATS = {"tops", "outer"}


_SIZE_RANK = {"XXS": 0, "XS": 1, "S": 2, "M": 3, "L": 4, "XL": 5, "XXL": 6, "2XL": 6, "XXXL": 7, "3XL": 7}


def _name_rank(n) -> float | None:
    m = re.match(r"\s*(XXXL|XXL|XXS|XS|XL|[23]XL|[SML])\b", str(n or ""), re.I)
    if m:
        return _SIZE_RANK[m.group(1).upper()]
    m = re.match(r"\s*(\d{1,3})(?!\d)", str(n or ""))
    return float(m.group(1)) if m else None


def _tidy_rows(d: dict) -> dict:
    """값이 다 빈 줄은 뺀다(앱은 그 줄을 건너뛰어 이름 수가 어긋난다). 큰 사이즈부터 적은 표(roem 「L · M · S」)는 뒤집는다."""
    sz = d.get("sizes") or {}
    axes = {**sz, **{f"range:{k}": v for k, v in (d.get("ranges") or {}).items()}}
    if not axes:
        return d
    n = max(len(v) for v in axes.values())
    par = [k for k in ("size_names", "size_keys") if isinstance(d.get(k), list) and len(d[k]) == n]
    keep = [i for i in range(n) if any(i < len(v) and v[i] is not None for v in axes.values())]
    if 0 < len(keep) < n:
        d["sizes"] = {a: [v[i] for i in keep if i < len(v)] for a, v in sz.items()}
        if isinstance(d.get("ranges"), dict):
            d["ranges"] = {a: [v[i] for i in keep if i < len(v)] for a, v in d["ranges"].items()}
        for k in par:
            d[k] = [d[k][i] for i in keep]
        n = len(keep)
    rk = [_name_rank(x) for x in d.get("size_names") or []]
    if n > 1 and len(rk) == n and None not in rk and all(rk[i] > rk[i + 1] for i in range(n - 1)):
        d["sizes"] = {a: list(reversed(v)) if len(v) == n else v for a, v in d["sizes"].items()}
        if isinstance(d.get("ranges"), dict):
            d["ranges"] = {a: list(reversed(v)) if len(v) == n else v for a, v in d["ranges"].items()}
        for k in par:
            d[k] = list(reversed(d[k]))
    return d


def _load_size_check() -> dict[tuple[str, str], list[tuple[str, str, str]]]:
    """data/size_check.csv — 원본부터 틀린 칸(브랜드,상품번호,부위,항목,사이즈,왜). 고치지 않고 「확인 필요」만 붙인다."""
    p = DATA / "size_check.csv"
    out: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    if p.exists():
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            out[(r["브랜드"].strip(), r["상품번호"].strip())].append(
                ((r.get("부위") or "").strip(), (r.get("항목") or "").strip(), (r.get("사이즈") or "").strip()))
    return out


SIZE_CHECK = _load_size_check()
_RANGES = json.loads((DATA / "size_labels.json").read_text(encoding="utf-8")).get("_ranges_cm", {})


# 단면으로 적는 둘레 부위의 단면 상한(size_labels.json _ranges_cm) — 이 값을 넘으면 둘레로 적힌 줄이다
_GIRTHY = {k: _RANGES[k][1] for k in ("가슴", "허리", "엉덩이", "허벅지") if k in _RANGES}


def _size_rank(name: str):
    try:
        from size_from_ocr import _opt_rank
    except ImportError:
        return None
    return _opt_rank(name)


def _ascending(vs: list, tol: float = 1.0) -> bool:
    return all(b >= a - tol for a, b in zip(vs, vs[1:]))


def size_chk(tab: dict, explicit: list[tuple[str, str]]) -> dict[str, list[int]]:
    """「확인 필요」 칸 — {항목: [사이즈 자리]}, 이름이 겹치면 「_names」. 값은 그대로 두고 앱이 흐리게 보여 준다(앱 QA 2026-10-25).

    저절로 붙는 것: 같은 줄 다른 값의 다섯 배 넘게 어긋나고 라벨 범위에서도 크게 벗어난 값(소수점 빠진 921 · 1224 · 437),
    겹친 사이즈 이름(S · M · M). 나머지는 data/size_check.csv 에 적는다(빈 칸 「46m」처럼 숫자가 아닌 원본)."""
    out: dict[str, set[int]] = defaultdict(set)
    names = tab.get("size_names") or []
    for i, n in enumerate(names):
        if n in names[:i]:
            out["_names"].add(i)
    for lab, vs in (tab.get("sizes") or {}).items():
        lo, hi = _RANGES.get(lab, (None, None))
        if lo is None:
            continue
        for i, v in enumerate(vs):
            o = [x for j, x in enumerate(vs) if j != i and x is not None]
            if v is None or not o:
                continue
            m = sorted(o)[len(o) // 2]
            if m > 0 and (v >= 5 * m or v <= m / 5) and (v > 3 * hi or v < lo / 2):
                out[lab].add(i)
    # 단면 라벨에 둘레 크기 값 — 사람 표는 원문 수치를 그대로 두므로(bmuette 「가슴 115 · 120 · 125」 가슴둘레 원문,
    # roem 「허리둘레 69.5 … 82.5」, hyein-seo 「허리 74 · 79」) 앱은 단면 칸에 두 배 값을 보게 된다. 한 칸이라도 단면 상한
    # (size_labels 범위)을 넘으면 그 줄은 둘레로 적힌 줄이다 — 줄 전체를 흐리게 한다(2026-10-27 코덱스 sizes.impossible).
    for lab, cap in _GIRTHY.items():
        vs = (tab.get("sizes") or {}).get(lab) or []
        if any(v is not None and v > cap for v in vs):
            out[lab].update(i for i, v in enumerate(vs) if v is not None)
    # 크기 차례를 거스르는 칸 — 기계 표는 size_from_ocr.drop_reversed_labels 가 줄째 빼지만 사람 표는 원문 그대로다
    # (juntae-kim 298 가슴 「52 · 54 · 56 · 58 · 41」 — 매장 그림의 XL 칸이 아랫줄 값으로 밀려 있다).
    # 그 칸 하나만 빼면 차례가 맞으면 그 칸을, 아니면 어긋난 두 칸을 함께 흐리게 한다. 값은 그대로 둔다.
    rk = [_size_rank(str(x)) for x in names]
    if len(rk) >= 2 and all(r is not None for r in rk) and rk == sorted(rk) and len(set(rk)) == len(rk):
        for lab, vs in (tab.get("sizes") or {}).items():
            pts = [(i, v) for i, v in enumerate(vs[:len(names)]) if isinstance(v, (int, float)) and i not in out.get(lab, ())]
            bad = [(a, b) for (a, x), (b, y) in zip(pts, pts[1:]) if y < x - 1]
            if not bad:
                continue
            one = [i for i, _ in pts if _ascending([v for j, v in pts if j != i])]
            out[lab].update(one or [i for ab in bad for i in ab])
    for lab, n in explicit:
        i = names.index(n) if n in names else (int(n) - 1 if n.isdigit() and 0 < int(n) <= len(names) else None)
        if i is not None:
            out["_names" if lab == "이름" else lab].add(i)
    return {k: sorted(v) for k, v in out.items()}


def _girth_chk(tab: dict, girth: list | None, chk: dict) -> dict:
    """수기 항목이 「…둘레」라고 적은 줄(size_from_ocr.load_manual 의 girth) — 값이 단면 크기로 작으면(단면 상한의 60% 이하,
    ava-molli 1425 「가슴둘레 49.5」처럼 매장이 단면 값에 둘레라 적은 것) 그대로 두고, 아니면 줄 전체를 「확인 필요」로."""
    for lab in girth or []:
        vs = (tab.get("sizes") or {}).get(lab) or []
        nums = [v for v in vs if isinstance(v, (int, float))]
        hi = _RANGES.get(lab, (None, None))[1]
        if nums and hi and max(nums) > 0.6 * hi:
            chk[lab] = sorted(set(chk.get(lab, [])) | {i for i, v in enumerate(vs) if isinstance(v, (int, float))})
    return chk


def size_entry(e: dict | None, known_t: set[str] | None = None, row: dict | None = None) -> dict | None:
    """상품 하나의 사이즈를 앱 모양으로. 세트는 size_parts 로 하의 표를 따로 싣는다.

    part 값은 「하의」, 또는 수기 구성품 탭 이름(슬리브리스 · 볼레로)이다 — 기본 size 가 상의(첫 구성품)다.
    기계가 읽은 표의 다른 말은 앱이 옷장 카테고리와 짝을 못 맞추니 여기서 한 번 더 거른다.
    chk 는 「확인 필요」 칸(size_chk).
    t(품목)는 상품 품목(subtype)에 실제로 쓰이는 말일 때만 싣는다 — 앱이 이 말로 긴바지·반바지
    핏 기준을 가르는데(없으면 긴바지), 창고에 없는 말(「쇼츠」 — 창고 말은 「숏팬츠」)은 짝이 안 맞는다.
    """
    if not e:
        return e
    parts = []
    for p in e.get("size_parts") or []:
        # 「하의」 말고는 수기의 구성품 탭(슬리브리스 · 볼레로)만 — 부위 이름이 그대로 탭 이름이다(앱 QA 2026-10-25)
        if not (p.get("sizes") or p.get("ranges")) or not p.get("part") or (p["part"] != "하의" and e.get("source") != "manual"):
            continue
        if p.get("t") and known_t is not None and p["t"] not in known_t:
            print(f"   size_parts 의 품목 {p['t']!r} 는 창고 품목에 없는 말이라 뺀다")
            p = {k: v for k, v in p.items() if k != "t"}
        parts.append(p)
    # 앱이 안 읽는 내부 칸(출처 · 브랜드 · 사이즈 대조)은 공개 게시본에 안 싣는다(2026-10-26). 형제 출처 주소(sibling_of)도
    # 1,218벌에 새어 나가고 있었다 — 앱 코드는 읽지 않는다(2026-10-28). girth 는 수기 둘레 표시(아래 _girth_chk 가 쓴다).
    out = {k: v for k, v in e.items() if k not in ("size_parts", "t", "dimension_text", "dimension_table",
                                                   "source", "brand_slug", "size_keys", "size_match", "girth",
                                                   "sibling_of")}
    # 방향을 모르는 잡화 치수(「37.5x15.5x26」 → 치수 1 · 2 · 3)는 앱 표 머리가 못 된다 — 창고에만 둔다
    if out.get("sizes") and any(k.startswith("치수 ") for k in out["sizes"]):
        out["sizes"] = {k: v for k, v in out["sizes"].items() if not k.startswith("치수 ")}
        if not out["sizes"]:
            return None
    # 수기는 원문 순서·행·축을 그대로 낸다. 하의 표에는 source가 없으므로
    # 부모의 출처를 기준으로 함께 보호한다.
    if e.get("source") != "manual":
        out = _tidy_rows(out)
        parts = [_tidy_rows(dict(p)) for p in parts]
    flags = SIZE_CHECK.get(((row or {}).get("brand_slug"), str((row or {}).get("product_no"))), [])
    c = _girth_chk(out, e.get("girth"), size_chk(out, [(lab, n) for part, lab, n in flags if not part]))
    if c:
        out["chk"] = c
    for i, p in enumerate(parts):
        g = p.get("girth")
        p = parts[i] = {k: v for k, v in p.items() if k != "girth"}
        c = _girth_chk(p, g, size_chk(p, [(lab, n) for part, lab, n in flags if part == p["part"]]))
        if c:
            p["chk"] = c
    if parts:
        out["size_parts"] = parts
        # 세트는 기본 표(상의)에도 품목을 싣는다 — 상품 품목이 하의로 들어온 세트(ronron-7966 「스커트」)는
        # 앱이 상의 표를 어느 핏 묶음과 견줄지 모른다(앱 세션 2026-09-25). 사람이 적은 상의 품목이 먼저,
        # 없으면 상품 품목이 상의·아우터일 때 그것(forcesensitive 셋업 「맨투맨」). 세트가 아니면 안 싣는다.
        t = e.get("t") or ((row or {}).get("subtype") if (row or {}).get("category_code") in TOP_CATS else None)
        if t and (known_t is None or t in known_t):
            out["t"] = t
    return out


# 앱 「이 옷 읽기」는 부위가 빈칸 · 「겉감」 · 「겉감 1」인 묶음만 겉감으로 읽는다(layer-web docs/garment-reading.md §5).
# 그 규칙에 걸려 겉감이 아닌 것이 겉감으로 읽히거나, 겉감이 둘로 읽히는 꼴을 여기서 정리한다(2026-10-09 판매중 의류 전수):
#   · 빈칸 묶음이 겉감 묶음과 값까지 같다(asp 「"" = 겉감」 중복) → 빈칸 묶음을 뺀다(안감과 같은 값은 흔하니 그대로)
#   · 빈칸 묶음이 「겉감 2 · 3」과 함께 있고 「겉감 · 겉감 1」은 없다(eastlogue · grove 171벌) → 빈칸이 「겉감 1」이다
#   · 빈칸 묶음이 「겉감 · 겉감 1」과 함께 있다(andersson-bell 퍼 봄버) → 어느 쪽이 몸판인지 모른다 — 빈칸을 「부분」으로
#   · 빈칸 묶음이 충전 섬유뿐이다(diafvine 「다운 80 · 깃털 20」) → 「충전재」
_FILL_FIBERS = {"다운", "덕다운", "구스다운", "깃털", "페더", "웰론", "솜", "오리털", "거위털", "신슐레이트", "프리마로프트"}


def tidy_mat_parts(mat: list) -> list:
    if not isinstance(mat, list) or not mat:
        return mat
    groups = [dict(g) for g in mat if isinstance(g, dict)]
    names = [(g.get("p") or "") for g in groups]
    out = []
    for g, p in zip(groups, names):
        if p == "":
            # 겉감 쪽 묶음과 같을 때만 중복이다 — 겉감 · 안감이 둘 다 「폴리에스터 100」인 옷은 흔하다(209벌)
            same = [q for q, pn in zip(groups, names) if pn.startswith("겉감") and q.get("v") == g.get("v")]
            if same:
                continue
            fibers = {str(f) for f, *_ in (g.get("v") or []) if f}
            if fibers and fibers <= _FILL_FIBERS:
                g["p"] = "충전재"
            elif any(pn in ("겉감", "겉감 1") for pn in names):
                g["p"] = "부분"
            elif any(pn.startswith("겉감 ") for pn in names):
                g["p"] = "겉감 1"
        out.append(g)
    return out


def gallery_for_app(r: dict, d: dict) -> list[str]:
    """현재 저장본의 견본도 걷는다. extra/small은 실제 큰 사진이므로 남긴다."""
    rejected = set(d.get("gallery_excluded") or [])
    out = []
    for u in d.get("gallery") or []:
        parts = urlsplit(u)
        chip = (parts.hostname in {"roughside.co.kr", "www.roughside.co.kr"}
                and "/web/product/small/" in parts.path)
        if u not in rejected and not chip and u not in out:
            out.append(u)
    return out


def full(r: dict, tags: dict, sizes: dict, crawl: dict,
         upref: dict | None = None) -> dict:
    """상세 한 벌 — 얇은 목록에 없는 것 전부."""
    d = crawl.get(r["source_url"]) or {}
    out = {"id": f'{r["brand_slug"]}-{r["product_no"]}'}
    # 「공식몰 보기」 주소. **목록이 아니라 여기** 둔다 — 목록 11만 줄에 실으면 첫 화면이
    # 1.4MB 무거워지는데(앱 실측: 4.70 → 6.07MB · 홈 2,143 → 2,858ms), 주소를 쓰는 것은
    # 사람이 실제로 여는 몇 벌뿐이고 그때는 어차피 이 조각을 받는다(앱 쪽 제안 2026-09-20).
    # 매장마다 공통 앞머리를 떼어 catalog.brands[].up 에 한 번만 적는 것은 그대로다.
    su = r.get("source_url") or ""
    upre = (upref or {}).get(r["brand_slug"], "")
    if su:
        # 앞머리는 언제나 주소보다 짧다(img_prefix 가 그렇게 만든다) — 꼬리가 빌 일은 없다.
        out["u"] = su[len(upre):] if upre and su.startswith(upre) else su
    # 혼용률. 소재 칸(`tags.material`)은 표준화된 이름(「코튼」)뿐이라 70/30 이 없다.
    # 설명글이 유일한 자리였다 — 칸으로 옮긴다(앱 쪽 지적 2026-09-20). 전수 9.7% · 12,327벌.
    # **부위 구조를 뭉개지 않는다.** 스펙표에 어떻게 세울지는 앱이 정한다.
    # 2026-10-02 부터는 tag_items 가 설명 · 스펙 · 사이즈가이드 창 · 브라우저 글 · 그림 글을 다 보고 만든 것을 옮긴다
    # (tag_items.blend_of_brand 주석 — 판매중 의류 13.6% → 72.2%). 새 판 태거를 거친 상품은 못 읽었어도 "mat": [] 가 실려 있다 —
    # 그것을 설명글 해석으로 되살리면 안 된다(새 판이 **버린** 것: 어긋나는 출처 · 베껴 붙인 매장 글 · 겉감 없는 안감만).
    # 열쇠가 아예 없는 상품(옛 판 태그 파일)만 예전처럼 설명글에서 읽는다.
    te = tags.get(r["source_url"]) or {}
    if "mat" in te:
        mat = te["mat"] or []
    else:
        mat = product_desc.blend(d.get("description") or "")
    if mat:
        out["mat"] = tidy_mat_parts(mat)
    out.update({
        "tags": fold_finish((tags.get(r["source_url"]) or {}).get("tags") or {}),
        "size": size_entry(sizes.get(r["source_url"]), KNOWN_T, r),
        "gallery": gallery_for_app(r, d),
        # 설명문을 내보낸다(2026-09-20 사람 결정: 「지금 당장 메울 곳은 메워, 배송이나
        # 세탁/후기 문의 같은 찌꺼기들은 싹 지우고」). 2026-09-07 에 안 내보내기로 한 까닭이
        # 사라졌다 — 그때는 씻는 자리가 없어 남의 말이 상품 설명 자리에 떴다:
        #   siyazu 783벌  손님 후기 통째로 —「커서 스몰사이즈로 교환했는데도 크네요」
        #   vunque·kirsh·blayer·andersson-bell  후기 신고 안내문
        #   divein   「Q & A Write View all 상품명 … 판매가 109,000원」
        #   dnsr     「RELATED ITEMS 원턱 버뮤다 데님 팬츠 블루 KRW 74,000 …」
        #   badblood 「Delivery / Returns * Estimated delivery dates …」
        # 이제 product_desc 가 그것들을 걷어내고, 매장 글이 아무 말도 안 하면 그림에서
        # 읽어 둔 글로 메운다. --with-desc 는 씻기 전 원문까지 보고 싶을 때만 쓴다.
        # 설명은 브랜드 조각에 안 넣는다 — descs/ 로 따로 나간다(아래 write_descs).
        # 상세 화면은 브랜드 조각만 받고, 설명을 펼칠 때 그 조각을 받는다(사람 결정
        # 2026-09-20). 설명을 같이 넣으면 브랜드 조각이 평균 1.2MB · 최대 5.9MB 가 된다.
        **({"desc_raw": (d.get("description") or "")[:4000]} if WITH_DESC else {}),
        "color_name": r.get("representative_color") or "",
        "price_log": d.get("price_log") or [],
        # 할인가 자국 [[날짜, 할인가 또는 0(할인 없음)], …] — 바뀔 때만 한 줄(crawl_cafe24.carry_over)
        "sale_log": d.get("sale_log") or [],
        "price_seen_at": d.get("price_seen_at") or "",
    })
    return out


# 미리 압축하지 않는다 — **평문 JSON 으로 올린다.**
#
# 처음엔 `.json.gz` 로 올리고 Content-Encoding: gzip 을 붙이려 했다. 슈퍼베이스가 올린
# 그대로 내보낸다고 들어서다. 실제로 올려 되받아 보니 둘 다 틀렸다:
#
#   · 슈퍼베이스는 그 머리말을 **안 붙여 준다** (content-encoding 이 아예 없다)
#   · 그런데 앞에 **클라우드플레어**가 있고, 그쪽이 알아서 압축해 준다
#         --compressed 로 부르면 content-encoding: br 로 온다
#
# 그래서 이미 gzip 인 것을 brotli 로 한 번 더 감싸는 꼴이 됐다 — 728,949 → 728,959 바이트,
# 열 바이트가 **늘었다**. 두 번 압축하면 아무것도 안 줄어든다.
#
# 평문으로 올리면 클라우드플레어가 brotli 로 줄이는데 그게 gzip 보다 낫고, 머리말 문제도
# 사라지고, 앱은 그냥 `.json` 을 받으면 된다.
#
# 여기서 세는 크기는 **평문 크기**다. 실제로 오가는 값은 그보다 작다(brotli 가 줄인다).
GZ = False


def write(path: Path, obj, dry: bool) -> int:
    """쓴 바이트 수를 돌려준다 — **압축한 뒤**의 크기다(그게 실제로 오가는 값이다)."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    b = gzip.compress(raw, 6) if GZ else raw
    if not dry:
        out = path.with_suffix(path.suffix + ".gz") if GZ else path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b)
    return len(b)


def build_descs(rows: list[dict], crawl: dict, mined: dict, tags: dict | None = None, stats: dict | None = None) -> dict:
    """저장 원문에서 앱 설명을 만든다. 파일 쓰기 없이 회귀 비교에도 같은 경로를 쓴다.
    stats 를 주면 매장 단위 거르기마다 뺀 수를 적어 준다(전후 비교용).
    tags 를 주면 설명 맨 앞 혼용률 줄을 태거가 정한 혼용률(mat — 상세 조각에 나가는 그 값)에 맞추고, 매장 견본 혼용률을
    걷는다(product_desc.head_of · store_templates 주석, qa27 2026-10-27). 태그 파일에 mat 열쇠가 없는 상품은 예전 그대로."""
    stats = stats if stats is not None else {}
    desc_of: dict[str, dict[str, dict]] = defaultdict(dict)
    back_of: dict[str, tuple] = {}      # 되풀이 줄을 빼고 비면 다시 채울 재료
    mat_of: dict[str, list | None] = {}
    for r in rows:
        te = (tags or {}).get(r["source_url"]) or {}
        mat_of[f'{r["brand_slug"]}-{r["product_no"]}'] = tidy_mat_parts(te.get("mat") or []) if "mat" in te else None
    by_store: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_store[r["brand_slug"]].append(r)
    acc_of = {f'{r["brand_slug"]}-{r["product_no"]}': r.get("category_code") in size_from_ocr.NON_APPAREL_CODES for r in rows}
    cat_of = {f'{r["brand_slug"]}-{r["product_no"]}': r.get("category_code") or "" for r in rows}
    # 그림 글의 영문 토막이 진짜 낱말인지 — 매장 글 전체에 쓰이는 낱말로 가린다(product_desc.ocr_noise_line 주석)
    product_desc.set_ocr_vocab((crawl.get(r["source_url"]) or {}).get("description") or "" for r in rows)
    # 매장 틀 혼용률(product_desc.blend_templates 주석) — 원문에서 덩이째 지운 뒤 설명을 씻는다(그래야 머리줄도 남의 것을 안 읽는다)
    blend_keys = {slug: product_desc.blend_templates([((crawl.get(r["source_url"]) or {}).get("description") or "",
                                                        acc_of[f'{slug}-{r["product_no"]}']) for r in rs])
                  for slug, rs in by_store.items()}
    # 같은 매장 상품 이름 — 값을 단 추천 목록을 이름째 지우고(strip_listed), 이름만 남은 줄을 뺀다(drop_name_lines)
    name_idx = {slug: product_desc.name_index([r.get("name") for r in rs] + [display_name(r.get("name") or "") for r in rs])
                for slug, rs in by_store.items()}
    stats.update(blend_template=0, boilerplate_products=0, boilerplate_sentences=0, other_name_lines=0, garment_on_acc=0)
    for r in rows:
        d = crawl.get(r["source_url"]) or {}
        pid = f'{r["brand_slug"]}-{r["product_no"]}'
        raw = d.get("description") or ""
        raw2 = product_desc.drop_blend_template(raw, blend_keys.get(r["brand_slug"]) or set(), acc_of[pid])
        if raw2 != raw:
            stats["blend_template"] += 1
        txt, src = product_desc.best(raw2,
                                     mined.get((r["brand_slug"], str(r["product_no"]))),
                                     acc=acc_of[pid],
                                     spec=d.get("spec"), mat=mat_of[pid], names=name_idx.get(r["brand_slug"]))
        if txt and src == "shop" and acc_of[pid] and product_desc.describes_garment(txt, r.get("name") or ""):
            stats["garment_on_acc"] += 1          # 잡화에 옷 한 벌의 설명(low-classic 양말에 봄버 자켓) — 틀린 글보다 빈 칸
            # 본문이 남의 옷이어도 간략설명에는 이 잡화의 특징이 있을 수 있다(2026-10-28).
            spec = {key: value for key, value in (d.get("spec") or {}).items()
                    if key in product_desc.SPEC_DESC_KEYS
                    and not product_desc.describes_garment(product_desc.clean(str(value), True), r.get("name") or "")}
            txt, src = product_desc.best("", mined.get((r["brand_slug"], str(r["product_no"]))),
                                         acc=True, spec=spec, mat=mat_of[pid], names=name_idx.get(r["brand_slug"]))
        if txt:
            desc_of[r["brand_slug"]][pid] = {"t": txt, "s": src}
            back_of[pid] = (d, mined.get((r["brand_slug"], str(r["product_no"]))), acc_of[pid])
    # 매장이 설명마다 되풀이하는 줄은 뺀다(product_desc.store_repeats 주석). 매장 글과 그림 글을 따로 센다 —
    # 그림 글은 벌 수가 적어(포스센스티브 13) 문턱을 5벌로 둔다.
    # 옷과 잡화를 따로 센다 — 가방에만 붙는 가죽 관리 안내(margesherwood 157벌)는 매장 전체로 세면 40%를 못 넘는다.
    dropped_rep = 0
    name_only = 0
    tpl_n: Counter = Counter()
    name_of = {f'{r["brand_slug"]}-{r["product_no"]}': r.get("name") or "" for r in rows}
    for slug, m in desc_of.items():
        repeats, boiler = {}, {}
        for (src_kind, min_n), acc in ((k, a) for k in (("shop", 15), ("ocr", 5)) for a in (False, True)):
            keys = [k for k, v in m.items() if v["s"] == src_kind and acc_of.get(k, False) == acc]
            rep = product_desc.store_repeats([m[k]["t"] for k in keys], min_n)
            repeats[src_kind, acc] = rep
            for k in keys:
                t2 = product_desc.drop_lines(m[k]["t"], rep)
                if t2 != m[k]["t"]:
                    dropped_rep += 1
                    m[k]["t"] = t2
        # 매장 상용구 문장(product_desc.store_boilerplate 주석) — 안내 낱말이 없어도 매장 설명 20%(20편) 넘게 · 두 갈래 넘게 서면 뺀다
        for src_kind in ("shop", "ocr"):
            keys = [k for k, v in m.items() if v["s"] == src_kind]
            bp = product_desc.store_boilerplate([(m[k]["t"], cat_of.get(k, "")) for k in keys])
            boiler[src_kind] = bp
            for k in keys:
                t2, n = product_desc.drop_boilerplate(m[k]["t"], bp)
                if n:
                    stats["boilerplate_products"] += 1
                    stats["boilerplate_sentences"] += n
                    m[k]["t"] = t2
        # 같은 매장 다른 상품의 이름뿐인 줄(추천 목록 머리를 뗀 뒤 남은 이름)
        store_names = {product_desc._boiler_key(x) for r in by_store.get(slug, ())
                       for x in (r.get("name"), display_name(r.get("name") or "")) if x}
        for k, v in m.items():
            own = {product_desc._boiler_key(name_of.get(k, "")), product_desc._boiler_key(display_name(name_of.get(k, "")))}
            t2 = product_desc.drop_name_lines(v["t"], store_names - own, own)
            if t2 != v["t"]:
                stats["other_name_lines"] += 1
                v["t"] = t2
        # 설명이 상품 이름 한 줄뿐이면 비운다 — 앱은 이름을 상세 맨 위에 크게 쓴다(앱 세션 2026-09-24).
        # 다른 글 사이에 섞인 이름 줄은 앱이 뺀다. 비운 것은 아래에서 스펙·그림 글로 채울 수 있으면 채운다.
        for k, v in m.items():
            if v["s"] == "shop" and (product_desc.is_name_only(v["t"], name_of.get(k, ""))
                                     or product_desc.is_name_only(v["t"], display_name(name_of.get(k, "")))):
                v["t"] = ""
                name_only += 1
        # 매장 견본 혼용률(다른 상품의 소재 블록) — mat 과 어긋나는 것만 걷는다. 비면 아래에서 스펙 · 그림 글로 채운다.
        tpl = product_desc.store_templates([(v["t"], mat_of.get(k)) for k, v in m.items()])
        if tpl:
            for k, v in m.items():
                t2 = product_desc.drop_templates(v["t"], mat_of.get(k), tpl, name_of.get(k, ""))
                if t2 != v["t"]:
                    v["t"] = t2
                    tpl_n[slug] += 1
        # 매장 글이 안내문뿐이었던 상품은 비었다 — 스펙의 간략설명, 그다음 그림 글로 채운다
        # (넘버링 76벌: 설명 칸은 후기 위젯·관리 안내뿐이고 「상품간략설명」에 진짜 설명이 있다).
        for k in [k for k, v in m.items() if not v["t"]]:
            d, mk, acc = back_of[k]
            own = {product_desc._boiler_key(name_of.get(k, "")), product_desc._boiler_key(display_name(name_of.get(k, "")))}

            def filtered_fallback(t, source):
                # 간략설명·그림 글에도 같은 틀이 있다. 대체 출처로 메우며 방금 뺀 상용구를 도로 넣지 않는다(2026-10-28).
                t = product_desc.drop_lines(t, repeats.get((source, acc), set()))
                t, _ = product_desc.drop_boilerplate(t, boiler.get(source, set()))
                t = product_desc.drop_name_lines(t, store_names - own, own)
                if acc and product_desc.describes_garment(t, name_of.get(k, "")):
                    return ""
                return t

            t = product_desc.clean("\n".join(str(d.get("spec", {}).get(x)) for x in product_desc.SPEC_DESC_KEYS
                                             if (d.get("spec") or {}).get(x)), acc, mat=mat_of.get(k), names=name_idx.get(slug))
            t = filtered_fallback(t, "shop")
            if t:
                m[k] = {"t": t, "s": "shop"}
                continue
            t = filtered_fallback(product_desc.from_mined(mk, mat_of.get(k)), "ocr")
            if t:
                m[k] = {"t": t, "s": "ocr"}
            else:
                del m[k]
    # 간략설명 · 그림 글로 메운 설명도 상품 이름 한 줄뿐이면 뺀다(wednesday-oasis 「워시드 스트레이트 데님 팬츠_BLUE(WCB6PT02)」)
    for slug, m in desc_of.items():
        for k in [k for k, v in m.items() if v["s"] == "shop" and (product_desc.is_name_only(v["t"], name_of.get(k, ""))
                  or product_desc.is_name_only(v["t"], display_name(name_of.get(k, ""))))]:
            del m[k]
            name_only += 1
    # 매장 글은 특징(f)과 안내(n)로 나눠 싣는다 — 앱은 있으면 그대로 그리고, 없으면 t 를 제 규칙으로 나눈다.
    # 그림 글(s="ocr")은 나누지 않는다 — 앱이 통으로 보여 준다(앱 세션 2026-09-24).
    for m in desc_of.values():
        for v in m.values():
            if v["s"] == "shop":
                f, n = product_desc.split_fn(v["t"])
                if f:
                    v["f"] = f
                if n:
                    v["n"] = n
                # f·n 이 붙은 줄은 t 를 뺀다 — 같은 글을 두 번 실어 설명 조각이 50 → 96MB 가 됐다. 앱은 t 가 없으면
                # f·n 을 이어 받는다(layer-web 9e2a4cc 「row?.t ?? [...f, ...n]」). **그 앱이 마스터에 오른 뒤에만**
                # 이렇게 한다 — t 만 읽는 앱에 t 를 빼면 설명이 다 사라진다(앱 세션 2026-09-24). 그림 글은 t 가 유일한 글이라 둔다.
                if f or n:
                    del v["t"]
    print(f"설명에서 매장 되풀이 줄을 뺀 상품 {dropped_rep} · 이름 한 줄뿐이라 비운 설명 {name_only} · "
          f"매장 상용구 문장 {stats['boilerplate_sentences']}개({stats['boilerplate_products']}편) · "
          f"매장 틀 혼용률 {stats['blend_template']}편 · 다른 상품 이름 줄 {stats['other_name_lines']}편 · "
          f"잡화에 옷 설명 {stats['garment_on_acc']}편 · "
          f"견본 혼용률을 걷은 설명 {sum(tpl_n.values())}"
          + (" (" + ", ".join(f"{s} {n}" for s, n in tpl_n.most_common(6)) + ")" if tpl_n else ""))
    return desc_of


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry", action="store_true", help="쓰지 않고 크기만 잰다")
    ap.add_argument("--with-desc", action="store_true",
                    help="매장 설명문도 내보낸다(31MB 늘어난다. full() 의 주석을 먼저 읽어라)")
    args = ap.parse_args()
    global WITH_DESC
    WITH_DESC = args.with_desc
    out = Path(args.out)

    rows = list(csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig")))
    # 걷고 있지만 아직 앱에 안 내보내는 매장(platforms.APP_HOLD) — 설명·소재·실측이 덜 찬 채로
    # 앱에 서면 「빈 칸 많은 매장」으로 먼저 보인다. 채우고 나서 목록에서 지운다.
    import platforms
    held = sum(1 for r in rows if r["brand_slug"] in platforms.APP_HOLD)
    rows = [r for r in rows if r["brand_slug"] not in platforms.APP_HOLD]
    if held:
        print(f"아직 안 내보내는 매장 {sorted(platforms.APP_HOLD)} — {held}벌 뺌")
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    # 검수에서 확인한 대표색(color_fix.csv)이 태그 색보다 앞선다 — 앱 대표색(co)은 태그 색 첫 칸이다
    cfx = DATA / "color_fix.csv"
    if cfx.exists():
        fix = {(x["브랜드"], x["상품번호"]): x["색"] for x in csv.DictReader(cfx.open(encoding="utf-8-sig")) if x.get("색")}
        n_cf = 0
        for r in rows:
            c = fix.get((r["brand_slug"], str(r["product_no"])))
            if c:
                e = tags.get(r["source_url"]) or {}
                tags[r["source_url"]] = e
                tg = e.get("tags") or {}
                e["tags"] = tg
                tg["color"] = [c] + [x for x in tg.get("color") or [] if x != c]
                n_cf += 1
        print(f"대표색 고침(color_fix) {n_cf}벌")
    gfx = DATA / "gender_fix.csv"
    if gfx.exists():
        gm = {"W": "WOMENSWEAR", "M": "MENSWEAR", "U": "UNISEX"}
        gf = {(x["브랜드"], x["상품번호"]): gm.get(x["성별"], x["성별"])
              for x in csv.DictReader(gfx.open(encoding="utf-8-sig")) if x.get("성별")}
        n_gf = 0
        for r in rows:
            g = gf.get((r["brand_slug"], str(r["product_no"])))
            if g and r.get("gender_target") != g:
                r["gender_target"] = g
                n_gf += 1
        print(f"성별 고침(gender_fix) {n_gf}벌")
    n_mat = sum(1 for e in tags.values() if "mat" in (e or {}))
    print(f"혼용률(mat): 태그 파일이 정한 상품 {n_mat:,} / {len(tags):,} (나머지는 설명글에서)")
    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    for r in rows:
        TAG_DF.update(style_tags((tags.get(r["source_url"]) or {}).get("tags") or {}))
    tag_list = [x for x, _ in sorted(TAG_DF.items(), key=lambda kv: (-kv[1], kv[0]))]
    TAG_ID.update({x: i for i, x in enumerate(tag_list)})
    print(f"스타일 태그 사전 {len(tag_list)}개 — 흔한 것: {', '.join(tag_list[:8])}")
    KNOWN_T.update(r.get("subtype") for r in rows if r.get("subtype"))
    crawl = {}
    for p in sorted((DATA / "crawl").glob("*.jsonl")):
        if p.name.startswith("_"):
            continue
        for ln in p.open(encoding="utf-8"):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if d.get("source_url"):
                crawl[d["source_url"]] = d

    for p in sorted((DATA / "crawl" / "detail").glob("*.jsonl")):
        for ln in p.open(encoding="utf-8"):
            try:
                m = json.loads(ln)
            except Exception:
                continue
            MINED[(m.get("brand_slug") or p.stem, str(m.get("product_no")))] = m
    print(f"그림에서 읽어 둔 상품 글 {len(MINED):,}벌")

    # 색만 다른 형제 묶기 — 둘 이상 모인 묶음에만 번호를 준다(단독은 0)
    fam: dict[tuple, list[str]] = defaultdict(list)
    for r in rows:
        base = size_from_ocr.color_base(r["name"])
        if base:
            fam[(r["brand_slug"], base)].append(r["source_url"])
    gid = 0
    for k, urls in sorted(fam.items()):
        if len(urls) < 2:
            continue
        gid += 1
        for u in urls:
            COLOR_GROUP[u] = gid
    print(f"색만 다른 형제 묶음 {gid}개 · 묶인 상품 {len(COLOR_GROUP)}벌")
    ax_path = DATA / "similar_axes.json"
    axes = json.loads(ax_path.read_text(encoding="utf-8")) if ax_path.exists() else {}
    ax_ok_ids = {f'{r["brand_slug"]}-{r["product_no"]}' for r in rows}
    for pid, v in (axes.get("items") or {}).items():
        # 실제로 실리는 기준만 비트를 켠다 — 문턱 · 앱에 나가는 이웃으로 거른 뒤 AX_MIN 벌 이상(아래 조각 쓰기와 같은 규칙)
        aidx_ = axes.get("index") or []
        f = 0
        for key, bit in (("cp", 1), ("s", 2), ("m", 4)):
            if len(ax_pairs(v.get(key) or [], aidx_, ax_ok_ids)) >= AX_MIN:
                f |= bit
        if f:
            AX_FLAGS[pid] = f
    # 무늬 · 색 사진 판정 — 없으면(임베딩 전 판) 글만으로 정한다
    PHOTO_AT.update(photo_attrs.load())
    PATTERN_FIX.update(photo_attrs.load_pattern_fix())
    _cf = DATA / "color_fix.csv"
    if _cf.exists():
        with _cf.open(encoding="utf-8-sig") as f:
            COLOR_FIXED.update(((r["브랜드"], r["상품번호"]), r["색"].strip()) for r in csv.DictReader(f) if (r.get("색") or "").strip())
    _pp = DATA / "photo_pred.json"
    if _pp.exists():
        PHOTO_PRED.update(json.loads(_pp.read_text(encoding="utf-8")))
    print(f"사진 무늬 · 색 판정 {len(PHOTO_AT):,}벌 · 사진 그래픽 {len(PHOTO_PRED):,}벌")

    # 매장마다 사진 주소 앞머리 — 줄에서 떼어 내고 catalog.json 에 한 번만 적는다
    urls_of: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.get("image_url"):
            urls_of[r["brand_slug"]].append(r["image_url"])
    slugs = sorted({r["brand_slug"] for r in rows})
    bi = {s: i for i, s in enumerate(slugs)}
    pref = {s: img_prefix(urls_of.get(s, [])) for s in slugs}
    codes = sorted({(r.get("category_code") or "other") for r in rows})
    ci = {c: i for i, c in enumerate(codes)}
    # 갈래 코드 → 화면 이름. 창고 줄에서 그대로 모은다(따로 적어 두면 낡는다).
    cat_label, grp_label = {}, {}
    for r in rows:
        c = r.get("category_code") or "other"
        cat_label.setdefault(c, r.get("category") or "")
        grp_label.setdefault(c, r.get("group") or "")
    brand_name, brand_mood, brand_chip = {}, {}, {}
    with (DATA / "brands_seed.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            brand_name[r["slug"]] = r.get("name") or r["slug"]
            brand_mood[r["slug"]] = fold_mood(r.get("mood_tags") or "")
    _lp = DATA / "mood_genre_labels.json"
    if _lp.exists():
        for s, v in (json.loads(_lp.read_text(encoding="utf-8")).get("labels") or {}).items():
            if s in brand_mood and v.get("g") and not v.get("unk"):
                brand_mood[s] = chip_mood(v, brand_mood[s]) or brand_mood[s]
                brand_chip[s] = v["g"]
    _pp = DATA / "llm" / "pmood.jsonl"
    if _pp.exists():
        pid_ = {r["source_url"]: (r["brand_slug"], f'{r["brand_slug"]}-{r["product_no"]}') for r in rows}
        for l in _pp.read_text(encoding="utf-8").splitlines():
            o = json.loads(l) if l.strip() else {}
            g = {("스트릿" if k == "스트리트" else k): int(v) for k, v in ((o.get("out") or {}).get("g") or {}).items()}
            g = {k: v for k, v in g.items() if k in MOOD_APP and v in (1, 2)}
            if g and len(g) <= 5 and o.get("u") in pid_:
                b_, k_ = pid_[o["u"]]
                PMOOD[k_] = dict(sorted(pmood_fix(g, brand_chip.get(b_, {})).items(), key=lambda x: (-x[1], MOOD_APP.index(x[0]))))
        print(f"상품 무드 {len(PMOOD):,}벌")

    # 상품 주소도 매장마다 앞머리가 같다 — 사진과 같은 방식으로 접는다.
    # `by` 는 아래에서 만들어지므로 여기서는 rows 에서 바로 모은다(안 그러면 NameError).
    _u_of: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        _u_of[r["brand_slug"]].append(r.get("source_url") or "")
    upref = {slug: img_prefix(us) for slug, us in _u_of.items()}

    shard: dict[str, list] = defaultdict(list)
    for r in rows:
        shard[r.get("category_code") or "other"].append(
            thin_row(r, tags, bi, ci, pref))
    files: dict[str, dict] = {}
    idx_gz = 0
    for code, items in sorted(shard.items()):
        n = write(out / "index" / f"{code}.json", items, args.dry)
        # 열쇠 이름은 **압축 전** 크기라는 뜻이다. 앞서 `gzip` 이라 적고 평문 값을 넣어
        # 앱이 다섯 배 잘못 판단할 뻔했다(앱 쪽 지적 2026-09-20: tops 가 gzip 11.08MB 로
        # 적혀 있는데 실제 전송량은 2.23MB). 오가는 값은 클라우드플레어가 정하므로 우리가
        # 알 수 없다 — 아는 것(평문)만 그 이름으로 적는다.
        files[f"index/{code}.json"] = {"n": len(items), "bytes": n}
        idx_gz += n
    # ── 검색 조각 — 11만 벌 **이름 전부**를 한 파일에 ──────────────────────────────
    # 갈래로 쪼개 두면 상의만 열어 본 사람이 검색을 누를 때 나머지를 그 자리에서 받아야
    # 한다(4.7MB). 이름·번호만 담으면 훨씬 작다 — 사진 주소가 목록 무게의 절반이다.
    # 자리로 적는다. 여기서는 사람이 읽을 일이 없고, 11만 번 반복되는 열쇠가 그대로 무게다.
    #   [브랜드번호, 상품번호, 이름, 갈래번호, 성별(0 여 · 1 남 · 2 공용), 판매중(1/0)]
    # 앱이 안 읽어 2026-10-26 부터 안 낸다 — 공개 버킷에서 이름 전부를 한 번에 받아 가기 쉬웠다(공개 데이터 줄이기 1단계).
    # 「이 옷 읽기」 범위표(size_ranges.py 주석 · layer-web docs/garment-reading.md §9)
    import size_ranges
    sr = size_ranges.build(rows, tags, sizes)
    srn = write(out / "size-ranges.json", sr, args.dry)
    files["size-ranges.json"] = {"n": len(sr["rows"]), "bytes": srn}
    bsp = DATA / "brand_similar.json"
    if bsp.exists():
        bs = json.loads(bsp.read_text(encoding="utf-8"))
        files["brand-similar.json"] = {"n": len(bs.get("brands") or {}), "bytes": write(out / "brand-similar.json", bs, args.dry)}
    print(f"범위표 {len(sr['rows']):,}칸 · 뺀 표본 {sr['excluded']}")

    print(f"목록 {len(rows):,}벌 · 갈래 {len(shard)}개 · 평문 {idx_gz/1048576:.1f} MB "
          f"(오갈 때는 brotli 로 줄어든다) — "
          + " · ".join(f"{k} {len(v):,}" for k, v in sorted(shard.items(), key=lambda x: -len(x[1]))[:4]))

    by = defaultdict(list)
    for r in rows:
        by[r["brand_slug"]].append(r)
    # 상세 화면은 **한 벌**을 보여 주는데 조각이 매장 통째였다 — 제일 큰 곳이 3,548KB 다.
    # 설명을 뺀 뒤에도 무거우므로 같은 잣대로 쪼갠다(2026-09-20). 앱은 `product_no % bp`
    # 로 어느 조각인지 바로 안다(bp 는 brands.json 에 적는다. 1 이면 안 쪼갠 것이다).
    SHARD_BYTES = 500_000
    tot = 0
    big = []
    shards_of: dict[str, int] = {}
    for slug, items in sorted(by.items()):
        made = [full(r, tags, sizes, crawl, upref) for r in items]
        raw = len(json.dumps(made, ensure_ascii=False, separators=(",", ":")).encode())
        bp = max(1, -(-raw // SHARD_BYTES))
        shards_of[slug] = bp
        if bp == 1:
            size = write(out / "brands" / f"{slug}.json", made, args.dry)
        else:
            buckets: list[list] = [[] for _ in range(bp)]
            for r, m in zip(items, made):
                buckets[int(r["product_no"]) % bp].append(m)
            size = sum(write(out / "brands" / f"{slug}.{i}.json", b, args.dry)
                       for i, b in enumerate(buckets))
        tot += size
        big.append((size // bp, slug, len(items), bp))
    big.sort(reverse=True)
    cut = [s for s, n in shards_of.items() if n > 1]
    print(f"브랜드 조각 {len(by)}곳 · 합계 {tot/1048576:.1f} MB · "
          f"쪼갠 매장 {len(cut)}곳 · 조각 하나 평균 {tot/sum(shards_of.values())/1024:.0f} KB")
    for s, slug, k, bp in big[:3]:
        print(f"   제일 큰 조각: {slug} {k}벌 ×{bp} → 조각당 {s/1024:.0f} KB")

    # ── 설명 — 브랜드 조각과 따로, 펼칠 때만 받는다 ─────────────────────────────
    # 브랜드 하나를 한 덩이로 두면 설명 하나 펼치자고 그 매장 설명 전부가 따라온다.
    # 전수로 재니 매장당 중앙 158KB 인데 꼬리가 길다 — years-ago 3,296KB ·
    # facade-pattern 1,984 · dunst 1,770 · 1MB 넘는 곳이 열 곳이다(2026-09-20).
    # 그래서 큰 곳은 쪼갠다. 앱은 `product_no % dp` 로 어느 조각인지 바로 안다
    # (dp 는 brands.json 에 적는다. 1 이면 안 쪼갠 것이다).
    DESC_PART_BYTES = 500_000
    desc_of = build_descs(rows, crawl, MINED, tags)
    parts_of: dict[str, int] = {}
    desc_bytes = 0
    for slug, m in sorted(desc_of.items()):
        raw = len(json.dumps(m, ensure_ascii=False, separators=(",", ":")).encode())
        dp = max(1, -(-raw // DESC_PART_BYTES))
        parts_of[slug] = dp
        if dp == 1:
            desc_bytes += write(out / "descs" / f"{slug}.json", m, args.dry)
            continue
        buckets: list[dict] = [{} for _ in range(dp)]
        for k, v in m.items():
            buckets[int(k.rsplit("-", 1)[1]) % dp][k] = v
        for i, b in enumerate(buckets):
            desc_bytes += write(out / "descs" / f"{slug}.{i}.json", b, args.dry)
    # AI 요약 — cards/<slug>.json {id: {s, se}} · brand-cards.json
    def _llm(name):
        p_ = DATA / "llm" / f"{name}.jsonl"
        if p_.exists():
            for l in p_.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    yield json.loads(l)
    pid_of = {r["source_url"]: f'{r["brand_slug"]}-{r["product_no"]}' for r in rows}
    se_of = {o["u"]: (o.get("out") or {}).get("season") or [] for o in _llm("card")}
    se_of.update({o["u"]: (o.get("out") or {}).get("season") for o in _llm("card_v2") if (o.get("out") or {}).get("season")})
    card_of: dict[str, dict[str, dict]] = defaultdict(dict)
    fix_of = {o["u"]: (o.get("out") or {}).get("s") for o in _llm("cardfix") if (o.get("out") or {}).get("s")}
    # v2(2026-10-22 프롬프트, 블라인드 200벌 오류 22%→14%)가 있으면 그것을 쓴다
    v2 = {o["u"]: o for o in _llm("card3_v2") if ((o.get("out") or {}).get("s") or "").strip()}
    fix_of = {u: t for u, t in fix_of.items() if u not in v2}       # 옛 고침은 옛 요약에만
    for o in [o for o in _llm("card3") if o["u"] not in v2] + list(v2.values()):
        t = haeyo.to_haeyo((fix_of.get(o["u"]) or (o.get("out") or {}).get("s") or "").strip())[0]
        t = KKE.sub(r"\1쯤", t)
        k = pid_of.get(o["u"])
        if t and k:
            card_of[k.rsplit("-", 1)[0]][k] = {"s": t, **({"se": se_of[o["u"]]} if se_of.get(o["u"]) else {})}
    card_parts_of: dict[str, int] = {}
    for slug, m in sorted(card_of.items()):
        cp = max(1, -(-len(json.dumps(m, ensure_ascii=False, separators=(",", ":")).encode()) // DESC_PART_BYTES))
        card_parts_of[slug] = cp
        if cp == 1:
            write(out / "cards" / f"{slug}.json", m, args.dry)
            continue
        buckets = [{} for _ in range(cp)]
        for k, v in m.items():
            buckets[int(k.rsplit("-", 1)[1]) % cp][k] = v
        for i, b in enumerate(buckets):
            write(out / "cards" / f"{slug}.{i}.json", b, args.dry)
    bcards = {}
    for o in _llm("brandcard"):
        x = o.get("out") or {}
        if o["u"] in by and x.get("summary"):
            bcards[o["u"]] = {"s": haeyo.to_haeyo(x["summary"])[0], "sig": x.get("signature") or [],
                              "price": haeyo.to_haeyo(x.get("price", ""))[0], "fit": haeyo.to_haeyo(x.get("fit", ""))[0]}
    # brand-cards.json 은 앱이 안 읽어 공개 버킷에 안 낸다(2026-10-26). 만들어 두기만 한다.
    print(f"AI 요약 {sum(len(m) for m in card_of.values()):,}벌 · 브랜드 카드 {len(bcards)}곳")
    split = [s for s, n in parts_of.items() if n > 1]
    print(f"설명 조각 {len(desc_of)}곳 · 합계 {desc_bytes/1048576:.1f} MB · "
          f"쪼갠 매장 {len(split)}곳 ({', '.join(f'{s}×{parts_of[s]}' for s in sorted(split))})")

    # ── 비슷한 옷 — 브랜드별 조각, 상세를 열 때만 받는다 ─────────────────────────
    # data/similar_items.json(similar_items.py, 상품마다 40벌 · 점수)을 상세 · 설명 조각과 같은 잣대로 매장별로 자른다.
    # 앱은 `product_no % sp` 로 어느 조각인지 안다(sp 는 brands 에 적는다. 1 이면 안 쪼갠 것, 0 이면 그 매장은 없음).
    # 줄 모양: {상품 id: [[이웃 상품 id, 점수 0~1], …]} — 이웃은 다른 브랜드일 수 있으니 id 를 그대로 적는다(앱 세션 2026-10-05).
    # 원본은 [번호, 점수×1000, 번호, 점수×1000 …] 로 평평하게 적혀 있다(크기 때문) — 여기서 앱이 읽기 쉬운 짝으로 푼다.
    SIM_PART_BYTES = 500_000
    sim_parts_of: dict[str, int] = {}
    sim_bytes = 0
    sim_of: dict[str, dict[str, list]] = defaultdict(dict)
    sim_path = DATA / "similar_items.json"
    if sim_path.exists():
        sim = json.loads(sim_path.read_text(encoding="utf-8"))
        sidx = sim.get("index") or []
        exported = {f'{r["brand_slug"]}-{r["product_no"]}' for r in rows}   # APP_HOLD 매장 등 앱에 안 나가는 상품은 이웃에서도 뺀다(코덱스 2026-10-05)
        dropped_nb = below_min = 0
        for pid, nb in (sim.get("items") or {}).items():
            if pid not in exported:
                continue
            slug = pid.rsplit("-", 1)[0]
            pairs = []
            for j, sc in zip(nb[0::2], nb[1::2]):
                if 0 <= j < len(sidx) and sidx[j] in exported:
                    if sc / 1000 >= SIM_MIN:
                        pairs.append([sidx[j], min(1.0, max(0.0, sc / 1000))])   # 세부 품목 가산으로 1 을 살짝 넘을 수 있어 0~1 로 자른다
                    else:
                        below_min += 1
                else:
                    dropped_nb += 1
            if pairs:
                sim_of[slug][pid] = pairs
        for slug, m in sorted(sim_of.items()):
            raw = len(json.dumps(m, ensure_ascii=False, separators=(",", ":")).encode())
            sp = max(1, -(-raw // SIM_PART_BYTES))
            while True:   # product_no % sp 가 고르지 않으면 한 조각이 상한을 넘는다 — 넘으면 조각 수를 늘려 다시 나눈다
                buckets_s: list[dict] = [{} for _ in range(sp)]
                for k, v in m.items():
                    buckets_s[int(k.rsplit("-", 1)[1]) % sp][k] = v
                if sp == 1 or sp >= 64 or all(len(json.dumps(b_, ensure_ascii=False, separators=(",", ":")).encode()) <= SIM_PART_BYTES for b_ in buckets_s):
                    break
                sp += 1
            sim_parts_of[slug] = sp
            if sp == 1:
                sim_bytes += write(out / "similar" / f"{slug}.json", m, args.dry)
                continue
            for i, b_ in enumerate(buckets_s):
                sim_bytes += write(out / "similar" / f"{slug}.{i}.json", b_, args.dry)
        print(f"   앱에 안 나가는 상품이라 뺀 이웃 {dropped_nb:,} · 점수 {SIM_MIN} 미만이라 뺀 이웃 {below_min:,} · "
              f"남은 이웃 3벌 미만인 상품 {sum(1 for m in sim_of.values() for v in m.values() if len(v) < 3):,}")
        print(f"비슷한 옷 조각 {len(sim_of)}곳 · 상품 {sum(len(m) for m in sim_of.values()):,}벌 · 합계 {sim_bytes/1048576:.1f} MB · "
              f"쪼갠 매장 {sum(1 for n in sim_parts_of.values() if n > 1)}곳 · version {sim.get('version')}")
    else:
        print("비슷한 옷 조각 없음 — data/similar_items.json 이 없다(similar_items.py 가 아직 안 돌았다)")

    # ── 기준별 비슷한 옷(색·무늬 · 모양 · 소재) — 앱 「비슷한 옷」 글자 줄, 그 기준을 처음 볼 때만 받는다 ─────────
    # 원본 data/similar_axes.json(similar_items.py) — 상품마다 {cp, s, m: [번호, 점수×1000, …] 12벌, f: 비트}.
    # 조각 similar-axis/<slug>[.i].json, 쪼개기는 similar 와 같은 규칙(product_no % ap), 줄: {id: {"cp": [[이웃 id, 점수]…], "s": …, "m": …}}.
    # 정보 없는 기준은 키를 안 적는다. 이웃은 앱에 나가는 상품만(앱 세션 2026-10-06).
    ax_parts_of: dict[str, int] = {}
    ax_of: dict[str, dict[str, dict]] = defaultdict(dict)
    ax_bytes = 0
    if axes.get("items"):
        aidx = axes.get("index") or []
        exported_ax = {f'{r["brand_slug"]}-{r["product_no"]}' for r in rows}
        for pid, v in axes["items"].items():
            if pid not in exported_ax:
                continue
            row_ = {}
            for key in ("cp", "s", "m"):
                pairs = ax_pairs(v.get(key) or [], aidx, exported_ax)
                if len(pairs) >= AX_MIN:
                    row_[key] = pairs
            if row_:
                ax_of[pid.rsplit("-", 1)[0]][pid] = row_
        for slug, m in sorted(ax_of.items()):
            raw = len(json.dumps(m, ensure_ascii=False, separators=(",", ":")).encode())
            ap = max(1, -(-raw // SIM_PART_BYTES))
            while True:
                buckets_a: list[dict] = [{} for _ in range(ap)]
                for k, v in m.items():
                    buckets_a[int(k.rsplit("-", 1)[1]) % ap][k] = v
                if ap == 1 or ap >= 64 or all(len(json.dumps(b_, ensure_ascii=False, separators=(",", ":")).encode()) <= SIM_PART_BYTES for b_ in buckets_a):
                    break
                ap += 1
            ax_parts_of[slug] = ap
            if ap == 1:
                ax_bytes += write(out / "similar-axis" / f"{slug}.json", m, args.dry)
                continue
            for i, b_ in enumerate(buckets_a):
                ax_bytes += write(out / "similar-axis" / f"{slug}.{i}.json", b_, args.dry)
        print(f"기준별 비슷한 옷 조각 {len(ax_of)}곳 · 상품 {sum(len(m) for m in ax_of.values()):,}벌 · 합계 {ax_bytes/1048576:.1f} MB · version {axes.get('version')}")
    else:
        print("기준별 비슷한 옷 조각 없음 — data/similar_axes.json 이 없다")

    # ── catalog.json — 앱이 제일 먼저 읽는 도장 ────────────────────────────────
    # 하루 한 번 데이터가 바뀌는데 앱이 「새 게 나왔는지」를 알 길이 없었다. 받아 둔 걸
    # 계속 쓰거나 매번 4MB 를 다시 받거나 둘 중 하나가 된다. `v` 한 줄이 그걸 푼다 —
    # 앱은 이것만 보고 바뀐 조각만 다시 받는다(앱 쪽 요청 2026-09-20).
    #
    # 번호표(브랜드·갈래)는 **여기에만** 둔다. index 파일마다 적으면 브랜드가 늘 때마다
    # 모든 index 를 다시 써야 한다.
    #
    # 사진 주소는 imgBase 한 줄로 못 만든다 — 실측: 도메인에 매장 슬러그가 든 것 58%,
    # 83곳이 공용 CDN(cafe24img.poxo.com·ecimg.cafe24img.com), 경로가 /web/product/ 로
    # 시작하는 것 62%(나머지는 /pushbuttonspace/web/ 처럼 매장 계정명이 낀다).
    # 그래서 **매장마다** 앞머리(p)를 적는다. 그러면 115,934장이 100% 접힌다.
    #   사진 주소 = brands[b].p + 줄의 m
    for slug, bp in shards_of.items():
        files[f"brands/{slug}.json" if bp == 1 else f"brands/{slug}.<0..{bp-1}>.json"] = {
            "n": len(by[slug]), "parts": bp}
    for slug, dp in parts_of.items():
        files[f"descs/{slug}.json" if dp == 1 else f"descs/{slug}.<0..{dp-1}>.json"] = {
            "n": len(desc_of[slug]), "parts": dp}
    for slug, cp in card_parts_of.items():
        files[f"cards/{slug}.json" if cp == 1 else f"cards/{slug}.<0..{cp-1}>.json"] = {
            "n": len(card_of[slug]), "parts": cp}
    for slug, sp in sim_parts_of.items():
        files[f"similar/{slug}.json" if sp == 1 else f"similar/{slug}.<0..{sp-1}>.json"] = {
            "n": len(sim_of[slug]), "parts": sp}
    for slug, ap in ax_parts_of.items():
        files[f"similar-axis/{slug}.json" if ap == 1 else f"similar-axis/{slug}.<0..{ap-1}>.json"] = {
            "n": len(ax_of[slug]), "parts": ap}
    catalog = {
        "v": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "count": len(rows),
        # 줄의 열쇠가 무슨 뜻인가 — 앱이 여기서 읽게 해 두면 열쇠가 늘어도 안 어긋난다
        "fields": {
            "i": "상품 id (<slug>-<product_no>)", "b": "brands 번호", "n": "이름",
            "p": "지금 사는 값(원) — 할인 중이면 할인가. 0 이면 값을 모른다(매장이 값을 내린 품절 상품 — 「0원」으로 보여 주지 말 것)", "op": "할인 전 정가(원) — 할인 중일 때만, 줄 그어 보여 줄 값(없으면 안 적힘 · 목록에서 내려간 상품은 안 적힘)",
            "m": "사진 — brands[b].p 를 앞에 붙인다",
            "c": "cats 번호", "t": "품목(subtype)", "g": "W 여성 · M 남성 · U 남녀공용",
            "s": "1 판매중 · 0 품절", "co": "대표색 — 이름 · 옵션 낱말, 옷은 사진이 확신하면 고치거나 채움", "ma": "대표소재", "se": "시즌",
            "cg": "색만 다른 형제 묶음(없으면 안 적힘)",
            "tg": "스타일 태그 — tags 번호 배열, 드문 것부터 많아야 6(없으면 안 적힘)",
            "ax": "기준별 비슷한 옷이 있는 기준 비트 — 1 색·무늬 · 2 모양 · 4 소재(similar-axis 조각, 0 이면 안 적힘)",
            "pt": "1 이면 무늬 있음(스트라이프 · 체크 · 카모 · 꽃 · 큰 그래픽 프린트 …, 작은 로고는 무지) — 이름 · 사진 · 설명으로 판정, 무지 · 모름은 안 적힘",
            "pm": "상품 무드(중심) — moods 번호 배열. 사진으로 판정, 없으면 안 적힘",
            "pw": "상품 무드(약간) — moods 번호 배열, 없으면 안 적힘",
            "wn": "1 이면 대표 사진(m)이 착용컷(사람이 입은 컷 — 얼굴이 잘려도 팔 · 다리가 보이면). 옷 분할 모델 · 글-사진 모델(착용 점수) 판정, 아니거나 아직 안 쟀으면 안 적힘",
            "dt": "하의 디테일 — 카고 · 카펜터 · 퍼티그 · 파라슈트 · 벌룬 중 여럿(배열). 하의 · 데님 · 스커트에만, 상품명 · 세부품목에서 읽는다. "
                  "세부품목(t)과 따로라 데님 카고는 t=데님팬츠 · dt=[카고]. 없으면 안 적힘",
            "dw": "데님 워싱 단계 — 연청 · 중청 · 진청 · 생지 · 흑청 · 블랙 · 화이트 · 컬러 중 하나. 데님 옷에만, 대표색 · 이름 · 옵션 낱말로 정하고 "
                  "모르면 안 적힘(블루 · 인디고만으로는 정하지 않는다). 낱말로 못 정한 것 일부는 사진 판정(교차검증 정밀도 90% 넘는 단계 · 확신만)",
        },
        # 브랜드 무드는 brands[].mo, 상품 무드는 줄의 pm · pw
        "brand_fields": {"mo": "무드 — moods 안의 말만 쓴다",
                         "p": "사진 주소 앞머리", "up": "상품 주소 앞머리",
                         "bp": "brands 조각 수", "dp": "descs 조각 수(0 이면 없음)", "sp": "similar 조각 수(0 이면 없음)",
                         "ap": "similar-axis 조각 수(0 이면 없음)", "cp": "cards(AI 요약) 조각 수(0 이면 없음)"},
        # 상세 조각(brands/<slug>.json) 한 벌에만 있는 열쇠
        "detail_fields": {"u": "상품 주소 — brands[b].up 을 앞에 붙인다",
                          "mat": "혼용률 — [{p: 부위(빈칸이면 구분 없음), v: [[소재, %], …]}, …]"},
        # 비슷한 옷 조각(similar/<slug>.json) — 상품 id → [[이웃 상품 id, 점수 0~1], …] 최대 40, 점수 높은 순.
        # 큰 품목 안 · 사진 반 태그 반 · 브랜드 무드 · 가격. 같은 브랜드 · 같은 옷 다른 색은 없다. 노션 「유사도·추천 설계」 2절.
        "similar_fields": {"<id>": "[[이웃 id, 점수], …] — 이웃은 다른 매장일 수 있다. 앱은 보일 때 브랜드당 2벌로 섞는다. "
                                   f"점수 {SIM_MIN} 미만은 안 싣는다 — 3벌 미만이면 앱은 「다른 브랜드의 ○○」로 대신한다"},
        # 기준별 비슷한 옷(similar-axis/<slug>.json) — 같은 큰 품목 안에서 그 기준 위주로 다시 찾은 12벌(브랜드당 2벌), 점수 높은 순.
        "similar_axis_note": f"기준마다 점수 {SIM_MIN} 미만은 안 싣고, {AX_MIN}벌 미만이면 그 기준 키를 뺀다(index 줄 ax 비트도 꺼짐)",
        "similar_axis_fields": {"cp": "색·무늬 — 무지끼리 · 또는 무늬(패턴 · 프린트)가 같은 옷, 그 안에서 색 · 워싱 순",
                                "s": "모양 — 사진(옷만 잘라 낸 판) 반 · 실루엣 · 기장 · 소매 · 넥라인 · 디테일 태그 반",
                                "m": "소재 — 혼용률 · 소재 태그 · 겉보기 소재(가죽 · 스웨이드 · 퍼 · 데님 …)"},
        # 품목 계층 — 갈래 → 큰 품목 → 세부 품목(subtype 값). 앱은 손으로 쓴 품목 표 대신 이걸 읽는다(앱 세션 2026-10-05).
        # 원칙 「모양이 품목, 소재는 태그」 — 레더 · 스웨이드 · 트위드는 품목이 아니라 ma(대표소재) · 태그로 거른다. 무스탕만 품목.
        "hier": [{"sec": sec, "cat": cat, **({"base": base} if base else {}), "subs": subs} for sec, cat, base, subs in HIER],
        "brands": [{"i": bi[s], "s": s, "n": brand_name.get(s, s), "p": pref.get(s, ""),
                    "c": len(by.get(s, [])), "bp": shards_of.get(s, 1), "dp": parts_of.get(s, 0), "sp": sim_parts_of.get(s, 0),
                    "ap": ax_parts_of.get(s, 0), "cp": card_parts_of.get(s, 0),
                    "im": next((r.get("image_url") for r in by.get(s, []) if r.get("image_url")), ""),
                    **({"up": upref[s]} if upref.get(s) else {}),
                    **({"mo": brand_mood[s]} if brand_mood.get(s) else {})}
                   for s in slugs],
        "cats": [{"i": ci[c], "c": c, "n": cat_label.get(c, ""), "g": grp_label.get(c, "")}
                 for c in codes],
        "moods": MOOD_APP,
        # 줄의 tg 가 가리키는 말. 축: 디자인 요소 · 실루엣 · 구성 · 기장 · 무늬(단색 뺌) · 둘째 소재. 흔한 것이 앞번호.
        "tags": tag_list,
        "files": files,
    }
    b = write(out / "catalog.json", catalog, args.dry)
    print(f"도장 catalog.json — 매장 {len(slugs)}곳 · 갈래 {len(codes)}개 · "
          f"파일 {len(files)}개 · {b/1024:.0f} KB · v={catalog['v']}")
    if args.dry:
        print("(--dry 라 아무것도 쓰지 않았다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
