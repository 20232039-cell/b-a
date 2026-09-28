"""imweb 매장 공용 해석기 — 매장 파일(stores/<slug>.py)은 BASE 만 두고 이것을 묶는다.

2026-09-28 사용자 PC(국내)에서 다섯 곳(eenk · label-archive · my-joyful-decisions · preoccupy · taille)을
열어 보고 짰다. 요청서(relay 005)에는 「카페24로 보임」이었지만 다섯 곳 다 imweb 이었다.

목록: 사이트맵의 /shop_view/N 은 **믿을 수 없다** — eenk 는 1,000개에서 잘리고 MJD 는 16개뿐인데
목록 화면에는 194개가 있었다. 그래서 사이트맵의 상품 주소에 사이트맵의 메뉴 화면들을 ?page= 로 넘기며
나온 상품 링크(/<메뉴>/?idx=N)를 더한다. 게시판 글(…&bmode=view&idx=N)은 idx 가 첫 인자가 아니라
걸리지 않는다. 상품이 아닌 번호가 섞여 와도 parse_page 가 JSON-LD Product 가 없으면 _skip 으로 넘긴다.

상품 페이지(/shop_view/?idx=N)에 거의 다 있다:
- JSON-LD Product — 이름 · 사진(cdn.imweb.me/thumbnail) · 값 · availability
- BreadcrumbList(여러 개) — 분류 이름
- .goods_summary — 요약 글(SIZE · COLOR · FABRIC …)
- <template id="prodDetailPC"> — 상세 본문 HTML(글 + 상세 그림 cdn.imweb.me/upload/…). 화면에서는
  자바스크립트가 이것을 #prod_detail_body 에 붙인다.
사이즈 옵션과 옵션별 품절만 따로 온다: POST /shop/load_option.cm (type=prod&prod_idx=N) 의 option_html.
X-Requested-With · Referer 가 없으면 빈 답이 온다. 품절 옵션은 글에 「(품절)」이 붙는다.
"""
import html as _html
import json
import re
import time
from functools import partial
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import crawl_pages

_PID = re.compile(r'href="(?:https?://[^/"]+)?/[^"?#]*\?idx=(\d+)"|/shop_view/\??(?:idx=)?(\d+)')


def _pids(text: str) -> list[int]:
    return [int(a or b) for a, b in _PID.findall(text)]


def list_urls(base: str, http, log=print) -> list[str] | None:
    r = http.get(base + "/sitemap.xml")
    if r is None or r.status_code != 200:
        log(f"  [{base}] sitemap 받기 실패({getattr(r, 'status_code', '없음')})")
        return None
    locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", r.text)
    ids: dict[int, None] = dict.fromkeys(_pids("\n".join(l for l in locs if "/shop_view" in l)))
    for page_url in (l for l in locs if "/shop_view" not in l):
        last: set[int] = set()
        for pg in range(1, 200):
            u = page_url + ("&" if "?" in page_url else "?") + f"page={pg}"
            pr = http.get(u)
            if pr is None or pr.status_code != 200:
                break                     # 메뉴 한 곳이 안 열려도 다른 메뉴·사이트맵으로 대개 채워진다
            got = set(_pids(pr.text))
            ids.update(dict.fromkeys(p for p in _pids(pr.text) if p not in ids))
            # 끝 쪽을 넘기면 imweb 은 마지막 쪽을 되풀이해 준다 — 「새 번호 없음」으로 멈추면 안 된다.
            # 첫 쪽이 사이트맵에 다 있던 taille 메뉴가 1쪽에서 멈춰 616개만 잡혔다(2026-09-28).
            if not got or got <= last or f"page={pg + 1}" not in pr.text:
                break
            last = got
    log(f"  [{base}] 상품 번호 {len(ids)}개 (사이트맵 + 메뉴 목록)")
    return [f"{base}/shop_view/?idx={p}" for p in ids]


def _post(http, url: str, data: dict, referer: str):
    """PoliteSession 에 POST 가 없어 같은 호스트 간격 규칙(http.delay)을 지키며 보낸다."""
    host = urlparse(url).netloc
    with http._lock_for(host):
        wait = http.delay - (time.monotonic() - http._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        http._last[host] = time.monotonic()
        http.requests_made += 1
        try:
            return http.s.post(url, data=data, timeout=cc.TIMEOUT,
                               headers={"X-Requested-With": "XMLHttpRequest", "Referer": referer})
        except requests.RequestException:
            return None


def fix_url(u: str) -> str:
    """「//호스트」→ https, 「http:/호스트」(eenk 상세 글에 420개)·「http:///」→ 빗금 둘. 브라우저가 그렇게 읽는다."""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    return re.sub(r"^(https?:)/*", r"\1//", u) if re.match(r"(?i)https?:", u) else u


def _ld(html_text: str) -> list[dict]:
    out = []
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html_text, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _num(s) -> int:
    m = re.search(r"\d[\d,]*", str(s or ""))
    return int(m.group(0).replace(",", "")) if m else 0


def _options(http, base: str, pid: int, referer: str) -> tuple[list[tuple[str, bool]], bool]:
    """(사이즈 옵션 [(값, 품절)], 옵션을 받았는지). 사이즈 축만 — 색 · 추가상품은 뺀다."""
    r = _post(http, base + "/shop/load_option.cm", {"type": "prod", "prod_idx": pid}, referer)
    if r is None or r.status_code != 200:
        return [], False
    try:
        h = r.json().get("option_html") or ""
    except ValueError:
        return [], False
    return _parse_options(h), True


def _parse_options(h: str) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []
    # 옵션 칸마다 .option_title 하나 + .dropdown-item 여러 개. 값은 item 안 첫 span.blocked(값 뒤 span 은 가격).
    # 그림 없는 틀(label-archive)은 span 에 margin-bottom-lg 가 없다. 칸 제목이 색 이름인 매장도 있어
    # (preoccupy 「BLACK」 → S · M) 제목이 SIZE 가 아니면 값이 모두 사이즈 꼴일 때만 사이즈 축으로 친다.
    s = BeautifulSoup(h, "lxml")
    for title in s.select(".option_title"):
        wrap = title.find_next_sibling(class_="form-select-wrap")
        if wrap is None:
            continue
        vals = []
        for item in wrap.select(".dropdown-item"):
            sp = next((x for x in item.select("span.blocked") if "no-margin" not in (x.get("class") or [])), None)
            v = sp.get_text(" ", strip=True) if sp else ""
            if v:
                vals.append((v, "(품절)" in item.get_text()))
        is_size = re.search(r"(?i)size|사이즈", title.get_text()) or (
            vals and all(_SIZEISH.match(v) for v, _ in vals))
        if is_size:
            for v, so in vals:
                if v not in [x for x, _ in out]:
                    out.append((v, so))
    return out


_SIZEISH = re.compile(r"(?i)^\s*(XXS|XS|S|M|L|XL|XXL|XXXL|[0-9]{1,3}|F|FREE|OS|O/S|ONE ?SIZE)\s*(\(.*\))?\s*$")


def parse_page(base: str, html_text: str, url: str, slug: str, now: str, http=None) -> dict | None:
    lds = _ld(html_text)
    prod = next((d for d in lds if d.get("@type") == "Product"), None)
    mi = re.search(r'"prod_idx":(\d+)', html_text)
    if prod is None or not mi:
        m = re.search(r"idx=(\d+)", url)
        return {"product_no": int(m.group(1)) if m else 0, "_skip": "상품 페이지 아님"}
    pid = int(mi.group(1))
    name = _html.unescape(prod.get("name") or "").strip()
    offers = prod.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    mp = re.search(r'"prod_price":(\d+)', html_text)
    price = int(mp.group(1)) if mp else _num(offers.get("price"))
    if not name or not price:
        return None
    s = BeautifulSoup(html_text, "lxml")
    sale = s.select_one(".pay_detail .sale_price")       # 할인 전 값(줄 그은 것)
    listed = _num(sale.get_text()) if sale is not None else 0

    imgs = prod.get("image") or []
    imgs = [imgs] if isinstance(imgs, str) else list(dict.fromkeys(imgs))

    cats: list[str] = []
    for d in lds:
        if d.get("@type") == "BreadcrumbList":
            for it in d.get("itemListElement") or []:
                if it.get("item"):              # 마지막 칸(상품 이름)은 item 주소가 없다
                    c = _html.unescape(it.get("name") or "").replace("\xa0", " ").strip()
                    if c and c not in cats:
                        cats.append(c)

    lines: list[str] = []
    summ = s.select_one(".goods_summary")
    if summ is not None:
        lines += [t.strip() for t in summ.get_text("\n", strip=True).splitlines() if t.strip()]
    tpl = re.search(r'<template id="prodDetailPC">(.*?)</template>', html_text, re.S)
    detail_html = tpl.group(1) if tpl else ""
    detail_imgs: list[str] = []
    ds = None
    if detail_html:
        ds = BeautifulSoup(detail_html, "lxml")
        lines += [t.strip() for t in ds.get_text("\n", strip=True).splitlines() if t.strip()]
        for im in ds.find_all("img"):
            u = fix_url(im.get("src") or im.get("data-src") or "")
            if u.startswith("http") and u not in detail_imgs:
                detail_imgs.append(u)
    text = "\n".join(dict.fromkeys(lines))

    table = cc.extract_size_any(detail_html) if ds is not None and ds.find("table") else {}
    if len(table) < 2:
        table = crawl_pages.size_table_from_text(text)

    opts, got_opts = _options(http, base, pid, url) if http is not None else ([], False)
    avail = str(offers.get("availability") or "")
    soldout = avail.endswith("OutOfStock") or (bool(opts) and all(so for _, so in opts))

    d = {
        "product_no": pid,
        "name": name,
        "price": price,
        "soldout": soldout,
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": f"{base}/shop_view/?idx={pid}",
        "description": text,
        "description_source": "imweb",
        "detail_text": text,
        "size_table": table,
        "spec": {"상품명": name},
        "detail_images": detail_imgs,
        "options": [v for v, _ in opts],
        "soldout_options": [v for v, so in opts if so],
        "category_nos": [],
        "category_names": cats,
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "imweb",
    }
    if listed > price:
        d["price_listed"] = listed
    if not avail and not got_opts:
        d["soldout_unknown"] = True
    return d


def bind(base: str):
    """매장 파일에서: list_urls, parse_page = _imweb.bind(BASE)"""
    return partial(list_urls, base), partial(parse_page, base)


if __name__ == "__main__":   # python -m stores._imweb — 옵션 틀 세 가지(2026-09-28 실물에서 줄인 것)
    def blk(title, items):
        return (f'<div class="option_title text-12">{title} <span class="option_require">*</span></div>'
                f'<div class="form-select-wrap"><div class="dropdown-menu">{items}</div></div>')
    taille = blk("SIZE", '<div class="dropdown-item "><a><span class="blocked margin-bottom-lg">1(UNISEX)</span>'
                         '<span class="no-margin blocked"><strong> 185,000원</strong></span> (품절) 재입고 알림</a></div>'
                         '<div class="dropdown-item "><a><span class="blocked margin-bottom-lg">2(UNISEX)</span></a></div>')
    label = blk("size", '<div class="dropdown-item "><div><a><span class="blocked">2</span>'
                        '<span class="no-margin blocked"><strong> 208,000원</strong></span></a></div></div>')
    preocc = blk("BLACK", '<div class="dropdown-item "><a><span class="blocked">S</span> (품절)</a></div>'
                          '<div class="dropdown-item "><a><span class="blocked">M</span></a></div>')
    color = blk("COLOR", '<div class="dropdown-item "><a><span class="blocked">Navy</span></a></div>')
    assert _parse_options(taille) == [("1(UNISEX)", True), ("2(UNISEX)", False)]
    assert _parse_options(label) == [("2", False)]
    assert _parse_options(preocc) == [("S", True), ("M", False)]
    assert _parse_options(color + label) == [("2", False)]
    assert fix_url("http:/a.com/x.jpg") == fix_url("http:///a.com/x.jpg") == "http://a.com/x.jpg"
    assert fix_url("//a.com/x.jpg") == "https://a.com/x.jpg" and fix_url("https://a.com//x") == "https://a.com//x"
    print("ok")
