"""컨셉원(concepts1one) — 위사몰(wm_engine_SW). 사이트맵으로 상품 주소를 받고 상품 페이지를 한 벌씩 읽는다.

사이트맵에 `shop/detail.php?pno=<32자 16진>` 이 730개 있는데, 같은 옷이 여러 칸(신상품·기획전·TOP…)에
**복제 상품**으로 올라가 있어 실제 옷은 그보다 적다. pno 는 상품 번호의 md5 다(md5("30579") →
918B71F2…, 2026-09-27 확인). 복제본은 `ppno` 에 원본의 md5 를 들고, 옵션 입력칸의 `data-pno` 는
원본 번호(30579)다 — 재고·옵션이 원본 것이라는 뜻이라, 원본 번호를 product_no 로 써 복제본을 한 줄로 모은다.

사이즈별 품절은 정적 HTML 에 없다. 색을 고르면 `/main/exec.php?exec_file=shop/getAjaxData.php
&exec=getOptionStock` 으로 받아 칩에 soldout 을 다는데(shop.js selectOptionChip), robots.txt 가
`/main/` 을 막는다. 그래서 옵션이 있는 줄은 soldout_unknown 을 단다. 부가 사진(getAddImgList)도
같은 길이라 gallery 는 대표 사진 하나다.

실측표는 「실측사이즈」 레이어(#det_size_info) 안의 **그림**(`/_con_on/<해>/<품번>size.jpg`)이다 —
detail_images 에 넣어 OCR 이 읽게 한다. 상품정보고시(…info.jpg)·소재정보(…ch.jpg) 그림도 소재가
적혀 있어 같이 넣는다.
"""
import html as _html
import json
import re

from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import crawl_shopify as cs

BASE = "https://www.concepts1one.co.kr"
PAGE_DELAY = 3.0          # robots.txt 에 Crawl-delay 가 없다 — 식스샵·다른 매장과 같은 간격

_PNO = re.compile(r"pno=([0-9A-Fa-f]{32})")


def list_urls(http, log=print) -> list[str] | None:
    r = cs.get_patient(http, f"{BASE}/sitemap.xml", log)
    if r is None or r.status_code != 200:
        log(f"  [concepts1one] 사이트맵 받기 실패({getattr(r, 'status_code', '없음')})")
        return None
    out: dict[str, str] = {}
    for u in re.findall(r"<loc>([^<]+)</loc>", r.text):
        u = _html.unescape(u).strip()
        m = _PNO.search(u)
        if "/shop/detail.php" in u and m:
            # 주소의 cno1(칸 번호)은 빼고 pno 로만 — 같은 pno 가 칸마다 두 번 오지 않게
            out.setdefault(m.group(1).upper(), f"{BASE}/shop/detail.php?pno={m.group(1).upper()}")
    if not out:
        log("  [concepts1one] 사이트맵에 상품 주소가 없다")
        return None
    return list(out.values())


def _won(s: str) -> int:
    m = re.search(r"\d[\d,]*", s or "")
    return int(m.group(0).replace(",", "")) if m else 0


def _abs(u: str) -> str:
    u = (u or "").strip().split("#")[0]      # 대표 사진 주소 끝의 「#addimg」 표식을 뗀다
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return BASE + u               # 세트의 「/_con_on//1344-…info.jpg」 겹 빗금은 매장이 쓴 그대로 둔다
    return u


def _ld_product(html_text: str) -> dict:
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html_text, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            return d
    return {}


def _options(s: BeautifulSoup) -> tuple[list[str], list[str]]:
    """사이즈 축의 칩 값과, 정적 HTML 에 soldout 이 달린 칩.

    옵션 이름은 숨은 입력칸 option_name<N>(세트 상품은 option_name<N>[<구성>])에 있고, 칩은
    selectOptionChip(N, '값::…::항목번호::cpx0::') 이다. 세트(자켓+슬랙스)는 구성마다 사이즈 축이 따로라
    둘 다 받는다(M·L·XL 과 29·30…).
    """
    size_axes: set[str] = set()
    for inp in s.select('input[name^="option_name"]'):
        m = re.match(r"option_name(\d+)", inp.get("name") or "")
        if m and re.search(r"사이즈|size", inp.get("value") or "", re.I):
            size_axes.add(m.group(1))
    vals: list[str] = []
    sold: list[str] = []
    for a in s.select('a[onclick*="selectOptionChip"]'):
        m = re.search(r"selectOptionChip\(\s*(\d+)\s*,\s*'([^']*)'", a.get("onclick") or "")
        if not m or m.group(1) not in size_axes:
            continue
        v = _html.unescape(m.group(2).split("::")[0]).strip()
        if not v:
            continue
        if v not in vals:
            vals.append(v)
        if "soldout" in (a.get("class") or []) and v not in sold:
            sold.append(v)
    return vals, sold


def parse_page(html_text: str, url: str, slug: str, now: str, http=None) -> dict | None:
    if 'name="prdFrm"' not in html_text:
        return None                       # 없는 상품·오류 화면
    s = BeautifulSoup(html_text, "lxml")
    ld = _ld_product(html_text)
    name_el = s.select_one(".wrap_prd .info h3.name")
    name = ""
    if name_el is not None:
        for a in name_el.find_all("a"):
            a.decompose()                 # 위시 단추 글(「위시 담기」)을 이름에서 뺀다
        name = name_el.get_text(" ", strip=True)
    name = name or (ld.get("name") or "").strip()
    # 값 — .info 의 data-sell_prc 가 판매가, data-normal_prc 가 정가. 화면 큰 글씨와 JSON-LD price 는
    # data-lower_prc(선착순 회원 쿠폰가)라 쓰지 않는다 — 쿠폰가는 수시로 바뀐다(crawl_cafe24 머리말).
    info = s.select_one(".wrap_prd .info[data-sell_prc]")
    price = _won(info.get("data-sell_prc")) if info is not None else 0
    listed = _won(info.get("data-normal_prc")) if info is not None else 0
    if not name or not price:
        return None                       # 값을 못 읽은 줄은 쓰지 않는다 — 0원 옷보다 빈 줄이 낫다

    # 상품 번호 — 원본 번호로 모은다(머리말). 세트는 구성품 번호가 data-pno 라 set_pno 를 먼저 본다.
    m = (re.search(r'name="set_pno" value="(\d+)"', html_text)
         or re.search(r'data-pno="(\d+)"', html_text)
         or re.search(r"var refPno = '(\d+)'", html_text))
    if not m:
        return None
    product_no = int(m.group(1))
    # 주소도 원본의 것으로 — 복제본마다 주소가 달라도 같은 옷이 같은 주소가 되게
    mp = re.search(r'name="ppno" value="([0-9A-Fa-f]{32})"', html_text) or _PNO.search(url)
    pno = mp.group(1).upper() if mp else ""
    source_url = f"{BASE}/shop/detail.php?pno={pno}" if pno else url

    # 판매 상태 — 위사몰 stat 2=정상(3 품절 · 4 숨김). shop.js addCart 가 「stat != 2」면 품절 알림을 띄우고
    # 담기를 막으므로 2 가 아니면 다 품절로 본다. JSON-LD availability 도 같이 본다.
    ms = re.search(r'name="stat" value="(\d+)"', html_text)
    stat = ms.group(1) if ms else ""
    avail = str((ld.get("offers") or {}).get("availability") or "")
    opts, sold_opts = _options(s)
    whole_sold = ((stat != "" and stat != "2") or avail.endswith(("OutOfStock", "SoldOut", "Discontinued"))
                  or (bool(opts) and len(sold_opts) == len(opts)))

    img = s.select_one("#mainImg")
    imgs = []
    for u in [img.get("src") if img is not None else "", ld.get("image") or ""]:
        u = _abs(u)
        if u and u not in imgs:
            imgs.append(u)

    # 설명 — 「상품 설명」 탭(.prd_detail_content_div_gio 첫 칸)이 글이다. 둘째·셋째 칸은 상품정보고시·
    # 소재정보 그림, 넷째 칸은 배송 안내라 뺀다. 요약(.toggle_detail .section)은 주석 처리돼 있어 안 본다.
    tabs = s.select(".prd_detail_table_gio .prd_detail_content_div_gio")
    text = ""
    if tabs:
        text = "\n".join(ln.strip() for ln in tabs[0].get_text("\n", strip=True).splitlines() if ln.strip())
    if not text:
        # 오래된 상품은 탭이 비어 있다 — JSON-LD 설명(앞 100자쯤에서 잘린다)이라도 둔다
        text = (ld.get("description") or "").strip()

    detail_imgs: list[str] = []

    def add(u):
        u = _abs(u)
        # 세트 상품의 「/_con_on//1344-…info.jpg」는 해 폴더가 빠진 주소라 그림이 아니라 오류 HTML 이 온다 — 뺀다
        if u and u not in detail_imgs and "/_con_on//" not in u and not re.search(r"/_skin/|coupon|banner|icon|logo", u, re.I):
            detail_imgs.append(u)

    size_el = s.select_one("#det_size_info .size_img")
    for im in (size_el.find_all("img") if size_el is not None else []):
        add(im.get("src") or im.get("data-src"))
    # 상세 그림 — .wing-detail-more-contents 안(쿠폰 배너 .detail_banner_gio 는 바깥이라 섞이지 않는다)
    for im in s.select(".wing-detail-more-contents img"):
        add(im.get("src") or im.get("data-src"))
    for t in tabs[1:3]:
        for im in t.find_all("img"):
            add(im.get("src") or im.get("data-src"))

    size_table = {}
    if size_el is not None and size_el.find("table") is not None:
        size_table = cc.extract_size_any(str(size_el))
    if not size_table and size_el is not None and re.search(r"\d", size_el.get_text(" ", strip=True)):
        import crawl_pages        # 레이어에 표 대신 글로 적은 상품이 있으면 — 지금 본 것은 다 그림이다
        size_table = crawl_pages.size_table_from_text(size_el.get_text("\n", strip=True))

    cats = []
    for a in s.select(".navi a"):
        t = a.get_text(" ", strip=True)
        if t and t.lower() != "home" and "detail.php" not in (a.get("href") or "") and t not in cats:
            cats.append(t)

    style = ""
    ident = ld.get("identifier")
    if isinstance(ident, dict):
        style = str(ident.get("value") or "").strip()
    spec = {"상품명": name}
    if style:
        spec["품번"] = style

    d = {
        "product_no": product_no,
        "name": name,
        "price": price,
        "soldout": whole_sold,
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": source_url,
        "description": text,
        "description_source": "wisa",
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
        "platform": "wisa",
    }
    if opts and not whole_sold:
        d["soldout_unknown"] = True   # 사이즈별 재고는 robots 가 막은 ajax 에만 있다(머리말) — 판매중으로 믿지 않게
    if listed > price:
        d["price_listed"] = listed
    return d
