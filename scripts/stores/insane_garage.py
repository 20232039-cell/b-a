"""인세인 개러지(insane-garage) — 고도몰5 편집숍(자체 상품만 걷는다 — own_goods). 「ALL ITEM」 목록(cateCd=003)으로 상품 주소를 받고 상품 페이지를 한 벌씩 읽는다.

사이트맵(/sitemap.xml)은 낡았다 — goods_view 49개 가운데 38개(goodsNo 1000000017~60)가 지금 목록에 없고,
열면 `alert('해당 상품은 현재 구매가 불가한 상품입니다.')` 한 줄짜리 페이지가 온다. 사이트맵으로 걸으면
그것들이 「실패」로 쌓이고, 정작 판매 중인 673벌(2026-09-27)은 거의 빠진다. 그래서 목록 페이지를 쓴다.
ALL ITEM 은 한 쪽에 300벌씩 3쪽이고, 다른 칸(NEW·SALE·GIRLS·JEWELLERY·EXCLUSIVE …)은 모두 그 부분집합이다.
목록의 품절 표시(soldOut-cover)는 상품 페이지에서도 같은 것을 읽으므로 따로 쓰지 않는다.

robots.txt 는 `*` 에 아무것도 막지 않는다(Crawl-delay 10 은 이름 붙은 봇 무리에만). 그래도 한 판에 700번
가까이 부르니 간격을 넉넉히 둔다.
"""
import html as _html
import re

from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import crawl_shopify as cs

BASE = "https://www.insanegarage.shop"
PAGE_DELAY = 4.0
ALL_CATE = "003"          # 「ALL ITEM」

_UNAVAILABLE = "구매가 불가한 상품"

# 자체 상품의 표시 — 설명의 BRAND 줄이 없거나 인세인·IG 이면 자체, 이름에 INSANE · 「X IG」(협업)가 있어도 자체.
# 표본 24벌(2026-09-27): BRAND 줄이 없는 11벌은 모두 INSANE … / IGS … / 자체 모자였고, 줄이 있는 13벌은
# HDEX X IG(협업) · LOVE PARADISE 의 INSANE 키링을 빼면 모두 남의 브랜드였다.
_OWN_BRAND = re.compile(r"(?i)insane|인세인|^\s*igs?\s*$")
_OWN_NAME = re.compile(r"(?i)\binsane\b|\bx\s*igs?\b|^\s*igs?\b")


def own_goods(brand: str, name: str) -> bool:
    return not brand.strip() or bool(_OWN_BRAND.search(brand)) or bool(_OWN_NAME.search(name))


def list_urls(http, log=print) -> list[str] | None:
    seen: dict[str, None] = {}
    last = 1
    page = 1
    while page <= min(last, 50):
        u = f"{BASE}/goods/goods_list.php?cateCd={ALL_CATE}&page={page}"
        r = cs.get_patient(http, u, log)
        if r is None or r.status_code != 200:
            # 한 쪽이라도 못 받으면 목록이 모자란다 — 모자란 목록으로 걸면 멀쩡한 옷이 「목록에 없음」이 된다
            log(f"  [insane-garage] 목록 {page}쪽 받기 실패({getattr(r, 'status_code', '없음')})")
            return None
        codes = re.findall(r'<li class="product-item" data-code="(\d+)"', r.text)
        if not codes:
            break
        for c in codes:
            seen.setdefault(c, None)
        pages = [int(p) for p in re.findall(r"goods_list\.php\?page=(\d+)&amp;cateCd=" + ALL_CATE, r.text)]
        last = max([last, *pages])
        page += 1
    if not seen:
        log("  [insane-garage] 목록이 비었다")
        return None
    return [f"{BASE}/goods/goods_view.php?goodsNo={c}" for c in seen]


def _num(s: str) -> int:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s or "")
    return int(float(m.group(0).replace(",", ""))) if m else 0


def _hidden(html_text: str, name: str) -> str:
    m = re.search(r'name="' + re.escape(name) + r'"\s+value="([^"]*)"', html_text)
    return m.group(1) if m else ""


def _big(u: str) -> str:
    u = (u or "").strip()
    return "https:" + u if u.startswith("//") else u


_NOTICE = re.compile(r"연휴|휴무|출고|배송|CS\b|지연|공지|중단")


def _desc_text(el) -> str:
    """PRODUCT DETAILS 칸의 글 — 머리에 붙은 매장 공지는 뺀다.

    거의 모든 상품 설명 맨 앞에 「* 추석 연휴로 인하여 9월28일 12:00까지 CS 및 출고가 중단됩니다.」 같은
    공지가, 몇 벌에는 「[배송 지연 안내]」 칸이 통째로 들어 있다. 첫 「[…]」 머리 앞의 공지 줄과,
    머리에 배송·공지·안내가 든 칸만 뺀다(「* MEASUREMENT MAY VARY」 같은 실측 주석은 남는다).
    """
    if el is None:
        return ""
    t = el.get_text("\n", strip=True)
    t = re.sub(r"[\xa0　\u200b]", " ", t)
    out: list[str] = []
    seen_head = False
    skip = False
    for ln in (re.sub(r"[ \t]+", " ", x).strip() for x in t.splitlines()):
        if not ln:
            continue
        if re.fullmatch(r"\[[^\]]{1,30}\]", ln):
            seen_head = True
            skip = bool(re.search(r"배송|공지|안내|notice", ln, re.I))
            if skip:
                continue
        if skip or (not seen_head and _NOTICE.search(ln)):
            continue
        out.append(ln)
    return "\n".join(out)


_SEG = re.compile(r"^\s*([A-Za-z가-힣][A-Za-z가-힣 .()]*?)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:\(?\s*cm\s*\)?)?\s*$", re.I)
_HEAD = re.compile(r"^\s*([A-Za-z0-9]{1,6}(?:\s*(?:SIZE|사이즈))?)\s*[-–:]\s*(?=[A-Za-z가-힣])", re.I)


def _size_text(text: str) -> str:
    """사이즈마다 한 줄로 「이름 값 / 이름 값 …」을 늘어놓은 실측을 표 글(머리줄 + 사이즈 줄)로 바꾼다.

    이 매장 설명은 거의 다 「1 SIZE - 총장 65 / 어깨단면 48.5 / 가슴단면 57 / AH 25 / 소매길이 64.4 (CM)」
    꼴이다(브랜드마다 「M - …」「S 사이즈 - …」「XL- …」). size_from_ocr 에 그대로 주면 첫 사이즈 값만
    읽혀 칸마다 값이 하나였다(INSANE 긴팔 · 청바지로 확인). HANDSEND 는 「1Size」 줄 다음에
    「Shoulder : 47 / Chest : 54 …」를 둔다 — 사이즈 이름을 앞의 짧은 줄에서 받는다.
    칸 이름이 첫 줄과 같은 줄만 모은다(모델 정보 「Height 180 / Weight 70」 따위를 섞지 않게).
    """
    lines = text.splitlines()
    rows: list[tuple[str, list[tuple[str, str]]]] = []
    for i, ln in enumerate(lines):
        ln = re.sub(r"\(\s*cm\s*\)?|cm\s*\)", "", ln, flags=re.I)
        mh = _HEAD.match(ln)
        head, body = (mh.group(1), ln[mh.end():]) if mh else ("", ln)
        segs = [_SEG.match(x) for x in body.split("/")]
        if len(segs) < 2 or not all(segs):
            continue
        if not head and i and len(lines[i - 1].strip()) <= 12:
            head = lines[i - 1].strip()
        head = re.sub(r"(?i)\s*(?:size|사이즈)\s*$", "", head).strip()
        if head:
            rows.append((head.replace(" ", ""), [(m.group(1).replace(" ", ""), m.group(2)) for m in segs]))
    if not rows:
        return text
    keys = [k for k, _ in rows[0][1]]
    rows = [r for r in rows if [k for k, _ in r[1]] == keys]
    # 「AH」(암홀)는 size_from_ocr 가 모르는 이름이라, 다른 칸 하나가 범위 밖으로 빠지면 값이 한 칸씩 밀렸다
    # (INSANE CAMO 티셔츠: 총장 칸에 어깨 값). 아는 이름으로 바꿔 둔다.
    heads = ["암홀" if re.fullmatch(r"(?i)a\.?h\.?", k) else k for k in keys]
    return "\n".join(["SIZE " + " ".join(heads)] + [h + " " + " ".join(v for _, v in kv) for h, kv in rows])


def _options(s: BeautifulSoup) -> tuple[list[str], list[str], bool]:
    """사이즈 값 · 품절 값 · 고를 수 있는지.

    고도몰 옵션 값은 `옵션번호||값추가금||…||재고^|^값1^|^값2`. 옵션 이름(dt.option-name)이
    「COLOR / SIZE」처럼 여럿이면 값도 ^|^ 로 여럿이라 사이즈 자리만 받는다. 품절 옵션에는 고도몰이
    「[품절]」 글을 붙이고 disabled 를 단다.
    """
    sel = s.select_one('select[name="optionSnoInput"]')
    if sel is None:
        return [], [], True
    box = sel.find_parent("dl")
    dt = box.select_one("dt.option-name") if box is not None else None
    names = [n.strip().lower() for n in re.split(r"[/,]", dt.get_text(" ", strip=True) if dt else "")]
    idx = next((i for i, n in enumerate(names) if re.search(r"size|사이즈|치수", n)), None)
    if idx is None:
        # 이름이 하나이고 색이 아니면(「OPTION」 따위) 사이즈로 본다 — 색 축만 있으면 사이즈 옵션이 없다
        idx = 0 if len(names) == 1 and not re.search(r"colou?r|색", names[0]) else None
    # 값마다 「품절」 표시가 있었는지 — 색·사이즈가 한 칸이면 같은 사이즈가 색마다 오므로,
    # 어느 색에서든 살 수 있으면 그 사이즈는 품절이 아니다
    got: dict[str, bool] = {}
    for o in sel.find_all("option"):
        v = (o.get("value") or "").strip()
        if "^|^" not in v or idx is None:
            continue
        parts = [_html.unescape(p).strip() for p in v.split("^|^")[1:]]
        if idx >= len(parts) or not parts[idx]:
            continue
        # 「13cm/본 제품의 교환&환불 정책 내용을 확인하였고 동의합니다.」 — 값 뒤에 붙인 동의 문구를 뗀다
        val = re.sub(r"\s*/[^/]*(?:동의|확인)[^/]*$", "", parts[idx]).strip()
        so = "품절" in o.get_text(" ", strip=True)
        got[val] = got.get(val, True) and so
    vals = list(got)
    sold = [v for v, so in got.items() if so]
    return vals, sold, not sel.has_attr("disabled")


def parse_page(html_text: str, url: str, slug: str, now: str, http=None) -> dict | None:
    if _UNAVAILABLE in html_text[:2000] or 'id="frmView"' not in html_text:
        return None                       # 판매 불가·숨김 상품 — 한 줄짜리 alert 페이지
    s = BeautifulSoup(html_text, "lxml")
    m = re.search(r"var goodsNo = '(\d+)'", html_text) or re.search(r"goodsNo=(\d+)", url)
    name_el = s.select_one(".item_tit_detail_cont .product-name h3")
    if not m or name_el is None:
        return None
    name = name_el.get_text(" ", strip=True)
    # 값 — set_goods_price 가 판매가, set_goods_fixedPrice 가 정가(없으면 0). 상품 할인(goodsDiscountFl=y)이
    # 걸리면 화면 값은 판매가에서 깎은 값이라 그것을 판다. 화면 글씨(.item_price)는 고도몰이 할인을 반영해 찍는다.
    base = _num(_hidden(html_text, "set_goods_price"))
    fixed = _num(_hidden(html_text, "set_goods_fixedPrice"))
    shown_el = s.select_one(".item_detail_list .item_price strong strong") or s.select_one(".item_detail_list .item_price")
    shown = _num(shown_el.get_text(" ", strip=True)) if shown_el is not None else 0
    price = base
    if _hidden(html_text, "goodsDiscountFl") == "y" and shown and shown < base:
        price = shown
    price = price or shown
    if not price:
        return None                       # 값을 못 읽은 줄은 쓰지 않는다 — 0원 옷보다 빈 줄이 낫다
    listed = max(fixed, base)

    opts, sold_opts, selectable = _options(s)
    # 상품 전체 품절 — 고도몰은 품절 상품이면 장바구니 단추(#cartBtn)를 빼고 옵션 칸을 disabled 로 둔다
    whole = s.select_one("#cartBtn") is None or not selectable or (bool(opts) and len(sold_opts) == len(opts))
    if whole and opts:
        sold_opts = list(opts)            # 살 수 있는 사이즈가 없다 — 재고 숫자가 남아 있어도(판매 중지) 다 품절

    imgs: list[str] = []
    for im in s.select(".detail-img-wrap .pi .detail-img img") or s.select(".detail-img-wrap img"):
        u = _big(im.get("src") or im.get("data-src") or "")
        if u and u not in imgs:
            imgs.append(u)
    if not imgs:
        og = s.select_one('meta[property="og:image"]')
        if og and og.get("content"):
            imgs = [_big(og["content"])]

    # 설명 — 「PRODUCT DETAILS」 칸. SHIPPING · RETURN & EXCHANGE · A/S · Q&A 칸은 매장 공지라 뺀다.
    desc_el = None
    for item in s.select(".product-detail-add .product-detail-add-item"):
        btn = item.select_one(".product-detail-add-btn")
        if btn is not None and re.search(r"PRODUCT\s*DETAIL", btn.get_text(" ", strip=True), re.I):
            desc_el = item.select_one(".product-detail-add-cont")
            break
    text = _desc_text(desc_el)
    detail_imgs: list[str] = []
    if desc_el is not None:
        for im in desc_el.find_all("img"):
            u = _big(im.get("data-src") or im.get("src") or "")
            if u and u not in detail_imgs and not re.search(r"icon|logo|banner|delivery|shipping", u, re.I):
                detail_imgs.append(u)

    size_table = {}
    if desc_el is not None and desc_el.find("table") is not None:
        size_table = cc.extract_size_any(str(desc_el))
    if not size_table and re.search(r"\d", text):
        import crawl_pages
        conv = _size_text(text)
        size_table = crawl_pages.size_table_from_text(conv)
        if not size_table and conv != text:
            size_table = crawl_pages.size_table_from_text(text)

    cat = s.select_one(".location_wrap .location_tit")
    cats = [cat.get_text(" ", strip=True)] if cat is not None and cat.get_text(strip=True) else []
    spec = {"상품명": name}
    mb = re.search(r"BRAND\s*:\s*([^\n]+)", text)
    if mb:
        spec["브랜드"] = mb.group(1).strip()
    if not own_goods(spec.get("브랜드", ""), name):
        # 편집숍의 남의 브랜드 옷(요세이즈씨·조실버·노매뉴얼 …) — 「인세인개러지」 이름으로 앱에 들어가면
        # 틀린 값이다. 실패가 아니라 건너뜀이라 구동부가 가드레일에 세지 않는다(crawl_pages 머리말).
        return {"product_no": int(m.group(1)), "_skip": "남의 브랜드 " + spec.get("브랜드", "")}

    d = {
        "product_no": int(m.group(1)),
        "name": name,
        "price": price,
        "soldout": whole,
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": f"{BASE}/goods/goods_view.php?goodsNo={m.group(1)}",
        "description": text,
        "description_source": "godo",
        "detail_text": text,
        "size_table": size_table,
        "spec": spec,
        "detail_images": detail_imgs,
        "options": opts,
        "soldout_options": sold_opts,
        "category_nos": [],
        "category_names": cats,
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "godo",
    }
    if not whole and not opts and _hidden(html_text, "optionFl") == "y" and s.select_one('select[name="optionSnoInput"]') is None:
        d["soldout_unknown"] = True   # 옵션이 있다는데 읽는 칸(optionSnoInput)이 없다 — 나눠진 선택칸 따위, 모르는 꼴
    if listed > price:
        d["price_listed"] = listed
    return d
