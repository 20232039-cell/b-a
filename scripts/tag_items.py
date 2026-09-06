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
# 옷 전용 축(넥라인·소매·실루엣·기장·바지종류)을 지울 품목. NON_GARMENT 보다 넓다 —
# 모자와 주얼리가 빠져 있어서 캡에 「레귤러핏」 59개, 발라클라바에 넥라인 「후드」 37개,
# 브로치에 「슬리브리스」가 붙어 있었다(합계 243개, 2026-09-06 실측).
# 보온성·통기성 갈래에는 NON_GARMENT 를 그대로 쓴다 — 기모 비니는 보온성이 맞다.
NO_GARMENT_AXES = NON_GARMENT | {"Headwear", "Jewelry"}
PANTS = {"Pants", "Denim", ""}
BOTTOMS = {"Pants", "Denim", "Skirts"}
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


def compile_alias(alias: str) -> re.Pattern:
    a = re.escape(alias.lower())
    if _is_hangul(alias) and len(alias) == 1:
        return re.compile(rf"(?<![{HANGUL}]){a}(?![{HANGUL}])")
    if _is_hangul(alias) and len(alias) == 2:
        return re.compile(rf"(?<![{HANGUL}]){a}")
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
    if not text or not _PRICE_RUN.search(text):
        return text
    out, last = [], 0
    for m in _PRICE_RUN.finditer(text):
        win_from = max(last, m.start() - back)
        win = text[win_from:m.start()]
        k = 0
        for mm in _SENT_END.finditer(win):
            k = mm.end()
        cut = win_from + k
        if cut >= last:
            out.append(text[last:cut])
        last = m.end()
    out.append(text[last:])
    return "".join(out)


class Tagger:
    def __init__(self, vocab: dict):
        self.vocab = vocab
        self.mine_only = set(vocab.get("_mine_alias_only", []))
        self.blocklist = [b.lower() for b in vocab.get("_color_blocklist", [])]
        self.text_blocklist = [b.lower() for b in vocab.get("_text_blocklist", [])]
        self.bottoms_only = [tuple(x.split(".", 1)) for x in vocab.get("_bottoms_only", [])]
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
        text = f"{name}\n{strip_other_products(body)}".lower()
        for b in self.text_blocklist:  # '시어링'→시어, '레이어드 스타일링'→레이어드 같은 오탐을 먼저 지운다
            text = text.replace(b, " " * len(b))
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
        if category not in BOTTOMS | {""}:
            for ax, val in self.bottoms_only:  # 하의 전용 값이 코디 문장으로 상의에 붙는 것 방지
                hits.get(ax, set()).discard(val)
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
        if quality == "ok" and not hits.get("pattern"):
            hits["pattern"].add("단색")
        # 소매 길이는 매장이 글로 안 적는다 — 소매가 빈 상의 12,248벌 중 12,170벌(99.4%)이
        # 원문 어디에도 없다(2026-09-05). 대신 우리에겐 실측 소매길이가 있다.
        # 롱슬리브 중앙 61cm · 반팔 중앙 21.6cm 로 뚜렷이 갈린다. 가운데(27~54cm)는 비워 두고
        # 양 끝만 쓴다 — 글에 아무 말도 없을 때만. 글로 아는 2,709벌에 대 보니 반팔↔롱슬리브
        # 혼동은 46건(98.1%)이고, 나머지 틀림은 슬리브리스·퍼프소매인데 그것들은 글에 적혀 있어
        # 애초에 여기까지 오지 않는다.
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
    return "\n".join(_HAN_ANY.sub(" ", l) if _gibberish(l) else l for l in text.split("\n"))


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
