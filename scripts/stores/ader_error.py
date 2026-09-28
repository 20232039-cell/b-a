"""아더에러(ader-error) — 매장이 직접 만든 사이트(PHP). 사이트맵의 /kr/shop/<N> 을 한 벌씩 연다.

상품 페이지 HTML(서버가 그린 것)에 있는 것:
- JSON-LD Product — 이름·품번(sku)·값(KRW)·사진 3장·availability(상품 전체 품절). 페이지의 JS 가
  같은 판단을 쓴다: stock_status 가 STSO·STSC 이면 OutOfStock.
- #details — 소재 정보 · 제품 정보 · 취급안내 (글).
- .vs-product-data — Virtusize 용 숨은 실측표(사이즈 · 부위 · cm 가 한 줄씩). 사이즈 이름도 여기서 얻는다.

사이즈별 재고는 HTML 에 없다. 페이지가 /_api/goods/detail/get 을 불러 그리는데 robots.txt 가
`Disallow: /_api/` 라 부르지 않는다(2026-09-27 확인). 그래서 사이즈별 품절은 모른다 —
상품 전체가 판매중이고 사이즈가 하나가 아니면 soldout_unknown 을 단다.

사이트맵에 없는 번호(/kr/shop/5000 따위)는 JSON-LD 도 이름도 비어 온다 — 숨긴 상품이다.
"""
import html as _html
import json
import re

from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import crawl_shopify as cs

BASE = "https://adererror.com"
# robots.txt 에 Crawl-delay 가 없다. 처음엔 규약 기본값 3초였는데 2,534쪽이라 한 판이 2시간 7분 —
# 주간 판의 묶음 제한(그때 110분)에 혼자 걸려 그 묶음이 통째로 취소됐다(2026-09-28). 카페24 수집기의
# 매장당 1초와 같은 결로 1.5초에 둔다(약 1시간).
PAGE_DELAY = 1.5


def list_urls(http, log) -> list[str] | None:
    r = cs.get_patient(http, f"{BASE}/sitemap.xml", log)
    if r is None or r.status_code != 200:
        log(f"  [ader-error] 사이트맵 받기 실패({getattr(r, 'status_code', '없음')})")
        return None
    # 같은 상품이 kr·en·jp 로 세 번 온다 — 값이 원화인 /kr/ 만 쓴다
    urls = re.findall(r"<loc>\s*(https://adererror\.com/kr/shop/\d+)\s*</loc>", r.text)
    return list(dict.fromkeys(urls)) or None


def _ld_product(s) -> dict:
    # 페이지 JS 안에도 「"@type": "Product"」 글자가 있어(SSR 이 비었을 때 채우는 코드) 글자로 찾으면 속는다.
    # <script type=application/ld+json> 을 JSON 으로 읽어 본다.
    for sc in s.find_all("script", type="application/ld+json"):
        try:
            j = json.loads(sc.string or "")
        except ValueError:
            continue
        if isinstance(j, dict) and j.get("@type") == "Product":
            return j
    return {}


def _img(u: str) -> str:
    u = _html.unescape((u or "").strip())
    return "https:" + u if u.startswith("//") else u


def _body_text(dd) -> str:
    b = dd.select_one(".body") if dd is not None else None
    if b is None:
        return ""
    t = b.get_text("\n", strip=True)
    return "\n".join(re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in t.splitlines() if ln.strip())


def _size_rows(s) -> tuple[list[str], dict[str, dict[str, str]]]:
    """숨은 실측표는 「S · 총장 · 63」처럼 한 줄에 칸 하나다. 사이즈 차례와 {사이즈: {부위: 값}} 으로 모은다."""
    names: list[str] = []
    cells: dict[str, dict[str, str]] = {}
    box = s.select_one(".vs-product-data")
    if box is None:
        return names, cells
    for tr in box.select("tbody tr"):
        td = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(td) < 3 or not td[0]:
            continue
        if td[0] not in cells:
            names.append(td[0])
            cells[td[0]] = {}
        if td[1]:
            cells[td[0]][td[1]] = td[2]
    return names, cells


def _size_table(names: list[str], cells: dict[str, dict[str, str]]) -> dict:
    """가로 표(머리줄 = 부위)로 다시 짜서 cc.extract_size_any 에 태운다 — 부위 이름 고르기를 다른 매장과 같게."""
    parts: list[str] = []
    for n in names:
        for p in cells[n]:
            if p not in parts:
                parts.append(p)
    if not parts:
        return {}
    esc = _html.escape
    head = "<tr><th>Size</th>" + "".join(f"<th>{esc(p)}</th>" for p in parts) + "</tr>"
    body = "".join("<tr><td>" + esc(n) + "</td>" + "".join(f"<td>{esc(cells[n].get(p, ''))}</td>" for p in parts)
                   + "</tr>" for n in names)
    return cc.extract_size_any(f"<table>{head}{body}</table>")


def parse_page(html_text, url, slug, now, http) -> dict | None:
    m = re.search(r"/shop/(\d+)", url)
    if not m:
        return None
    s = BeautifulSoup(html_text, "lxml")
    ld = _ld_product(s)
    if not ld:
        return None                   # 숨긴 상품 — 이름·값이 비어 온다
    name = (ld.get("name") or "").strip()
    offer = ld.get("offers") or {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    try:
        price = int(float(str(offer.get("price") or "0").replace(",", "")))
    except ValueError:
        price = 0
    if not price:
        pw = s.select_one("#frm-goods .price-wrapper")
        mp = re.search(r"\d[\d,]*", pw.get_text(" ", strip=True)) if pw else None
        price = int(mp.group(0).replace(",", "")) if mp else 0
    if not name or not price:
        return None                   # 값을 못 읽은 줄은 쓰지 않는다
    avail = str(offer.get("availability") or "")
    soldout = bool(re.search(r"OutOfStock|SoldOut|Discontinued", avail))

    imgs: list[str] = []
    for u in ld.get("image") or []:
        u = _img(u)
        if u and u not in imgs:
            imgs.append(u)
    # JSON-LD 는 사진을 3장까지만 준다. og:image 가 다른 컷(누끼 _P_1 따위)이면 하나 더 얻는다.
    og = s.select_one('meta[property="og:image"]')
    ogu = _img(og.get("content")) if og else ""
    if ogu and re.search(r"/(pcs|Migration_Prd_Img)/", ogu) and ogu not in imgs:
        imgs.append(ogu)

    sec: dict[str, str] = {}
    for dt in s.select("#details > dt"):
        key = (dt.get("class") or [""])[0]
        sec[key] = _body_text(dt.find_next_sibling("dd"))
    material, info = sec.get("material", ""), sec.get("info", "")
    # 취급안내는 뺀다 — 주얼리에 「니트 소재 등의 옷감을 훼손」 같은 공통 문구가 있어 분류를 흐린다
    text = "\n".join(t for t in (material, info) if t)

    names, cells = _size_rows(s)
    size_table = _size_table(names, cells) if any(cells.values()) else {}
    opts = names

    spec = {"상품명": name}
    if ld.get("sku"):
        spec["품번"] = str(ld["sku"]).strip()
    if material:
        spec["소재"] = " ".join(material.splitlines())
    mc = re.search(r"(?im)^made\s+in\s+(.+)$", info)
    if mc:
        spec["제조국"] = mc.group(1).strip()
    # 제품 정보 첫 줄이 라인 이름이다(「ADERERRROR Main Collection」 — 매장 표기 그대로, R 이 셋)
    cats = [ln for ln in info.splitlines()[:2] if re.search(r"(?i)collection|significant|essence", ln)][:1]

    d = {
        "product_no": int(m.group(1)),
        "name": name,
        "price": price,
        "soldout": soldout,
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": f"{BASE}/kr/shop/{m.group(1)}",
        "description": text,
        "description_source": "adererror",
        "detail_text": text,
        "size_table": size_table,
        "spec": spec,
        "detail_images": [],          # 상세 영역에 그림이 없다 — 설명은 글뿐
        "options": opts,
        "soldout_options": list(opts) if soldout else [],
        "category_nos": [],
        "category_names": cats,
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "adererror",
    }
    # 상품 전체는 판매중인데 어느 사이즈가 품절인지 모른다(사이즈별 재고는 /_api/ 뒤에만 있다).
    # 사이즈가 하나뿐이면 상품 전체 표시가 곧 그 사이즈다. 사이즈를 못 읽은 것(시그니피컨트 옷은 숨은
    # 실측표가 없다 — 페이지가 ADERERROR·ESSENCE 만 Virtusize 에 싣는다)도 모름으로 둔다.
    if not soldout and len(opts) != 1:
        d["soldout_unknown"] = True
    return d
