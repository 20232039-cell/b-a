"""cafe24 자사몰 전상품 크롤러 — brands_seed.csv 의 브랜드를 사이트맵으로 훑는다.

왜 cafe24 하나만 보나: 상품이 있는 50개 브랜드의 자사몰이 전부 cafe24 다
(2026-08-31 실측 — /product/detail.html?product_no= 또는 /product/<slug>/<no>/category/…
SEO URL, 둘 다 cafe24 형식). 어댑터 하나로 50곳이 붙는다.

전체 상품을 어떻게 세나:
  1. /sitemap.xml — cafe24 가 자동으로 만들어 준다. /product/<slug>/<no>/ 가 상품 하나다.
     카테고리 목록을 훑는 것보다 낫다: 중복이 없고, 어느 카테고리에도 안 걸린 상품도 잡힌다.
     (없거나 비면 카테고리 목록 크롤로 내려간다.)
  2. /product/list.html?cate_no=N — 상품이 어느 카테고리에 속하는지는 상세 페이지가 말해
     주지 않는다(상세의 oCategoryInfo 는 「개인결제창」 같은 값이 들어 있었다). 그래서 목록을
     한 번 훑어 product_no → 카테고리 이름들을 따로 얻는다. 목록은 48개/쪽이라 싸다.
  3. 상세 페이지에서 이름·판매가·대표컷·품절·추가컷·설명·옵션.

수집하지 않는 것:
  · 할인가(product_sale_price) — 타임세일·쿠폰으로 수시로 바뀌는데 우리는 주 단위로 본다.
    틀린 가격을 띄우는 건 표시 문제라 빈 칸조차 두지 않는다(build_products_seed.py 와 같은 판단).

예의:
  · 호스트당 1초 간격(+지터). 여러 호스트는 병렬이지만 한 호스트에는 동시에 하나만.
  · robots.txt 의 Disallow 를 존중한다(cafe24 기본값은 /exec/ /member/ /myshop/ 등 —
    상품 페이지는 허용이고, 매장이 특정 cate_no 를 막아둔 경우가 있어 그건 건너뛴다).
  · UA 에 정체와 연락처 성격의 이름을 적는다.

재실행 가능: data/crawl/<slug>.jsonl 에 상품 단위로 바로 쓰고, 다시 돌리면 이미 있는
product_no 는 건너뛴다. --refresh 를 주면 전부 다시 받는다.

사용:
    py scripts/crawl_cafe24.py                      # 상품이 있는 브랜드 전부
    py scripts/crawl_cafe24.py --brands 9999archive coor
    py scripts/crawl_cafe24.py --workers 8 --delay 1.0
    py scripts/crawl_cafe24.py --build-csv          # jsonl → data/products_full.csv 만 다시 만든다
"""
from __future__ import annotations

import argparse
import collections
import csv
import html as htmlmod
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CRAWL_DIR = DATA / "crawl"
BRANDS_CSV = DATA / "brands_seed.csv"
PRODUCTS_SEED = DATA / "products_seed.csv"
OUT_CSV = DATA / "products_full.csv"
SUMMARY_JSON = CRAWL_DIR / "_summary.json"

UA = "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"
TIMEOUT = 25
KST = timezone(timedelta(hours=9))

# ─── 어휘 — build_products_seed.py 와 같은 표. 새 값을 임의로 만들지 않는다(CLAUDE.md §3). ───

COLOR_VOCAB = {
    "블랙": ["black", "블랙"], "차콜": ["charcoal", "차콜"], "화이트": ["off white", "white", "화이트"],
    "아이보리": ["ivory", "아이보리"], "크림": ["cream", "크림"],
    "그레이": ["melange grey", "grey", "gray", "그레이", "멜란지"], "네이비": ["navy", "네이비"],
    "블루": ["light blue", "blue", "블루"], "민트": ["mint", "민트"], "그린": ["green", "그린"],
    "올리브": ["olive", "올리브"], "베이지": ["beige", "베이지"], "샌드": ["sand", "샌드"],
    "카키": ["khaki", "카키"], "카멜": ["camel", "카멜"], "브라운": ["brown", "브라운"],
    "초코": ["choco", "초코"], "옐로우": ["yellow", "옐로우", "lemon"], "핑크": ["pink", "핑크"],
    "레드": ["red", "레드"], "버건디": ["burgundy", "버건디", "와인"], "퍼플": ["purple", "퍼플"],
    "라벤더": ["lavender", "라벤더"], "라떼": ["latte", "라떼"], "피치": ["peach", "피치"],
    "버터": ["butter", "버터"], "내추럴": ["natural", "내추럴"],
    "차콜그레이": ["charcoal grey", "charcoal gray"], "스카이블루": ["sky blue", "스카이블루"],
    "실버": ["silver", "실버"], "골드": ["gold", "골드"], "코발트": ["cobalt", "코발트"],
    "머스타드": ["mustard", "머스타드"], "터콰이즈": ["turquoise", "터콰이즈"],
    "오트밀": ["oatmeal", "오트밀"], "잉크": ["ink blue", "잉크"],
}

ITEM_TYPE_VOCAB = {
    "후드": ["hoodie", "hoody", "후드", "후디", "sweat hoody", "sweat hoodie", "스웻 후드", "스웻후드"],
    "집업": ["zip-up", "zipup", "zip up", "집업", "half zip", "하프집업", "full zip", "풀집업", "quarter zip", "쿼터 집", "쿼터집"],
    # 스웻은 별도 품목이 아니다(사람 결정 2026-09-02): 스웻셔츠=맨투맨, 스웻팬츠=스웨트팬츠. 품목 단어 없는 「스웻」은 build_csv 가 상의일 때만 맨투맨
    "맨투맨": ["sweatshirt", "sweat shirt", "맨투맨", "crewneck", "crew neck", "스웻셔츠", "스웨트셔츠", "스웨트 셔츠", "스웻 셔츠", "스웻 크루넥", "sweat crew"],
    "티셔츠": ["t-shirt", "tshirt", "tee", "티셔츠"],
    "셔츠": ["shirt", "blouse", "셔츠", "블라우스"],
    "니트": ["knit", "sweater", "니트", "스웨터", "cardigan", "카디건"],
    "재킷": ["jacket", "자켓", "재킷", "blouson", "블루종"],
    "코트": ["coat", "코트"],
    "패딩": ["padding", "puffer", "패딩", "다운"],
    "데님": ["jeans", "denim", "데님", "청바지"],
    "팬츠": ["pants", "trousers", "팬츠", "슬랙스", "slacks"],
    "스커트": ["skirt", "스커트"],
    "원피스": ["dress", "원피스", "드레스"],
    "베스트": ["vest", "베스트"],
    "바람막이": ["windbreak", "windbreaker", "바람막이", "아노락", "anorak"],
    "숏팬츠": ["shorts", "숏팬츠", "반바지"],
    "점프수트": ["jumpsuit", "점프수트", "overall", "오버올"],
    "가디건": ["가디건"],
    "블레이저": ["blazer", "블레이저"],
    "트렌치": ["trench", "트렌치"],
    "점퍼": ["jumper", "점퍼"],
    "탑": ["top", "탑", "sleeveless", "슬리브리스", "민소매"],
    "롱슬리브": ["long sleeve", "롱슬리브", "긴팔"],
    "쇼츠": ["shorts", "쇼츠"],
    "스웨트팬츠": ["sweatpants", "sweat pants", "sweat pant", "스웨트팬츠", "스웨트 팬츠", "스웻팬츠", "스웻 팬츠", "트레이닝 팬츠", "트레이닝팬츠", "training pants"],
    "카고팬츠": ["cargo pants", "카고팬츠", "cargo"],
    # 2026-09-02 전상품 크롤에서 확인된 값 — 기존 subtype 표(products_seed.csv)에 이미 있던 이름만 쓴다.
    "저지": ["jersey", "저지"],
    "버뮤다": ["bermuda", "버뮤다"],
    "파카": ["parka", "파카"],
    "MA-1/봄버": ["bomber", "ma-1", "봄버"],
    "플리스": ["fleece", "플리스"],
    "후드집업": ["hood zip", "hooded zip", "후드집업", "hoodie zip"],
    "반팔": ["half sleeve", "short sleeve", "half t", "half tee", "반팔", "s/s tee", "ss tee"],
    "피케": ["polo", "pique", "피케", "폴로"],
    "레깅스": ["leggings", "레깅스"],
    "조거팬츠": ["jogger", "track pants", "조거"],
}

# item_type → 대분류. categories_seed.csv 의 depth-1 코드를 따른다
# (tops·outer·bottoms·dress·skirt·shoes·bags·accessories·suiting).
ITEM_TO_CATEGORY = {
    "후드": "tops", "집업": "tops", "맨투맨": "tops", "티셔츠": "tops", "셔츠": "tops", "니트": "tops",
    "베스트": "tops", "탑": "tops", "롱슬리브": "tops", "가디건": "tops",
    "재킷": "outer", "코트": "outer", "패딩": "outer", "바람막이": "outer", "블레이저": "outer",
    "트렌치": "outer", "점퍼": "outer",
    "데님": "bottoms", "팬츠": "bottoms", "숏팬츠": "bottoms", "쇼츠": "bottoms",
    "스웨트팬츠": "bottoms", "카고팬츠": "bottoms",
    "스커트": "skirt", "원피스": "dress", "점프수트": "dress",
    "저지": "tops", "반팔": "tops", "피케": "tops", "후드집업": "tops",
    "버뮤다": "bottoms", "레깅스": "bottoms", "조거팬츠": "bottoms",
    "파카": "outer", "MA-1/봄버": "outer", "플리스": "outer",
}

# 상품명·카테고리로도 못 정할 때 — 상세 설명의 치수 항목이 옷 종류를 말한다.
# 「Chest / Shoulder」가 있으면 상의 계열, 「Waist / Inseam / Thigh」면 하의.
SPEC_RULES = [
    ("bottoms", ["inseam", "밑위", "허벅지", "thigh", "waist", "허리", "밑단", "hem width", "leg opening"]),
    ("tops", ["chest", "가슴", "shoulder", "어깨", "sleeve", "소매", "총장", "armhole"]),
]

# 카테고리 이름(매장마다 제멋대로) → 대분류. 상품명으로 못 정했을 때의 폴백.
CATEGORY_NAME_RULES = [
    ("outer", ["outer", "아우터", "jacket", "자켓", "재킷", "coat", "코트", "jumper", "점퍼", "padding", "패딩", "blouson", "블루종"]),
    ("dress", ["dress", "원피스", "드레스", "one-piece", "onepiece"]),
    ("skirt", ["skirt", "스커트"]),
    ("bottoms", ["bottom", "하의", "pants", "팬츠", "denim", "데님", "jeans", "shorts", "쇼츠", "반바지", "trouser", "slacks", "슬랙스"]),
    ("tops", ["top", "상의", "tee", "t-shirt", "shirt", "셔츠", "knit", "니트", "sweat", "hood", "후드", "맨투맨", "blouse", "블라우스", "cardigan", "가디건", "vest", "베스트"]),
    ("shoes", ["shoes", "신발", "sneaker", "boots", "부츠", "sandal", "샌들", "slipper", "슬리퍼", "footwear"]),
    ("bags", ["bag", "가방", "tote", "backpack", "wallet", "지갑", "pouch", "파우치"]),
    ("accessories", ["acc", "액세서리", "악세사리", "cap", "hat", "모자", "belt", "벨트", "socks", "양말", "jewelry", "jewellery", "ring", "necklace", "scarf", "머플러", "muffler", "underwear", "언더웨어", "eyewear", "sunglass", "keyring", "glove", "장갑", "beanie", "비니", "bag charm", "charm", "키링", "wallet", "지갑"]),
    ("suiting", ["suit", "수트", "정장", "setup", "셋업"]),
]

# 성별 — 카테고리 이름에서. 없으면 브랜드 기본값.
GENDER_RULES = [
    ("WOMENSWEAR", ["women", "woman", "여성", "우먼", "womens", "ladies"]),
    ("MENSWEAR", ["men", "man", "남성", "mens"]),
    ("UNISEX", ["unisex", "유니섹스"]),
]
BRAND_GENDER = {"Womenswear": "WOMENSWEAR", "Menswear": "MENSWEAR", "Unisex": "UNISEX"}

# 상품이 아닌 페이지의 이름 — 개인결제·스태프 결제·룩북·테스트. 가격이 있어도 상품이 아니다.
# ^@ — glowny 가 고객 착용샷을 「@인스타아이디」 상품(2,500,000원)으로 830건 올려 둠. ^[¥*]+ — insilence 비공개 자리표시자 159건 (사람 결정 2026-09-02)
JUNK_NAME = re.compile(r"^@|^[¥*\s]+$|실장님|이사님|원장님|디자이너\s*님|\s님\s*$|개인\s*결제|테스트|샘플|배송비|추가\s*금|lookbook|룩북|campaign|캠페인|\d{4}\s*(spring|summer|fall|autumn|winter)", re.I)

# 판매·기획 카테고리 — 대분류 판정에서 뺀다(「SALE」이 tops 로 읽히면 안 된다). 소속은 기록한다.
NOISE_CATEGORY = ["sale", "세일", "new", "신상", "best", "베스트", "all", "전체", "view", "collection", "컬렉션",
                  "project", "week", "event", "이벤트", "off", "drop", "season", "must", "pick", "clearance", "time", "outlet"]


def match_vocab(text: str, vocab: dict) -> str:
    """가장 긴 키워드부터 — 'zip up' 이 'up' 보다, 'sweatshirt' 가 'shirt' 보다 먼저."""
    low = text.lower()
    best, best_len = "", 0
    for label, keys in vocab.items():
        for k in keys:
            if k in low and len(k) > best_len:
                best, best_len = label, len(k)
    return best


def classify_category(name: str, category_names: list[str], description: str = "") -> str:
    item = match_vocab(name, ITEM_TYPE_VOCAB)
    if item in ITEM_TO_CATEGORY:
        return ITEM_TO_CATEGORY[item]
    for cat in category_names:
        low = cat.lower()
        if any(n in low for n in NOISE_CATEGORY):
            continue
        for code, keys in CATEGORY_NAME_RULES:
            if any(k in low for k in keys):
                return code
    # 카테고리로도 못 정하면 상품명에서 한 번 더(신발·가방·액세서리 낱말)
    for code, keys in CATEGORY_NAME_RULES:
        if code in ("shoes", "bags", "accessories") and any(k in name.lower() for k in keys):
            return code
    # 마지막으로 설명문의 치수 항목. 하의 낱말을 먼저 본다 — 상의에도 「총장」은 있지만
    # 하의에 「chest」는 없다.
    low = (description or "").lower()
    for code, keys in SPEC_RULES:
        if any(k in low for k in keys):
            return code
    return "other"


def pick_color(name: str, description: str) -> str:
    c = match_vocab(name, COLOR_VOCAB)
    if c:
        return c
    # 이름에 색을 안 적는 매장(9999archive: 203 중 202)이 설명에 「Color: Washed Black」으로 적는다.
    m = re.search(r"(?:colou?r|색상|컬러)\s*[:：]\s*([^▪•|/\n]{1,40})", description or "", re.I)
    return match_vocab(m.group(1), COLOR_VOCAB) if m else ""


def classify_gender(category_names: list[str], brand_default: str) -> str:
    joined = " ".join(category_names).lower()
    for g, keys in GENDER_RULES:
        if any(re.search(rf"\b{k}\b", joined) for k in keys):
            return g
    return brand_default or "UNISEX"


# ─── HTTP — 호스트당 1초, 재시도 ───

class PoliteSession:
    def __init__(self, delay: float):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "ko,en;q=0.8"})
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._glock = threading.Lock()
        self.requests_made = 0

    def _lock_for(self, host: str) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault(host, threading.Lock())

    def get(self, url: str, retries: int = 2) -> requests.Response | None:
        host = urlparse(url).netloc
        lock = self._lock_for(host)
        for attempt in range(retries + 1):
            with lock:
                wait = self.delay + random.uniform(0, 0.3) - (time.monotonic() - self._last.get(host, 0))
                if wait > 0:
                    time.sleep(wait)
                self._last[host] = time.monotonic()
                self.requests_made += 1
                try:
                    r = self.s.get(url, timeout=TIMEOUT, allow_redirects=True)
                except requests.RequestException as e:
                    r = None
                    err = e
            if r is not None and r.status_code == 200:
                return r
            if r is not None and r.status_code in (404, 410):
                return r
            # 429·5xx·네트워크 오류 — 잠깐 물러난다
            time.sleep(2.0 * (attempt + 1))
        return r


# ─── 매장 하나 ───

@dataclass
class Shop:
    slug: str
    base: str                     # https://host
    brand_gender: str
    robots: RobotFileParser | None = None
    categories: dict[int, str] = field(default_factory=dict)      # cate_no → 이름
    membership: dict[int, set] = field(default_factory=dict)      # product_no → {cate_no}
    product_urls: dict[int, str] = field(default_factory=dict)    # product_no → 상세 URL
    enumerated_by: str = ""
    errors: list[str] = field(default_factory=list)
    list_paths: set = field(default_factory=lambda: {"/product/list.html"})
    failures: list[dict] = field(default_factory=list)

    def allowed(self, url: str) -> bool:
        if not self.robots:
            return True
        try:
            return self.robots.can_fetch(UA, url)
        except Exception:
            return True


PRODUCT_NO_IN_URL = [
    re.compile(r"product_no=(\d+)"),
    re.compile(r"/product/[^/?#]+/(\d+)/?"),
]


def product_no_of(url: str) -> int | None:
    for rx in PRODUCT_NO_IN_URL:
        m = rx.search(url)
        if m:
            return int(m.group(1))
    return None


def load_robots(http: PoliteSession, shop: Shop):
    rp = RobotFileParser()
    r = http.get(shop.base + "/robots.txt", retries=0)
    if r is not None and r.status_code == 200 and r.text.strip():
        rp.parse(r.text.splitlines())
        shop.robots = rp


def enumerate_by_sitemap(http: PoliteSession, shop: Shop) -> None:
    """사이트맵(인덱스면 하위까지)에서 상품 URL 을 모은다."""
    seen_maps: set[str] = set()
    queue = [shop.base + "/sitemap.xml"]
    while queue and len(seen_maps) < 30:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        r = http.get(sm, retries=1)
        if r is None or r.status_code != 200 or "<" not in r.text[:200]:
            continue
        # 사이트맵의 호스트가 official_url 과 다를 수 있다(badblood.co.kr → badbloodstores.com).
        # URL 은 그대로 쓴다 — 그쪽이 매장이 스스로 말하는 정본 도메인이다.
        if "<sitemapindex" in r.text[:500]:
            queue += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
            continue
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text):
            loc = htmlmod.unescape(loc)
            if "/product/" not in loc or "/product/list" in loc or "/category/" in loc.split("/product/")[-1][:0]:
                pass
            no = product_no_of(loc)
            if no and "/product/" in loc and "list.html" not in loc and "search" not in loc:
                shop.product_urls.setdefault(no, loc)
    if shop.product_urls:
        shop.enumerated_by = "sitemap"
    # 사이트맵은 다 담지 않는다 — 9999archive 는 협업 상품 5개가 사이트맵에 없고 홈·카테고리에만
    # 있었다(2026-09-02 실측, lastmod 가 8/24 라 그 뒤 올라온 것). 그래서 아래 카테고리 목록·홈
    # 링크와 합집합을 만든다. 상품 하나가 두 경로에서 나와도 product_no 로 하나다.


def harvest_product_links(html_text: str, shop: Shop) -> set[tuple[int, str]]:
    """페이지 안의 상품 링크 전부 — (product_no, 절대 URL). 홈·목록 공용."""
    found = set()
    for href in re.findall(r'href="([^"]*)"', html_text):
        if "/product/" in href and "list.html" not in href and (
            "product_no=" in href or re.search(r"/product/[^/]+/\d+/", href)
        ):
            no = product_no_of(href)
            if no:
                clean = htmlmod.unescape(href).split("?")[0] if "product_no=" not in href else htmlmod.unescape(href)
                found.add((no, urljoin(shop.base, clean)))
    return found


def load_categories(http: PoliteSession, shop: Shop, soup: BeautifulSoup, html_text: str = "") -> None:
    for a in soup.select('a[href*="cate_no="]'):
        href = a.get("href", "")
        m = re.search(r"cate_no=(\d+)", href)
        text = a.get_text(" ", strip=True)
        if m and text and len(text) <= 40:
            shop.categories.setdefault(int(m.group(1)), text)
    # 목록 페이지 이름이 list.html 이 아닌 매장이 있다 — pogservice 는 list2.html, matinkim 은
    # /product/kimmatin/list.html 을 같이 쓴다. 홈 링크에서 본 경로를 전부 후보로 둔다.
    for m in re.finditer(r'href="((?:https?://[^/"]+)?(/product/(?:[^/?"]+/)?list\w*\.html))\?[^"]*cate_no=', html_text):
        shop.list_paths.add(m.group(2))
    # 내비에 없는 카테고리가 상품 링크의 /category/N/ 에 숨어 있다(9999archive 의 협업 카테고리 1).
    for m in re.finditer(r"/product/[^/\"]+/\d+/category/(\d+)/", html_text):
        shop.categories.setdefault(int(m.group(1)), f"cate_{m.group(1)}")
    # 홈에 걸린 상품 링크도 후보다
    for no, href in harvest_product_links(html_text, shop):
        shop.product_urls.setdefault(no, href)


# 임직원·사내·관계자 전용 칸은 손님이 살 수 있는 상품이 아니다. 목록에서 아예 뺀다(사람 결정 2026-09-04).
PRIVATE_CATE = re.compile(r"임직원|직원|사내|스태프|관계자|가족|비공개|staff|employee|internal|wholesale|b2b", re.I)


def crawl_category_lists(http: PoliteSession, shop: Shop, max_pages: int = 80) -> None:
    """목록을 훑어 product_no → 카테고리 소속을 얻는다. 사이트맵이 없었으면 상품 URL 도 여기서 채운다."""
    pending = sorted(shop.categories)
    visited: set[int] = set()
    while pending:
        cate_no = pending.pop(0)
        if cate_no in visited:
            continue
        visited.add(cate_no)
        name = shop.categories.get(cate_no, "")
        if name and PRIVATE_CATE.search(str(name)):
            shop.errors.append(f"임직원 전용으로 보여 건너뜀: {name} (cate_no={cate_no})")
            continue
        for list_path in sorted(shop.list_paths):
            _crawl_one_list(http, shop, cate_no, list_path, max_pages)
        pending += [c for c in shop.categories if c not in visited and c not in pending]
    if shop.product_urls:
        shop.enumerated_by = (shop.enumerated_by + "+lists") if shop.enumerated_by else "category-lists"


def _crawl_one_list(http: PoliteSession, shop: Shop, cate_no: int, list_path: str, max_pages: int) -> None:
        page = 1
        seen_here: set[int] = set()
        while page <= max_pages:
            url = f"{shop.base}{list_path}?cate_no={cate_no}&page={page}"
            if not shop.allowed(url):
                shop.errors.append(f"robots disallow cate_no={cate_no}")
                break
            r = http.get(url, retries=1)
            if r is None or r.status_code != 200:
                break
            # <title> 이 카테고리 이름을 더 정확히 말해 준다("Outerwears - MATINKIM")
            if page == 1:
                m = re.search(r"<title>\s*([^<]+?)\s*(?:-|\||·)\s*[^<]*</title>", r.text)
                if m and 1 < len(m.group(1)) <= 40:
                    shop.categories[cate_no] = m.group(1).strip()
            links = harvest_product_links(r.text, shop)
            # 목록 페이지에 하위 카테고리 링크가 더 있으면 그것도 훑는다
            for m in re.finditer(r'href="[^"]*cate_no=(\d+)[^"]*"[^>]*>\s*([^<]{1,40}?)\s*<', r.text):
                shop.categories.setdefault(int(m.group(1)), m.group(2).strip())
            new = {no for no, _ in links} - seen_here
            if not new:
                break
            for no, href in links:
                shop.membership.setdefault(no, set()).add(cate_no)
                # 합집합이되 목록 URL 이 이긴다 — 목록은 지금 살아 있는 페이지에서 왔고, 사이트맵은
                # 옛 주소를 들고 있을 수 있다(lmood: http:// 주소 86개가 전부 404, 2026-09-02).
                shop.product_urls[no] = href
            seen_here |= new
            page += 1


# ─── 상세 파싱 ───

def _js_str(html_text: str, var: str) -> str | None:
    m = re.search(rf"var\s+{var}\s*=\s*'((?:[^'\\]|\\.)*)'", html_text)
    if not m:
        m = re.search(rf'var\s+{var}\s*=\s*"((?:[^"\\]|\\.)*)"', html_text)
    if not m:
        return None
    raw = m.group(1)
    try:
        return json.loads(f'"{raw}"')  # \uXXXX·\' 해석
    except Exception:
        return raw.replace("\\'", "'")


def _fix_url(u: str, base: str) -> str:
    if not u:
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return urljoin(base, u)
    return u


SIZE_LABELS = (r"(총\s*장|총\s*기장|기장|어깨\s*너비|어깨|가슴\s*단면|가슴|소매\s*길이|소매|화장|암홀|허리\s*단면|허리|밑위|"
               r"허벅지\s*단면|허벅지|밑단\s*단면|밑단|엉덩이|힙|sleeve\s*length|total\s*length|shoulder\s*width|chest\s*width|"
               r"length|shoulder|chest|sleeve|waist|hip|thigh|hem|rise|inseam)")
# 「Length - 61cm Shoulder - 55cm」(anotheryouth)처럼 붙임표로 잇는 표기도 읽는다
SIZE_RX = re.compile(SIZE_LABELS + r"\s*(?:\([^)]{0,20}\))?\s*[:：\-–—]?\s*((?:\d{1,4}(?:\.\d)?\s*(?:cm|mm)?\s*[/,|]?\s*){1,8})", re.I)


def parse_json_ld_product(html_text: str) -> dict:
    """<script type="application/ld+json"> 의 Product. cafe24 가 매장마다 넣어 준다(실측 11곳 중 10곳 —
    dnsr 만 없음). 이름·설명·가격·재고(availability)가 스킨과 무관하게 깨끗하게 들어 있어서,
    HTML 을 뒤지는 것보다 먼저 본다. lmood 의 「상품 설명」 아코디언은 HTML 에 없고 여기에만 있었다."""
    for raw in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.S):
        try:
            j = json.loads(raw)
        except Exception:
            continue
        items = j if isinstance(j, list) else [j]
        for it in items:
            if isinstance(it, dict) and it.get("@type") == "Product":
                return it
    return {}


def extract_size_table(html_text: str) -> dict[str, list[float]]:
    """사이즈 실측 — 총장·어깨·가슴… 뒤에 오는 숫자 묶음. 표(th/td)든 목록(ul/li)이든 스크립트 문자열
    안이든(lmood) 태그를 벗기고 글자 흐름에서 잡는다. 사이즈 이름(44/46/48)은 안 잡고 값의 순서만 남긴다 —
    옷장에서 실측을 쓸 때 정리한다. 매장마다 표가 달라 지금 컬럼화하지 않는다(2026-09-02 결정)."""
    t = re.sub(r'<script type="application/ld\+json">.*?</script>', " ", html_text, flags=re.S)
    t = htmlmod.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t\r\n]+", " ", t)
    # 밀리미터로 적는 매장(mischief 「SIZE(mm) S 허리 345 기장 1020」)은 10 으로 나눈다.
    mm = bool(re.search(r"(?:size|사이즈|단위)\s*[（(]?\s*mm\s*[)）]?", t, re.I))
    lo, hi = (30, 2000) if mm else (3, 200)
    rows: dict[str, list[float]] = {}
    for m in SIZE_RX.finditer(t):
        label = re.sub(r"\s+", "", m.group(1)).lower()
        nums = [float(x) for x in re.findall(r"\d{1,4}(?:\.\d)?" if mm else r"\d{1,3}(?:\.\d)?", m.group(2))]
        nums = [n / 10 if mm else n for n in nums if lo <= n <= hi]
        if nums and label not in rows:
            rows[label] = nums[:8]
    if len(rows) < 2:
        mat = extract_size_matrix(t)
        if len(mat) > len(rows):
            rows = mat
    return rows


# 행렬 표 — 머리 줄에 라벨, 다음 줄부터 「사이즈 이름 + 숫자 N개」. rssc 의 ul.size_guide 가 이 모양이다
# (「size(cm) Length Shoulder Chest Sleeve.L / 1size 61 50 59 59」, 2026-09-03 사람 발견). SIZE_RX 는 「라벨 뒤
# 숫자」만 봐서 이 표를 통째로 놓쳤다(rssc 370건 0%). 머리는 「size(cm)」 같은 표식 뒤에서 첫 사이즈 행이
# 나오기 전까지의 낱말 전부다 — 모르는 낱말(Crotch)이 섞여도 자리를 지켜야 숫자가 옆 칸으로 밀리지 않는다.
_SIZE_TOKEN = r"(?:xxs|xs|s|m|l|xl|xxl|2xl|3xl|free|f|os|one\s*size|\d{1,3}\s*size|\d{1,3})"
# 「▪ Size (Length / Chest / Shoulder / Arm) 2: 64 / 58.5 / 51 / 65.5」(9999archive 본문 글, 2026-09-03 사람 발견)도 여기서 잡는다
# 「단위(=cm)」(perenn)·「단위 : cm」처럼 괄호 안에 = 나 : 가 끼는 표기도 표의 시작으로 본다.
_HEADER_MARK = re.compile(r"(?:size\s*\(\s*cm\s*\)|사이즈\s*\(\s*cm\s*\)|size\s*guide\s*\(?\s*cm\s*\)?|\(\s*[=:]?\s*cm\s*\)|단위\s*[（(]?\s*[=:]?\s*cm\s*[)）]?|단위\s*[:：]\s*cm|size\s*\(cm\)|사이즈\s*표|size\s*chart|size\s*guide|size\s*info|size\s*cm\b|size\s*(?=\()|사이즈\s*(?=\())", re.I)
# 두 낱말짜리 라벨 — 공백으로 쪼개면 칸 수가 어긋난다(depound 「SLEEVE LENGTH」, badblood 「어깨 너비」)
_COMPOUND = [("sleeve", "length"), ("소매", "길이"), ("어깨", "너비"), ("가슴", "단면"), ("허리", "단면"), ("밑단", "단면"), ("허벅지", "단면"), ("total", "length"), ("shoulder", "width")]
_NUM = r"\d{1,3}(?:\.\d)?"
# 사이즈 이름 뒤에 올 수 있는 것: 「(여성용)」「size small」「|」「:」 — badblood·rough-side(2026-09-04)
_ROW_LEAD = r"(?:\s*(?:size)?\s*(?:small|medium|large|x-?small|x-?large|free)?\s*(?:\([^)]{0,12}\))?\s*[:：\-|]?\s*)"
_KNOWN = re.compile(r"^(?:" + SIZE_LABELS[1:-1] + r"|crotch|inseam|rise|arm|암홀|밑위|가슴둘레|허리둘레|밑단둘레|어깨너비|소매길이|가슴단면)", re.I)


def extract_size_matrix(t: str) -> dict[str, list[float]]:
    best: dict[str, list[float]] = {}
    for h in _HEADER_MARK.finditer(t):
        # 머리: 표식 뒤 낱말들(숫자 아닌 토큰) — 첫 사이즈 행(이름 + 숫자)이 시작되는 곳까지, 최대 10개
        # 낱말은 통째로 먹는다(공백 없이) — 「Hem」의 m 을 사이즈 M 으로 잘라 읽던 버그(2026-09-04)
        m = re.match(r"\s*((?:[A-Za-z가-힣(][A-Za-z가-힣.()]*\s*/?\s*){1,12}?)(?<![A-Za-z가-힣])(?=" + _SIZE_TOKEN + _ROW_LEAD + _NUM + r")", t[h.end():], re.I)
        if not m:
            continue
        head = m.group(1).strip().strip("()")
        if "/" in head:
            words = [w.strip(" ()") for w in head.split("/") if w.strip(" ()")]       # 「총장 / 어깨 너비 / 소매 길이」
        else:
            words = [w for w in re.split(r"\s+", head) if w]
            merged, i = [], 0
            while i < len(words):
                pair = (words[i].lower(), words[i + 1].lower()) if i + 1 < len(words) else None
                if pair in _COMPOUND:
                    merged.append(words[i] + words[i + 1]); i += 2
                else:
                    merged.append(words[i]); i += 1
            words = merged
        words = [w for w in words if w.lower() not in ("size", "사이즈", "cm", "size(cm)")]   # 머리의 「SIZE」 칸은 라벨이 아니다
        labels = [re.sub(r"[().\s]", "", w).lower() for w in words]
        labels = [re.sub(r"l(?:ength)?$", "", l) if l.startswith("sleeve") and l != "sleeve" else l for l in labels]
        if sum(1 for l in labels if _KNOWN.match(l)) < 2:
            continue
        n = len(labels)
        row_rx = re.compile(r"\s*(" + _SIZE_TOKEN + r")" + _ROW_LEAD + r"((?:" + _NUM + r"\s*(?:cm)?\s*[/,|]?\s*){" + str(n - 1) + r"}" + _NUM + r")(?!\d)", re.I)
        cols: dict[str, list[float]] = {l: [] for l in labels}
        pos = h.end() + m.end()
        got = 0
        while got < 8:
            r = row_rx.match(t, pos)
            if not r:
                break
            nums = [float(x) for x in re.findall(_NUM, r.group(2))][:n]
            if len(nums) < n or not all(3 <= x <= 200 for x in nums):
                break
            for l, x in zip(labels, nums):
                cols[l].append(x)
            got += 1
            pos = r.end()
        if got and got >= len(next(iter(best.values()), [])):
            best = {l: v for l, v in cols.items() if v and _KNOWN.match(l)}
    return best


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_detail(html_text: str, url: str, shop: Shop) -> dict | None:
    soup = BeautifulSoup(html_text, "lxml")
    ld = parse_json_ld_product(html_text)
    ld_offer = ld.get("offers") or {}
    if isinstance(ld_offer, list):
        ld_offer = ld_offer[0] if ld_offer else {}
    no = None
    m = re.search(r"iProductNo\s*=\s*(\d+)", html_text)
    if m:
        no = int(m.group(1))
    if no is None:
        no = product_no_of(url)
    if no is None:
        return None

    # 이름 — JS 변수가 가장 믿을 만하다. og:title 은 매장 이름을 넣는 곳이 있다(9999archive).
    name = _strip_tags(ld.get("name", "")) or None
    if not name:
        name = _js_str(html_text, "product_name")
    if not name:
        inp = soup.select_one('input[name="product_name"]')
        name = inp.get("value") if inp else None
    if not name:
        h = soup.select_one(".headingArea h2, .infoArea h2, .xans-product-detail h2, h2.title")
        name = h.get_text(" ", strip=True) if h else None
    if not name:
        t = soup.title.get_text(strip=True) if soup.title else ""
        name = re.split(r"\s+[-|]\s+", t)[0] if t else None
    if not name:
        return None
    name = _strip_tags(name)

    # 판매가 — product_price(정가). product_sale_price(할인가)는 의도적으로 안 본다.
    price = None
    p = _js_str(html_text, "product_price")
    if p and p.strip().isdigit():
        price = int(p)
    if price is None and str(ld_offer.get("price", "")).replace(".", "").isdigit():
        price = int(float(ld_offer["price"]))
    if price is None:
        span = soup.select_one("#span_product_price_text")
        if span:
            digits = re.sub(r"[^\d]", "", span.get_text())
            price = int(digits) if digits else None

    soldout = False
    m = re.search(r"(?:is_)?soldout_icon\s*=\s*'(\w)'", html_text)
    if m:
        soldout = m.group(1) == "T"
    elif soup.select_one('img[alt="품절"], .ec-product-soldout:not(.displaynone)'):
        soldout = True
    if "OutOfStock" in str(ld_offer.get("availability", "")) or "SoldOut" in str(ld_offer.get("availability", "")):
        soldout = True

    # og:image 를 그대로 믿으면 안 된다 — 사이트 공용 공유 이미지(LINK_0831.jpg, logo.svg,
    # share-image…)를 og:image 로 박아 둔 스킨이 다섯 곳(dunst 1,759건 전부 같은 그림).
    # 1차 크롤에서 그 탓에 4,600행이 「같은 사진」으로 묶여 빠졌다(2026-09-02).
    # 상품 사진은 언제나 /web/product/ 아래에 있으니 그 조건을 먼저 본다.
    ogs = [_fix_url(m.get("content", ""), shop.base) for m in soup.select('meta[property="og:image"]')]
    image = next((u for u in ogs if "/web/product/" in u), "")
    if not image:
        big = soup.select_one('img[src*="/web/product/big/"], .keyImg img, .bigImage img, .xans-product-image img, .prdImgView img, #big_img_box img')
        image = _fix_url(big.get("src", "") if big else "", shop.base)
    if not image:
        image = ogs[0] if ogs else ""

    gallery = []
    for img in soup.select('img[src*="/web/product/extra/"], img[src*="/web/product/medium/"], img[src*="/web/product/small/"]'):
        src = _fix_url(img.get("src", ""), shop.base)
        src = re.sub(r"/web/product/(extra/)?(small|medium|tiny)/", lambda mm: f"/web/product/{mm.group(1) or ''}big/", src)
        if src and src != image and src not in gallery:
            gallery.append(src)

    # canonical 이 홈을 가리키는 스킨이 있다(anderssonbell.com → 664건 전부 홈, badblood, haleine).
    # 상품 식별자가 없는 canonical 은 버리고 실제로 연 주소를 쓴다.
    canon = soup.select_one('link[rel="canonical"]')
    canon_href = htmlmod.unescape(canon.get("href")) if canon and canon.get("href") else ""
    source_url = canon_href if product_no_of(canon_href) else url

    # ── 상세 설명 ──
    # cafe24 상세는 대개 #prdDetail 에 「이미지」로 들어 있고 글은 거의 없다(matin-kim 1,170건 글 0자).
    # 글이 있는 곳은 셋으로 갈린다:
    #   · 기본정보 표(.xans-product-detaildesign table) — 상품명·판매가·상품요약정보·소재·제조국 같은
    #     열쇠:값. 표로 따로 뽑는다(spec).
    #   · 본문(#prdDetail, .xans-product-additional 의 desc/img-list 블록) — 있으면 글, 없으면 이미지.
    #   · #prdInfo — 교환·반품 안내라 설명이 아니다. 뺀다.
    # 이미지로만 설명하는 매장은 그 이미지 URL 을 detail_images 로 남긴다 — OCR 의 입력이다.
    # 표(기본정보·사이즈표) → 열쇠:값. 폼 라벨(「[필수] 옵션을 선택」·「월 렌탈 금액」·「상품수」)과
    # 가격·배송·결제 행은 설명이 아니라 뺀다. 남는 건 상품요약정보·description·소재·제조국·
    # 사이즈 실측(총장·어깨·가슴…) 같은 사실이다.
    NOISE_KEY = re.compile(r"판매가|할인|적립|수량|배송|결제|무이자|해외배송|상품코드|쿠폰|구매|SNS|PRICE|제휴|총 상품|최소주문|최대주문|"
                           r"렌탈|옵션|\[필수\]|SIZE=|상품수|리뷰|문의|Q&A|REVIEW|배송비|국내", re.I)
    NOISE_VAL = re.compile(r"\[필수\]|선택해 주세요|^상품수$|개월 기준|^-+$")
    # 매장이 모든 상품에 똑같이 붙이는 「사이즈 환산표」(KOREA/US/JP/EU/UK…)는 상품 실측이 아니다.
    # 두면 spec 이 「KOREA: 44 (85)」 같은 것으로 차고, size_table 에 엉뚱한 허리 21~32 가 들어간다
    # (open-yy 742벌 중 204벌, 2026-09-04). 다만 같은 SIZE GUIDE 칸 안에 진짜 실측표가 함께 있으므로
    # 칸 전체가 아니라 「환산표인 표」만 걷어낸다 — 나라 이름이 셋 이상 든 표.
    _CONV = ("korea", "us", "uk", "eu", "jp", "cn", "asia", "inch")
    for tb in soup.select("table"):
        head = " ".join(c.get_text(" ", strip=True).lower() for c in tb.find_all(["th", "caption"])[:24])
        if sum(1 for w in _CONV if re.search(rf"\b{w}\b", head)) >= 3:
            tb.decompose()

    spec: dict[str, str] = {}
    for tr in soup.select(".xans-product-detaildesign tr, .xans-product-detail tr, .xans-product-additional tr, #prdDetail tr, #details tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        k = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip(" :：|")
        v = re.sub(r"\s+", " ", cells[1].get_text(" ", strip=True))
        if not k or not v or len(k) > 16 or len(v) > 300 or NOISE_KEY.search(k) or NOISE_VAL.search(v) or k == v:
            continue
        spec.setdefault(k, v)

    # 본문 글 — 표·스크립트·안내 블록을 뺀 나머지. 기본정보 표(.xans-product-detaildesign)는
    # 글이 아니라 표라 spec 으로만 간다. 결제·배송·교환 안내(dunst 「고액결제의 경우…」 4,000자)와
    # 탭 이름 나열(kirsh 「상품상세정보 상품구매안내 상품사용후기」)은 설명이 아니다.
    POLICY = re.compile(r"교환\s*및\s*반품|반품\s*주소|환불|배송\s*(안내|기간|비|방법)|고액결제|무통장|카드사|주문\s*(취소|보류)|"
                        r"상품구매안내|상품사용후기|상품Q&A|관련상품|RETURN|EXCHANGE|SHIPPING|DELIVERY|월 렌탈|게시물이 없습니다|View All|"
                        r"리뷰 작성|글읽기 권한|성인인증|Related Items|Out of stock|게시글 신고|신고사유|상품결제정보")

    def _clean_text(el) -> str:
        el = BeautifulSoup(str(el), "lxml")
        for t in el.select("table, script, style, select, button, .xans-product-detailinfo, .xans-product-action"):
            t.decompose()
        return re.sub(r"\s+", " ", el.get_text(" ", strip=True))

    def _collect(el, out: list[str], depth: int = 0) -> None:
        """정책 문단만 걷어낸다. 예전엔 정책 낱말이 하나라도 있으면 요소 전체를 버렸는데, badblood 의 Details·Size Guide·
        Delivery 가 한 아코디언에, rough-side 의 제품 설명·사이즈 가이드·배송&반품이 #prdInfo 탭에 함께 있어 설명과
        사이즈 표까지 통째로 사라졌다(2026-09-04 사람 발견). 정책이 섞인 요소는 자식으로 내려가 정책 없는 가지만 남긴다."""
        t = _clean_text(el)
        if len(t) < 15:
            return
        if not POLICY.search(t):
            if t not in out and not any(t in p for p in out):
                out.append(t)
            return
        kids = [k for k in el.find_all(recursive=False) if k.name]
        if depth >= 8 or not kids:
            return
        for k in kids:
            _collect(k, out, depth + 1)

    parts: list[str] = []
    # 매장마다 상세를 담는 그릇이 다르다. 아코디언·탭이라도 글은 HTML 에 이미 있어 브라우저가 필요 없다
    # (사람이 화면으로 확인해 준 곳: open-yy·divein·rough-side·coor — 2026-09-04).
    for sel in ("#prdDetail", "#details", ".xans-product-additional", ".product-detail-block", ".xans-product-detaildesign", "#prdInfo",
                ".more-info-content", ".more-infos",          # open-yy: 아코디언 DETAILS(소재·핏·혼용률)
                ".accordion-cont", ".md-info-accordion",      # divein: 디테일 + SIZE(cm) 표
                ".accordion-desc", ".accordion-list",         # lecyto: PRODUCT INFO(혼용률) + SIZE GUIDE
                ".prd-detail-desc-list", ".size-guide",       # rough-side: 제품 설명 탭 + 사이즈 가이드 탭
                ".detailArea"):                               # coor: 상품간략설명 + Detail 실측
        for el in soup.select(sel):
            _collect(el, parts)
    body_text = " ".join(parts)
    ld_text = _strip_tags(ld.get("description", ""))
    if POLICY.search(ld_text):
        ld_text = ""
    # JSON-LD 설명이 100자 넘으면 그게 본문이다 — 매장이 상품마다 써 넣은 글이라서. HTML 본문은
    # 매장 공용 안내(lmood 「제품관리… 캐시미어」 592자)가 섞여 더 길어도 본문이 아닐 수 있다.
    # 2026-09-03 표본(12 브랜드): JSON-LD 가 100~280자 요약일 때 본문 글은 600~5,700자였다(dunst 209→5,713,
    # 9999archive 는 소재·사이즈·디테일이 전부 본문 글). 그래서 본문이 요약의 2배 이상이고 300자 넘으면 본문을
    # 설명으로 쓴다. 본문 글은 어느 쪽을 골랐든 detail_text 로 따로 남긴다 — 태거가 둘 다 읽는다.
    if body_text and len(body_text) >= max(300, 2 * len(ld_text)):
        description = body_text[:4000]
    else:
        description = (ld_text if len(ld_text) >= 100 or len(ld_text) >= len(body_text) else body_text)[:4000]
    description_source = "json-ld" if description == ld_text[:4000] and ld_text else ("html" if body_text else "")
    detail_text = body_text[:4000]
    size_table = extract_size_table(str(soup))   # 환산표를 걷어낸 문서에서 — 위 decompose 참고

    # 상세 이미지 — 이미지로만 설명하는 매장(matin-kim 1,170건 글 0자)의 설명은 여기 들어 있다.
    # OCR(scripts/ocr_detail_images.py)의 입력. 대표컷·갤러리·아이콘·스킨 자산은 뺀다.
    SKIP_IMG = re.compile(r"\.(gif|svg)(\?|$)|/ico_|icon|btn_|logo|blank|spacer|txt_naver|sizeguide|img\.echosting\.cafe24\.com|/skin/|badge|arrow|\.png\?v=", re.I)
    detail_images: list[str] = []
    for el in soup.select("#prdDetail, #details, .xans-product-detaildesign, .xans-product-additional, .product-detail-block, .xans-product-detail, "
                          ".more-info-content, .accordion-cont, .accordion-desc, .prd-detail-desc-list, .detailArea"):
        for img in el.select("img"):
            src = img.get("ec-data-src") or img.get("data-src") or img.get("data-original") or img.get("src") or ""
            src = _fix_url(src.strip(), shop.base)
            if not src or SKIP_IMG.search(src):
                continue
            if "/web/product/" in src and re.search(r"/(medium|small|tiny)/", src):
                continue  # 갤러리 축소본
            if "/web/product/" in src and "/big/" in src and (src in gallery or src == image):
                continue  # 대표컷·갤러리 — 이미 가진다. (frizmworks 처럼 /web/product/big/…_size.jpg 에 사이즈표를 두는 매장은 남긴다)
            if src not in detail_images and src != image and src not in gallery:
                detail_images.append(src)

    # 사이즈 가이드 이미지 — 상세 영역 밖(팝업·숨긴 div)에 두는 매장(andersson-bell: /up/24ss1/size/24ss1_67.jpg). 문서 전체에서 경로에 size 가 든 그림을 앞에 넣는다.
    SIZE_IMG = re.compile(r"(?:^|[/_\-])size(?:[/_.\-]|guide|chart|info|table)", re.I)
    for img in soup.select("img"):
        src = img.get("ec-data-src") or img.get("data-src") or img.get("data-original") or img.get("src") or ""
        src = _fix_url(src.strip(), shop.base)
        if not src or not SIZE_IMG.search(src) or re.search(r"btn_|icon|ico_|\.(gif|svg|png)(\?|$)", src, re.I):
            continue
        if src not in detail_images and src != image and src not in gallery:
            detail_images.insert(0, src)

    options = []
    for opt in soup.select('select[id^="product_option_id"] option, select[name^="option"] option'):
        v = opt.get_text(" ", strip=True)
        if v and not v.startswith("*") and v not in ("- [필수] 옵션을 선택해 주세요 -", "-------------------") and len(v) < 60:
            options.append(v)

    return {
        "product_no": no,
        "name": name,
        "price": price,
        "soldout": soldout,
        "image_url": image,
        "gallery": gallery[:12],
        "source_url": source_url,
        "description": description,
        "description_source": description_source,
        "detail_text": detail_text,
        "size_table": size_table,
        "spec": spec,
        "detail_images": detail_images[:40],
        "options": options[:30],
    }


# ─── 브랜드 하나 전체 ───

def crawl_brand(http: PoliteSession, shop: Shop, refresh: bool, log, refetch_ids: set[int] | None = None) -> dict:
    out_path = CRAWL_DIR / f"{shop.slug}.jsonl"
    done: dict[int, dict] = {}
    if out_path.exists() and not refresh:
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
                done[d["product_no"]] = d
            except Exception:
                pass

    load_robots(http, shop)
    home = http.get(shop.base + "/", retries=1)
    if home is None or home.status_code != 200:
        shop.errors.append(f"home {getattr(home, 'status_code', 'ERR')}")
        return {"slug": shop.slug, "ok": False, "errors": shop.errors}
    # 리다이렉트로 정본 도메인이 바뀌면(badblood.co.kr → badbloodstores.com) 그쪽을 base 로.
    final = urlparse(home.url)
    shop.base = f"{final.scheme}://{final.netloc}"
    load_categories(http, shop, BeautifulSoup(home.text, "lxml"), home.text)

    # 회원 전용으로 이미 확인된 상품은 다시 열지 않는다(사람 결정 2026-09-04)
    mo_path = CRAWL_DIR / "_members_only.json"
    mo_all = json.loads(mo_path.read_text(encoding="utf-8")) if mo_path.exists() else {}
    members_only: set[int] = set(mo_all.get(shop.slug, []))
    if members_only:
        log(f"[{shop.slug}] 회원 전용 {len(members_only)}건은 건너뛴다")

    if refetch_ids is not None:
        # 지정한 상품만 다시 받는다 — 설명·상세 이미지·주소를 새 파서로 채우는 용도(2026-09-02).
        # 저장된 주소에 식별자가 있으면 그 주소, 없으면 cafe24 공통 주소(detail.html?product_no=).
        todo = []
        for no in sorted(refetch_ids):
            # 이미 새 파서로 받은 행(size_table 필드가 있다)은 건너뛴다 — 중간에 멈춰도 이어서 간다.
            if "size_table" in done.get(no, {}):
                continue
            prev = done.get(no, {}).get("source_url", "")
            todo.append((no, prev if product_no_of(prev) else f"{shop.base}/product/detail.html?product_no={no}"))
        # 카테고리 소속은 옛 행에서 이어받는다(목록을 다시 훑지 않는다)
        for no, d in done.items():
            for c in d.get("category_nos", []):
                shop.membership.setdefault(no, set()).add(c)
                shop.categories.setdefault(c, (d.get("category_names") or [str(c)])[d["category_nos"].index(c)])
        shop.enumerated_by = "refetch"
    else:
        enumerate_by_sitemap(http, shop)
        crawl_category_lists(http, shop)
        todo = [(no, u) for no, u in shop.product_urls.items() if no not in done and no not in members_only]
    log(f"[{shop.slug}] 카테고리 {len(shop.categories)} · 상품 URL {len(shop.product_urls)} ({shop.enumerated_by}) · 받을 것 {len(todo)} · 이미 {len(done)}")

    fetched = 0
    failed = 0
    # --refresh 는 파일을 비우고 새로 쓴다. append 로 두면 옛 행이 남아 두 배가 된다
    # (2026-09-02 실측 — dunst 1,759 + 2,452 = 4,133행, 옛 행은 사진이 공용 이미지).
    with out_path.open("w" if refresh else "a", encoding="utf-8") as f:
        for no, url in todo:
            if not shop.allowed(url):
                failed += 1
                continue
            r = http.get(url, retries=2)
            if r is not None and r.status_code in (404, 410) and "product_no=" not in url:
                # 사이트맵 주소가 죽었어도 번호로는 열리는 경우가 있다
                url = f"{shop.base}/product/detail.html?product_no={no}"
                r = http.get(url, retries=1)
            if r is None or r.status_code != 200:
                failed += 1
                shop.failures.append({"product_no": no, "url": url, "reason": f"http {getattr(r, 'status_code', 'ERR')}"})
                continue
            if len(r.text) < 2000 and "member/login" in r.text:
                # 회원 전용 상품 — badblood 104건. 받을 수 없는 게 맞고, 다음부터는 열지도 않는다
                # (사람 결정 2026-09-04). _members_only.json 에 남겨 다음 실행이 건너뛴다.
                failed += 1
                members_only.add(no)
                shop.failures.append({"product_no": no, "url": url, "reason": "members-only"})
                continue
            d = parse_detail(r.text, url, shop)
            if not d or not d.get("price"):
                # 이름·가격이 없으면 상품이 아니다(룩북·안내 페이지가 /product/ 에 들어 있는 매장이 있다)
                failed += 1
                shop.failures.append({"product_no": no, "url": url, "reason": "no-name" if not d else "no-price", "bytes": len(r.text)})
                continue
            cates = sorted(shop.membership.get(no, set()))
            d["category_nos"] = cates
            d["category_names"] = [shop.categories.get(c, str(c)) for c in cates]
            d["brand_slug"] = shop.slug
            d["crawled_at"] = datetime.now(KST).strftime("%Y-%m-%dT%H:%M:%S")
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
            f.flush()
            done[no] = d
            fetched += 1
            if fetched % 100 == 0:
                log(f"[{shop.slug}] … {fetched}/{len(todo)}")

    if shop.failures:
        (CRAWL_DIR / f"_failures_{shop.slug}.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in shop.failures) + "\n", encoding="utf-8")
    if members_only:
        mo_all[shop.slug] = sorted(members_only)
        mo_path.write_text(json.dumps(mo_all, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {
        "slug": shop.slug, "ok": True, "base": shop.base, "enumerated_by": shop.enumerated_by,
        "categories": len(shop.categories), "product_urls": len(shop.product_urls),
        "fetched_now": fetched, "failed": failed, "total_saved": len(done),
        "soldout": sum(1 for d in done.values() if d.get("soldout")),
        "errors": shop.errors[:5],
        "failure_reasons": dict(collections.Counter(x["reason"] for x in shop.failures)),
    }
    log(f"[{shop.slug}] 끝 — 저장 {len(done)} (품절 {summary['soldout']}) · 실패 {failed}")
    return summary


# ─── jsonl → CSV (products_seed.csv 와 같은 열 + 몇 개) ───

CSV_FIELDS = [
    "brand_slug", "category_code", "item_type", "name", "gender_target", "price",
    "representative_color", "season", "status", "image_url", "source_url", "crawled_at",
    "category", "subtype",
    # 이하 추가 열 — 기존 파이프라인은 무시한다
    "product_no", "category_path", "gallery_count", "options",
    # 주간 갱신(weekly_update.py)이 채운다 — 매장이 내린 상품(연속 2주 목록에서 사라지고 상세 404). 지난 상품에 두되 링크가 죽었다는 표시
    "delisted", "last_seen",
]
CATEGORY_LABEL = {"tops": "Tops", "outer": "Outerwear", "bottoms": "Pants", "dress": "Dresses", "skirt": "Skirts",
                  "shoes": "Shoes", "bags": "Bags", "accessories": "Accessories", "suiting": "Suiting", "other": ""}


def build_csv(brand_gender: dict[str, str]) -> tuple[int, dict]:
    rows = []
    per_brand: dict[str, int] = {}
    seen_images: set[str] = set()
    dropped_dupe = 0
    dropped_noimg = 0
    dropped_junk = 0
    for path in sorted(CRAWL_DIR.glob("*.jsonl")):
        if path.name.startswith("_"):
            continue
        slug = path.stem
        # 같은 product_no 가 여러 줄이면 마지막 줄이 이긴다 — 다시 받은 행이 뒤에 붙는다.
        latest: dict[int, dict] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            latest[d["product_no"]] = d
        # 사이트 공용 이미지 판정 — 한 브랜드 안에서 같은 그림을 여러 상품이 물면 그건 상품 사진이
        # 아니다(dunst 의 LINK_0831.jpg 1,759건). 경로(/web/product/)로 가르면 depound 처럼
        # 자기 CDN(depound.cafe24.com/img/…)을 쓰는 매장의 진짜 사진 107장이 빠진다(2026-09-02).
        img_uses = collections.Counter(d.get("image_url", "") for d in latest.values())
        for d in latest.values():
            # 상품이 아닌 행 — 룩북·캠페인 페이지가 가격 1원으로 /product/ 에 들어 있다
            # (dunst 「19 SPRING 'Here We Are'」 = 1원). build_products_seed.py 와 같은 문턱.
            if int(d.get("price") or 0) <= 1000:
                continue
            # 상품 이름을 한 개인결제·룩북 페이지 — lecyto 「박민희 실장님 팀」 217건(가격 있음),
            # insilence 「셀럽 테스트」, opus-0012 「2024 spring summer」. 이름만으로 갈린다.
            if JUNK_NAME.search(d["name"]):
                dropped_junk += 1
                continue
            img = d.get("image_url", "")
            # 열 상품 넘게 같은 그림이면 매장 공용 이미지다. 그보다 적으면 색만 다른 같은 옷이
            # 대표컷 한 장을 나눠 쓰는 것이라 아래에서 첫 행만 남긴다(컬러웨이는 대개 2~6개).
            if not img or img_uses[img] >= 10:
                # 공용 이미지면 추가 사진의 첫 장이 대표컷이다(the-museum-visitor 553건 —
                # og:image 는 사이트 공용, 상품 사진은 extra 갤러리에 평균 10장).
                gal = [g for g in d.get("gallery", []) if img_uses.get(g, 0) < 10]
                img = gal[0] if gal else ""
                d["image_url"] = img
            if not img:
                # 사진이 하나도 없는 행은 앱이 보여줄 수 없다 — 상품이 아닌 행이 대부분이다
                # (lecyto 「○○ 실장님 팀」 217건, insilence 「셀럽 테스트」 44건). jsonl 에는 남는다.
                dropped_noimg += 1
                continue
            if img in seen_images:
                dropped_dupe += 1     # 색만 다른 같은 옷이 같은 대표컷을 쓰는 경우 — 첫 행만(build_products_seed 와 같은 판단)
                continue
            else:
                seen_images.add(img)
            item = match_vocab(d["name"], ITEM_TYPE_VOCAB)
            code = classify_category(d["name"], d.get("category_names", []), d.get("description", ""))
            if not item and code == "tops" and re.search(r"스웻|스웨트|sweat", d["name"], re.I):
                item = "맨투맨"   # 「Toy Sweat」처럼 품목 단어 없이 스웻만 적은 상의 — 비니·백팩은 code 가 다르니 안 걸린다
            # 데님은 category 라벨을 따로 둔다(기존 데이터 관례: category=Denim)
            label = "Denim" if item == "데님" else ("Knitwear" if item in ("니트", "가디건") else ("Shirts" if item == "셔츠" else CATEGORY_LABEL.get(code, "")))
            rows.append({
                "brand_slug": slug,
                "category_code": code,
                "item_type": item,
                "name": d["name"],
                "gender_target": classify_gender(d.get("category_names", []), brand_gender.get(slug, "UNISEX")),
                "price": d["price"],
                "representative_color": pick_color(d["name"], d.get("description", "")),
                "season": "",
                "status": "SOLD_OUT" if (d.get("soldout") or d.get("delisted")) else "ON_SALE",
                "image_url": d["image_url"],
                # 회원 전용·리다이렉트로 홈 주소만 남은 건(badblood 208, haleine 14)은 cafe24 표준 상세 주소로 복원
                "source_url": d["source_url"] if product_no_of(d["source_url"]) else f"{d['source_url'].rstrip('/')}/product/detail.html?product_no={d['product_no']}",
                "crawled_at": d.get("crawled_at", ""),
                "category": label,
                "subtype": item,
                "product_no": d["product_no"],
                "category_path": " | ".join(d.get("category_names", [])),
                "gallery_count": len(d.get("gallery", [])),
                "options": " | ".join(d.get("options", [])),
                "delisted": "1" if d.get("delisted") else "",
                "last_seen": d.get("last_seen", ""),
            })
            per_brand[slug] = per_brand.get(slug, 0) + 1
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows), {"per_brand": per_brand, "dropped_dupe_image": dropped_dupe, "dropped_no_image": dropped_noimg, "dropped_junk_name": dropped_junk}


# ─── main ───

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*", help="slug 목록. 없으면 products_seed.csv 에 상품이 있는 브랜드 전부")
    ap.add_argument("--workers", type=int, default=8, help="동시에 볼 호스트 수(호스트 안에서는 늘 순차)")
    ap.add_argument("--delay", type=float, default=1.0, help="같은 호스트 요청 간격(초)")
    ap.add_argument("--refresh", action="store_true", help="이미 받은 상품도 다시 받는다")
    ap.add_argument("--build-csv", action="store_true", help="크롤 없이 jsonl → CSV 만")
    ap.add_argument("--refetch-ids", help="slug<TAB>product_no 목록 파일 — 그 상품만 새 파서로 다시 받는다")
    args = ap.parse_args()

    CRAWL_DIR.mkdir(parents=True, exist_ok=True)
    with BRANDS_CSV.open(encoding="utf-8-sig") as f:
        brands = {r["slug"]: r for r in csv.DictReader(f)}
    brand_gender = {s: BRAND_GENDER.get(r.get("gender", ""), "UNISEX") for s, r in brands.items()}

    if args.build_csv:
        n, info = build_csv(brand_gender)
        print(f"{n}행 → {OUT_CSV}  (사진 중복 제외 {info['dropped_dupe_image']} · 사진 없음 제외 {info['dropped_no_image']} · 상품 아닌 이름 제외 {info['dropped_junk_name']})")
        for s, c in sorted(info["per_brand"].items(), key=lambda x: -x[1]):
            print(f"{c:5d} {s}")
        return

    refetch: dict[str, set[int]] = {}
    if args.refetch_ids:
        for line in Path(args.refetch_ids).read_text(encoding="utf-8").splitlines():
            if "\t" in line:
                slug, no = line.split("\t")[:2]
                refetch.setdefault(slug, set()).add(int(no))
        slugs = sorted(refetch)
    elif args.brands:
        slugs = args.brands
    else:
        with PRODUCTS_SEED.open(encoding="utf-8-sig") as f:
            slugs = sorted({r["brand_slug"] for r in csv.DictReader(f)})
    shops = []
    for s in slugs:
        b = brands.get(s)
        if not b or not b.get("official_url"):
            print(f"[{s}] brands_seed 에 없거나 official_url 없음 — 건너뜀", file=sys.stderr)
            continue
        u = urlparse(b["official_url"].strip())
        shops.append(Shop(slug=s, base=f"{u.scheme or 'https'}://{u.netloc}", brand_gender=brand_gender.get(s, "UNISEX")))

    http = PoliteSession(delay=args.delay)
    lock = threading.Lock()
    started = time.time()

    def log(msg):
        with lock:
            print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

    summaries = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(crawl_brand, http, shop, args.refresh, log, refetch.get(shop.slug) if refetch else None): shop for shop in shops}
        for fut in as_completed(futs):
            shop = futs[fut]
            try:
                summaries.append(fut.result())
            except Exception as e:  # 한 매장이 죽어도 나머지는 계속
                log(f"[{shop.slug}] 예외: {e!r}")
                summaries.append({"slug": shop.slug, "ok": False, "errors": [repr(e)]})

    n, info = build_csv(brand_gender)
    SUMMARY_JSON.write_text(json.dumps({
        "finished_at": datetime.now(KST).isoformat(timespec="seconds"),
        "elapsed_sec": round(time.time() - started),
        "requests": http.requests_made,
        "csv_rows": n,
        "dropped_dupe_image": info["dropped_dupe_image"],
        "brands": sorted(summaries, key=lambda x: x["slug"]),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"전부 끝 — 요청 {http.requests_made} · {round(time.time() - started)}s · CSV {n}행 → {OUT_CSV}")


if __name__ == "__main__":
    main()
