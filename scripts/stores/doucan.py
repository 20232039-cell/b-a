"""두칸(doucan) — 자바(Spring) 자체 쇼핑몰 틀. 화면 스크립트가 `window.bta` 이름공간을 쓴다(cw=PC, mw=모바일).

목록: 사이트맵(sitemap_product_1·2, 775곳)은 쓰지 않는다. 조사(2026-09-27)해 보니
  · 품절 상품이 사이트맵에서 **빠진다** — 매장 칸의 SOLD OUT 56벌이 사이트맵엔 한 벌도 없었다.
    사이트맵으로 걸으면 품절이 「목록에 없음」으로 세어져 delisted 로 내려간다.
  · 나머지 500곳은 CUSTOM(시즌 컬렉션) 칸의 「custom order」 — 값이 1원(prod_sale_price=1.00)이고
    장바구니가 없이 전화 주문(070-…)만 받는다. 파는 옷 줄로 쓸 수 없고, 해석 실패로 세면 가드레일이 걸린다.
  그래서 SHOP 칸(NEW ARRIVAL·TOP·SKIRT…)의 목록 ajax(/front/product/product_list.ajax, 한 쪽 16벌)를
  쪽마다 받는다. 판마다 요청 30번 안팎이고 품절도 목록에 남는다.
상세: 상품 페이지 HTML 하나에 값(#prod_sale_price) · 품절(#prod_soldouts · 수량칸 stock) · 사진(swiper) ·
  글(오른쪽 h3 — 실측·혼용률·모델)이 다 있다. 실측은 **글**(「Shoulder : 43 / Bust : 49 / …」)이다.
robots: /front/global/ 은 막혀 있다 — 옵션 재고 ajax(product_option.ajax)가 거기라 부르지 않는다.
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import crawl_pages
import crawl_shopify as cs

BASE = "https://doucan.net"
PAGE_DELAY = 3.0
LIST_AJAX = BASE + "/front/product/product_list.ajax?page={page}&cateIdx={cate}&qcate=0"
# 칸 이름을 읽을 첫 페이지 — 옆 메뉴 SHOP 묶음에 칸 번호·이름이 다 있다
MENU_URL = BASE + "/front/product/category/151"
# 새 상품 칸은 옷 갈래가 아니고 주마다 바뀐다 — 주소는 받되 칸 이름으로는 쓰지 않는다
SKIP_CAT_NAMES = {"NEW ARRIVAL"}

# list_urls 가 채운다: 상품 번호 → 매장 칸 이름들. parse_page 가 category_names 로 쓴다
_CATS: dict[int, list[str]] = {}


def _abs(u: str) -> str:
    u = (u or "").strip()
    if u.startswith("//"):
        return "https:" + u
    return urljoin(BASE + "/", u) if u else ""


def _shop_cats(html_text: str) -> list[tuple[int, str]]:
    """옆 메뉴의 SHOP 묶음만 — CUSTOM 묶음(시즌 컬렉션)은 전화 주문 옷이라 뺀다."""
    s = BeautifulSoup(html_text, "lxml")
    out = []
    for box in s.select(".side-bar-menu-box"):
        nav = box.select_one(".side-bar-menu-nav")
        if not nav or nav.get_text(strip=True).upper() != "SHOP":
            continue
        for a in box.select("a.qx-cate"):
            m = re.search(r"/category/(\d+)", a.get("href") or "")
            if m:
                out.append((int(m.group(1)), a.get_text(" ", strip=True)))
    return out


def list_urls(http, log) -> list[str] | None:
    r = cs.get_patient(http, MENU_URL, log)
    if r is None or r.status_code != 200:
        log(f"  [doucan] 메뉴 받기 실패({getattr(r, 'status_code', '없음')})")
        return None
    cats = _shop_cats(r.text)
    if not cats:
        log("  [doucan] SHOP 칸을 못 찾았다 — 메뉴 모양이 바뀌었는지 볼 것")
        return None
    _CATS.clear()
    order: list[int] = []
    for cno, cname in cats:
        page, pages = 1, 1
        while page <= pages:
            r = cs.get_patient(http, LIST_AJAX.format(page=page, cate=cno), log)
            if r is None or r.status_code != 200:
                # 칸 하나라도 못 받으면 판을 버린다 — 빠진 칸의 옷이 「목록에 없음」으로 내려가지 않게
                log(f"  [doucan] {cname} {page}쪽 받기 실패({getattr(r, 'status_code', '없음')})")
                return None
            m = re.search(r'total-page="(\d+)"', r.text)
            pages = int(m.group(1)) if m else 1
            for blk in r.text.split('class="product"')[1:]:
                mp = re.search(r"/front/product/(\d+)", blk)
                if not mp:
                    continue
                pno = int(mp.group(1))
                if pno not in _CATS:
                    _CATS[pno] = []
                    order.append(pno)
                if cname not in SKIP_CAT_NAMES and cname not in _CATS[pno]:
                    _CATS[pno].append(cname)
            page += 1
    log(f"  [doucan] SHOP 칸 {len(cats)}곳에서 {len(order)}벌")
    return [f"{BASE}/front/product/{n}" for n in order]


def _val(s, sel: str) -> str:
    el = s.select_one(sel)
    return (el.get("value") or "").strip() if el else ""


def _num(v: str) -> int:
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def _key(u: str) -> str:
    """사진 파일 이름의 앞머리 — 대표 사진은 detail/(800px)·origin/(1200px) 두 벌이 앞머리만 같다."""
    return u.rsplit("/", 1)[-1].split(".", 1)[0]


# 상세 편집기의 장식 그림 — 줄 긋기 gif · 이름 띠 · 브랜드 소개 띠(「doucanis」, 옛 편집기 alt 「…-S14L1」)
_DECOR = re.compile(r"(?i)\.gif$|tranc-bar|name-bg|doucanis")


# 실측 줄 — 「01 | Shoulder 39 / Bust 49 / Waist - / …」 · 「S > Shoulder : - / Waist : 34 / …」 ·
# 이름 없이 「Shoulder : 43 / Bust : 49 / …」(한 치수 옷). 사이즈 이름 뒤 가름표는 | 나 > 뿐이다
_ROW = re.compile(r"^(?:(?P<nm>[A-Za-z0-9]{1,4})\s*[|>]\s*)?(?P<body>[A-Za-z][A-Za-z ]*?\s*:?\s*(?:[\d.~\-]+|-)\s*(?:cm)?"
                  r"(?:\s*/\s*[A-Za-z][A-Za-z ]*?\s*:?\s*(?:[\d.~\-]+|-)?\s*(?:cm)?)+)\s*$", re.I)
_PAIR = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?)\s*:?\s*([\d.]+(?:\s*[~\-]\s*[\d.]+)?|-)?\s*(?:cm)?\s*$", re.I)


def _size_text(text: str) -> str:
    """실측 줄을 표 해석기가 읽는 꼴로 — 사이즈 이름이 있는 줄들은 「Size 어깨… / 01 39 …」 행렬로 모은다.

    size_from_ocr 는 「01 | Shoulder 39 / …」가 두 줄이어도 첫 줄만 읽었다(트위스트 티셔츠 01·02 에서
    01 만 남음). 행렬로 바꾸면 두 치수를 다 읽고 이름(_names)도 붙는다.
    「Waist : 31~48」(고무 허리) · 「Length : 58-94」(앞뒤 길이가 다른 옷)처럼 값이 폭으로 온 칸은 「-」로 둔다 —
    한쪽을 고르면 짐작이다.
    """
    rows: list[tuple[str, list[str], list[str]]] = []
    for ln in text.splitlines():
        m = _ROW.match(ln.strip())
        if not m:
            continue
        labs, vals = [], []
        for seg in m.group("body").split("/"):
            mp = _PAIR.match(seg)
            if not mp:
                labs = []
                break
            v = (mp.group(2) or "-").replace(" ", "")
            labs.append(mp.group(1).strip())
            vals.append(v if re.fullmatch(r"[\d.]+", v) else "-")
        if len(labs) >= 2:
            rows.append((m.group("nm") or "", labs, vals))
    if not rows:
        return ""
    named = [r for r in rows if r[0]]
    if named and all(r[1] == named[0][1] for r in named):
        return "\n".join(["Size " + " ".join(named[0][1])] + [f"{nm} " + " ".join(v) for nm, _, v in named])
    nm, labs, vals = rows[0]
    return " / ".join(f"{a} {v}" for a, v in zip(labs, vals))


def parse_page(html_text, url, slug, now, http=None) -> dict | None:
    s = BeautifulSoup(html_text, "lxml")
    pno = _val(s, "#prod_no")
    info = s.select_one("#prod_detail_info")
    if not pno.isdigit() or info is None:
        return None                   # 「상품 정보가 존재하지 않습니다」 페이지(내린 상품)
    sale = _num(_val(s, "#prod_sale_price"))
    if sale <= 1:
        return None                   # custom order(1원) — 전화 주문이라 값이 없다
    # 이벤트 할인은 금액으로 온다 — 화면 합계도 sale - disc_event 로 낸다(ui.cw.product.js).
    # 회원등급 할인(prod_disc_grade)은 로그인한 사람 값이라 쓰지 않는다.
    disc = _num(_val(s, "#prod_disc_event"))
    price = sale - disc if 0 < disc < sale else sale
    code_el = info.select_one(".infobox1 h2")
    code = code_el.get_text(" ", strip=True) if code_el else ""
    code = re.sub(r"\s+", " ", code)
    h3s = [re.sub(r"[ \t\xa0]+", " ", h.get_text("\n", strip=True)) for h in info.find_all("h3")]
    # 첫 h3 가 영어 이름(「V-neck Ruffle Blouse」), 매장 목록 이름은 품번(「MRC-5402 VBL GD」)뿐이다.
    # 품번만으로는 갈래를 못 읽으니 둘을 같이 쓴다.
    title = h3s[0] if h3s and "\n" not in h3s[0] and not re.search(r"\d\s*[:/|]", h3s[0]) else ""
    name = f"{title} ({code})" if title and code else (title or code)
    if not name:
        return None

    # 품절 — 상품 전체 일시품절(prod_soldouts=1)이면 버튼이 「일시품절」, 수량칸 stock=0.
    whole_so = _val(s, "#prod_soldouts") == "1"
    qty = info.select_one('.countbox input[name="cqty"]')
    stock = qty.get("stock") if qty is not None else None
    btn = info.select_one(".buttonwrap .button.disabled")
    soldout = whole_so or stock == "0" or bool(btn and "품절" in btn.get_text())
    # 옵션 상품(.optionbox — 트위스트 티셔츠 01·02, 핀턱 스커트 S·M)은 옵션 목록·재고를 ajax
    # (/front/global/product_option.ajax — robots 가 막음)로 받는다. 한 치수 옷은 옵션 상자 없이 수량칸만 있다.
    optbox = info.select_one(".optionbox")
    opt_labels = [dt.get_text(" ", strip=True) for dt in optbox.select("dt")] if optbox is not None else []

    # 사진 — og:image 가 원본(origin/ 1200px), 첫 슬라이드는 같은 그림의 detail/(800px)이라 뺀다
    og = s.select_one('meta[property="og:image"]')
    main = _abs(og.get("content")) if og else ""
    imgs = [main] if main else []
    for im in s.select(".product-img .swiper-slide img"):
        u = _abs(im.get("src") or im.get("data-src") or "")
        if u and u not in imgs and not (main and _key(u) == _key(main)):
            imgs.append(u)

    # 글 — 오른쪽 h3(이름 · 실측 · 혼용률 · 모델 · 세탁) + 상세 편집기 글. 전화번호·「More information」은 뺀다
    lines = []
    for t in h3s:
        for ln in t.split("\n"):
            ln = re.sub(r"[\xa0\s]+", " ", ln).strip()
            if ln and not re.fullmatch(r"[\d\-\s]{9,}", ln):
                lines.append(ln)
    ed = s.select_one("#contents_info .editorbox")
    if ed is not None:
        et = re.sub(r"[\xa0 \t]+", " ", ed.get_text("\n", strip=True))
        lines += [ln.strip() for ln in et.splitlines() if ln.strip()]
    text = "\n".join(dict.fromkeys(lines))
    detail_imgs = []
    if ed is not None:
        for im in ed.find_all("img"):
            u = _abs(im.get("data-src") or im.get("src") or "")
            alt = im.get("alt") or ""
            # 옛 편집기(NNEditor)는 첫 그림이 브랜드 소개 띠이고 alt 가 「블라우스 -S14L1」 꼴이다(L2 가 착용 사진)
            if not u or _DECOR.search(u) or re.search(r"-S\d+L1$", alt):
                continue
            if u not in detail_imgs:
                detail_imgs.append(u)

    # 사이즈 옵션 — 선택칸은 비어 오고(ajax 가 채운다) 그 ajax 는 robots 가 막는다. 옵션 상자가 「사이즈」
    # 하나일 때만 실측 줄의 사이즈 이름(01·02 / S·M)을 옵션으로 쓴다. 사이즈별 품절은 모른다.
    opts: list[str] = []
    if len(opt_labels) == 1 and re.search(r"(?i)사이즈|size", opt_labels[0]):
        for ln in text.splitlines():
            m = _ROW.match(ln.strip())
            if m and m.group("nm") and m.group("nm") not in opts:
                opts.append(m.group("nm"))

    # 실측표 — 편집기에 HTML 표가 있으면 그것, 없으면 글(「Shoulder : 43 / Bust : 49 / …」)
    size_table = cc.extract_size_any(str(ed)) if ed is not None and ed.find("table") else {}
    if len(size_table) < 2:
        size_table = crawl_pages.size_table_from_text(_size_text(text) or text)

    d = {
        "product_no": int(pno),
        "name": name,
        "price": price,
        "soldout": soldout,
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": f"{BASE}/front/product/{pno}",
        "description": text,
        "description_source": "bta",
        "detail_text": text,
        "size_table": size_table,
        "spec": {"상품명": name, **({"품번": code} if code else {})},
        "detail_images": detail_imgs,
        "options": opts,
        # 상품 전체가 품절이면 사이즈도 다 품절이다 — 그 밖엔 사이즈별 재고를 모른다
        "soldout_options": list(opts) if soldout else [],
        "category_nos": [],
        "category_names": list(_CATS.get(int(pno), [])),
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "bta",
    }
    if disc and price < sale:
        d["price_listed"] = sale
    if not soldout and (optbox is not None or stock is None):
        d["soldout_unknown"] = True   # 옵션별 재고를 못 읽었거나 수량칸이 없다 — 판매중으로 믿지 않게
    return d
