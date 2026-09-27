"""칼린(carlyn) — 메이크샵. 목록 JSON 으로 상품 번호를 받고 상품 페이지를 한 벌씩 읽는다.

사이트맵이 없다(/sitemap.xml 404). 목록 화면(shopbrand.html)은 빈 틀이고 상품은 자바스크립트가
`/shop/product_list.action.html?action_mode=get_list` 로 16벌씩 받아 붙인다. 이 JSON 의 `list` 에
branduid 가 있고 `is_page_end` 가 끝을 말한다. 「SHOP」 칸(xcode=010, type=Y)이 매장 전부다
(2026-09-27 267벌 · 17쪽 — 가방이 대부분, 티셔츠·모자·키링 조금. 가르기는 뒤 단계가 한다).

상품 페이지에 필요한 것이 다 있다: JSON-LD(이름·값·사진·재고·분류) · optionJsonData(옵션별
sto_state) · .price_wrap(판매가·소비자가·소개 글) · tr.detail_info(소재·크기·제조국·실측).
본문 편집기 칸([OPENEDITOR])은 거의 비어 있어 상세 그림이 없는 상품이 대부분이다(있으면 대개
「STYLING TIP」 착용 사진 몇 장). 사이즈 옵션도 없다 — 옵션은 「컬러」 하나뿐이고, 옷은 FREE 한 벌이라
실측이 PRODUCT DETAILS 에 「총장 45 / 어깨 37 / …」 글 한 줄로 온다.
"""
import html as _html
import json
import re

from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import crawl_pages

BASE = "https://www.carlynmall.com"
PAGE_DELAY = 3.0          # robots 에 Crawl-delay 가 없다 — 기본값
LIST_API = BASE + "/shop/product_list.action.html"


def list_urls(http, log=print) -> list[str] | None:
    uids: list[str] = []
    for page in range(1, 100):
        r = http.get(f"{LIST_API}?action_mode=get_list&page={page}&xcode=010&mcode=&scode="
                     f"&type=Y&sort=manual&is_list_buy=8")
        if r is None or r.status_code != 200:
            log(f"  [carlyn] 목록 {page}쪽 받기 실패({getattr(r, 'status_code', '없음')})")
            return None      # 중간에 끊긴 목록을 다 받은 것으로 치면 뒷쪽 상품이 「목록에 없음」이 된다
        try:
            d = r.json()
        except ValueError:
            log(f"  [carlyn] 목록 {page}쪽이 JSON 이 아니다")
            return None
        got = [str(x.get("uid")) for x in d.get("list") or [] if str(x.get("uid", "")).isdigit()]
        uids += [u for u in got if u not in uids]
        if d.get("is_page_end") is True or not got:
            break
    return [f"{BASE}/shop/shopdetail.html?branduid={u}" for u in uids]


def _num(s) -> int:
    m = re.search(r"\d[\d,]*", str(s or ""))
    return int(m.group(0).replace(",", "")) if m else 0


def _abs(u: str) -> str:
    u = (u or "").strip()
    # JSON-LD 사진 주소가 「https://www.carlynmall.com//carlyn.img9.kr/…」처럼 가게 주소에 스킴 없는
    # 그림 창고 주소를 이어 붙여 온다 — 뒤쪽이 진짜 주소다
    u = re.sub(r"^https?://www\.carlynmall\.com//", "//", u)
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return BASE + u
    return u


def _img_key(u: str) -> str:
    """?1787727617 같은 캐시 꼬리를 떼고 비교한다 — 같은 그림이 두 번 들어가지 않게."""
    return u.split("?")[0]


def _jsonld(html_text: str) -> dict:
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html_text, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            return d
    return {}


_KV = re.compile(r"(\w+):'((?:[^'\\]|\\.)*)'")


def _stocks(html_text: str) -> list[dict]:
    """optionJsonData 의 재고 줄들 — 자바스크립트 객체(따옴표 없는 키)라 json 으로 못 읽어 키:값 을 줍는다.

    재고 한 줄이 {adminuser:…, opt_ids:'204', opt_values:'(KQ)다크브라운', sto_state:'SALE', …} 꼴이다.
    """
    m = re.search(r"var optionJsonData = (\{.*?\});\s*\n", html_text)
    if not m:
        return []
    out = []
    for blk in re.findall(r"\{adminuser:[^{}]*\}", m.group(1)):
        out.append({k: v.replace("\\'", "'") for k, v in _KV.findall(blk)})
    return out


def _opt_label_map(s: BeautifulSoup) -> dict[str, str]:
    """opt_id → 옵션 이름(「컬러」「사이즈」) — 숨은 입력칸 label 에 있다."""
    out = {}
    for el in s.select("input.basic_option[opt_id]"):
        out[el.get("opt_id")] = (el.get("label") or "").strip()
    for ul in s.select("ul[opt_id]"):
        dt = ul.find_previous("dt")
        out.setdefault(ul.get("opt_id"), dt.get_text(strip=True) if dt else "")
    return out


def _clean_val(v: str) -> str:
    """「(KQ)다크브라운」「(T)화이트」 — 앞의 괄호는 매장 내부 색 부호라 뗀다. 떼고 남는 게 없으면 그대로."""
    v = v.strip()
    w = re.sub(r"^\([A-Z0-9]{1,4}\)\s*", "", v)
    return w or v


_SPEC_KEYS = {"name": "품목", "material": "소재", "color": "색상", "size": "크기",
              "manufacturer": "제조사", "origin": "제조국", "made in": "제조국"}
# 상품마다 붙는 공통 안내 두 줄 — 설명 글에서 뺀다
_BOILER = re.compile(r"^-\s*(사이즈는 측정방법에 따라|제품의 색상은 모니터)")


def parse_page(html_text: str, url: str, slug: str, now: str, http=None) -> dict | None:
    m = re.search(r"branduid=(\d+)", url)
    ld = _jsonld(html_text)
    mn = re.search(r"var product_name = '((?:[^'\\]|\\.)*)'", html_text)
    name = (ld.get("name") or (mn.group(1) if mn else "")).strip()
    if not m or not name:
        return None                     # 숨김·없는 상품은 상품 틀이 안 온다
    uid = m.group(1)
    s = BeautifulSoup(html_text, "lxml")

    # 값 — .price .normal 이 파는 값, .consumer 가 소비자가(할인 전). 둘이 같으면 할인 없음.
    # 숨은 입력칸 #price 와 JSON-LD offers.price 도 파는 값이다(셋이 같은 것을 확인).
    offers = ld.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    pel = s.select_one("#price")
    price = _num(pel.get("value")) if pel is not None else 0
    if not price:
        nel = s.select_one(".price_wrap .price .normal")
        price = _num(nel.get_text()) if nel else _num(offers.get("price"))
    if not price:
        return None                     # 0원 줄보다 빈 줄이 낫다
    cel = s.select_one(".price_wrap .price .consumer")
    listed = _num(cel.get_text()) if cel else 0

    # 옵션 — 재고 줄마다 opt_ids(「204」 또는 「204,205」)와 같은 차례의 opt_values 가 온다.
    # 사이즈 축만 받는다. 칼린은 거의 다 「컬러」 하나라 사이즈 옵션이 없다(색마다 상품이 따로인 것도 많다).
    labels = _opt_label_map(s)
    stocks = [x for x in _stocks(html_text) if x.get("sto_state") != "HIDE"]   # HIDE = 매장이 내린 색
    size_opts: list[tuple[str, bool]] = []
    for st in stocks:
        ids = (st.get("opt_ids") or "").split(",")
        vals = (st.get("opt_values") or "").split(",")
        so = st.get("sto_state") != "SALE"
        for oid, v in zip(ids, vals):
            if re.search(r"(?i)size|사이즈", labels.get(oid, "")):
                v = _clean_val(v)
                hit = [i for i, (x, _) in enumerate(size_opts) if x == v]
                if not hit:
                    size_opts.append((v, so))
                elif not so:              # 색 하나라도 그 사이즈가 남았으면 그 사이즈는 판매중
                    size_opts[hit[0]] = (v, False)

    avail = str(offers.get("availability") or "")
    all_out = bool(stocks) and all(st.get("sto_state") != "SALE" for st in stocks)
    soldout = avail.endswith("OutOfStock") or all_out or (bool(size_opts) and all(so for _, so in size_opts))

    # 사진 — 대표(/shopimages/carlyn/<코드>2.jpg, 960×1280) + 여러 장(img9.kr/MS_product/<uid>/…)
    imgs: list[str] = []

    def add(u):
        u = _abs(u)
        if u and _img_key(u) not in [_img_key(x) for x in imgs]:
            imgs.append(_img_key(u))
    for im in s.select("#productDetail .thumb-wrap img"):
        add(im.get("src") or "")
    for u in ld.get("image") or []:
        add(u)
    # 옆 칸의 연출 사진(mediaUrl) — mp4 영상일 때도 있어 그림일 때만 끝에 붙인다
    mm = re.search(r'var mediaUrl = "([^"]+)"', html_text)
    if mm and re.search(r"(?i)\.(jpe?g|png|webp|gif)$", mm.group(1)):
        add(mm.group(1))

    # 설명 — 소개 글(.price_wrap .desc) + PRODUCT DETAILS(소재·크기·제조국·실측)
    lines: list[str] = []
    spec = {"상품명": name}
    del_ = s.select_one(".price_wrap .desc")
    if del_ is not None:
        lines += [t for t in del_.get_text("\n", strip=True).splitlines() if t.strip()]
    det = s.select_one("tr.detail_info .detail_slide") or s.select_one("tr.detail_info")
    if det is not None:
        for dd in det.find_all("dd"):
            t = re.sub(r"[ \t\xa0]+", " ", dd.get_text("\n", strip=True)).strip()
            for ln in (x.strip() for x in t.splitlines()):
                if not ln or _BOILER.search(ln):
                    continue
                kv = re.match(r"^([A-Za-z ]+?)\s*:\s*(.*)$", ln)
                if kv and kv.group(1).strip().lower() in _SPEC_KEYS:
                    if not kv.group(2).strip():
                        continue          # 「Name :」 처럼 값이 빈 칸은 버린다
                    spec.setdefault(_SPEC_KEYS[kv.group(1).strip().lower()], kv.group(2).strip())
                lines.append(ln)
    # 본문 편집기 칸 — 대개 빈 <p>·<center> 뿐이지만 글·그림이 있는 상품도 있을 수 있다
    ed = s.select_one(".styling_tip_area")
    detail_imgs: list[str] = []
    ed_html = ""
    if ed is not None:
        # 「STYLING TIP / View All」 머리 글은 게시판 바로가기라 뺀다. 그 아래 사진(착용 사진)은 상세 그림으로 둔다
        for x in ed.select("#videotalk_area, .board-style-wrap .title-wrap"):
            x.decompose()
        ed_html = str(ed)
        et = ed.get_text("\n", strip=True).replace("﻿", "")
        lines += [t.strip() for t in et.splitlines() if t.strip()]
        for im in ed.find_all("img"):
            u = _abs(im.get("ec-data-src") or im.get("data-src") or im.get("src") or "")
            if u and u not in detail_imgs and not re.search(r"(?i)icon|/images/common/|btn_", u):
                detail_imgs.append(u)
    text = "\n".join(dict.fromkeys(lines))        # 같은 줄이 두 곳에 있으면 한 번만

    table = cc.extract_size_any(ed_html) if ed_html and ed.find("table") else {}
    if len(table) < 2:
        table = crawl_pages.size_table_from_text(text)

    cat = str(ld.get("category") or "")
    # 「BAG (대) > 2026 F/W (중)」「의류 (대) > 티셔츠 (중)」 — 대·중·소 꼬리표를 뗀다
    cats = [re.sub(r"\s*\((대|중|소|세)\)\s*$", "", _html.unescape(c)).strip() for c in cat.split(">")]
    cats = [c for c in cats if c]

    d = {
        "product_no": int(uid),
        "name": name,
        "price": price,
        "soldout": soldout,
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": f"{BASE}/shop/shopdetail.html?branduid={uid}",
        "description": text,
        "description_source": "makeshop",
        "detail_text": text,
        "size_table": table,
        "spec": spec,
        "detail_images": detail_imgs,
        "options": [v for v, _ in size_opts],
        "soldout_options": [v for v, so in size_opts if so],
        "category_nos": [],
        "category_names": cats,
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "makeshop",
    }
    if listed > price:
        d["price_listed"] = listed
    if not stocks and not avail:
        d["soldout_unknown"] = True     # 재고 줄도 JSON-LD 재고도 없다 — 판매중이라 믿지 않게
    return d
