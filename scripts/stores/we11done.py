"""웰던(we11done) — 고도몰5(mplshop 틀). 성별 칸 목록(ajax)으로 상품 주소를 받고 상품 페이지를 한 벌씩 읽는다.

2026-10-01 첫 시험. **국내몰 주소는 앞에 나라 칸이 없는 쪽이다.** we11-done.com/ 은 방문자 나라를 보고
/us/main/index.php(해외몰, 달러)로 넘기지만, /main/index.php · /goods/goods_list.php · /goods/goods_view.php 를
바로 부르면 해외 IP(이 컨테이너 — 미국)에서도 「웰던 공식 온라인 스토어」 원화 화면이 온다. /us/ 주소는 쓰지 않는다.

robots.txt 는 `*` 에 아무것도 막지 않는다(Crawl-delay 10 은 이름 붙은 봇 무리에만).

목록: 성별 맨 윗칸 여성(008) · 남성(006) — 신발 · 가방 · 주얼리까지 모두 그 아래다. 화면은 「더보기」가
`goods_list.php?mode=data&cateCd=…&cateType=cate&page=N` 을 8벌씩 부르는데, pageNum=100 을 붙이면 한 쪽에 100벌이
온다(여성 TOTAL 1,114 · 남성 915 — 겹치는 옷이 많다). 두 칸에 다 든 옷은 매장이 둘 다 입는 옷이라고 한 것이라
category_names 에 「여성」 「남성」을 같이 단다(cate_gender 가 유니섹스로 읽는다).

상품 페이지(정적 HTML)에 다 있다:
- 이름 .item_detail_tit h3 · 칸 경로 .item_category_tit(「남성 / 레디 투 웨어 / 스웨트셔츠」)
- 값 input[name=set_goods_price](판매가) · set_goods_fixedPrice(정가) · 재고 set_goods_stock
- 사이즈 칩 .prod-option .sel-opts — 품절 칩에 class soldout(XS · XL 품절, 1000002398)
- 사진 .item_photo_big .view-pc img · 설명 .goods_info_cont(특징 줄 · 「겉감 - 코튼 100%」 · 프로덕트 코드)
- 실측 .layer-sizeguide table — 머리 줄이 사이즈, 줄마다 부위다(Height · Shoulder · Chest · Hem · Sleeve /
  Height · Waist · Hip · Hem). 같은 창의 재는 자리 그림(spacsample/6.jpg)이 **Height 를 옆목점~밑단 = 총장**으로
  그린다. 우리 사전에서 height 는 모델 키라(size_labels.json 에 없다) 그대로 두면 총장이 빠진다 — Length 로 바꿔 읽는다.
  가방 · 모자는 표가 없다.
"""
import html as _html
import re

from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import crawl_shopify as cs

BASE = "https://we11-done.com"
PAGE_DELAY = 3.0          # 1,222벌 — 4초면 한 판 80분이 넘는다. 이름 붙은 봇의 Crawl-delay 10 은 우리 몫이 아니다(머리말)
GENDER_CATES = {"008": "여성", "006": "남성"}

# list_urls 가 채우고 parse_page 가 읽는다 — 상품 번호 → 그 옷이 든 성별 칸 이름들
_GENDER_OF: dict[int, list[str]] = {}


def list_urls(http, log=print) -> list[str] | None:
    seen: dict[int, list[str]] = {}
    for cate, label in GENDER_CATES.items():
        total = None
        got = 0
        for page in range(1, 60):
            u = f"{BASE}/goods/goods_list.php?mode=data&cateCd={cate}&cateType=cate&page={page}&pageNum=100"
            r = cs.get_patient(http, u, log)
            if r is None or r.status_code != 200:
                # 한 쪽이라도 못 받으면 목록이 모자란다 — 모자란 목록으로 걸면 멀쩡한 옷이 「목록에 없음」이 된다
                log(f"  [we11done] 목록 {cate} {page}쪽 받기 실패({getattr(r, 'status_code', '없음')})")
                return None
            nos = list(dict.fromkeys(int(x) for x in re.findall(r'class="item_cont goods_list_info".*?goodsNo=(\d+)',
                                                                 r.text, re.S)))
            if not nos:
                break
            for no in nos:
                seen.setdefault(no, [])
                if label not in seen[no]:
                    seen[no].append(label)
            got += len(nos)
            if len(nos) < 100:
                break
        # 첫 화면의 TOTAL 과 대 본다 — 목록이 갑자기 줄면(틀이 바뀜) 판을 버리는 게 낫다
        r0 = cs.get_patient(http, f"{BASE}/goods/goods_list.php?cateCd={cate}", log)
        m = re.search(r'id="totals">([\d,]+)', r0.text) if r0 is not None and r0.status_code == 200 else None
        total = int(m.group(1).replace(",", "")) if m else None
        log(f"  [we11done] {label}({cate}) 목록 {got}벌 / 화면 TOTAL {total}")
        if total and got < total * 0.9:
            log(f"  [we11done] 목록이 TOTAL 보다 많이 적다 — 판을 버린다")
            return None
    if not seen:
        log("  [we11done] 목록이 비었다")
        return None
    _GENDER_OF.clear()
    _GENDER_OF.update(seen)
    return [f"{BASE}/goods/goods_view.php?goodsNo={no}" for no in seen]


def _won(v) -> int:
    try:
        return int(float(str(v or "0").replace(",", "")))
    except ValueError:
        return 0


def _val(s: BeautifulSoup, name: str) -> str:
    el = s.select_one(f'input[name="{name}"]')
    return (el.get("value") or "").strip() if el is not None else ""


def _lines(el) -> str:
    """설명 칸 글 — 줄머리 「- 」가 따로 <span class="slash"> 라 get_text("\n") 로는 「-」와 글이 두 줄로 갈린다.
    <br> · 칸 끝만 줄바꿈으로 보고 나머지 꼬리표는 그냥 지운다. 주석(<!-- 옛 설명 -->)은 뺀다."""
    if el is None:
        return ""
    h = re.sub(r"(?s)<!--.*?-->", "", str(el))
    h = re.sub(r"(?i)<br\s*/?>|</(?:p|div|h\d|li)>", "\n", h)
    t = _html.unescape(re.sub(r"<[^>]+>", "", h))
    out = []
    for ln in t.splitlines():
        ln = re.sub(r"[ \t\xa0]+", " ", ln).strip()
        if ln:
            out.append(re.sub(r"^-\s*", "- ", ln))
    return "\n".join(out)


def _size_table(s: BeautifulSoup) -> dict:
    tbl = s.select_one(".layer-sizeguide table")
    if tbl is None:
        return {}
    h = str(tbl)
    # 재는 자리 그림이 Height 를 총장으로 그린다(머리말) — 줄 머리 칸의 「Height」만 바꾼다
    h = re.sub(r"(?i)(<t[dh][^>]*>\s*)height(\s*</t[dh]>)", r"\1Length\2", h)
    return cc.extract_size_any(h)


def parse_page(html_text: str, url: str, slug: str, now: str, http=None) -> dict | None:
    if 'id="frmView"' not in html_text:
        return None                       # 없는 상품 · 오류 화면(「잘못된 접근입니다」)
    s = BeautifulSoup(html_text, "lxml")
    m = re.search(r"goodsNo=(\d+)", url)
    if not m:
        return None
    no = int(m.group(1))
    h3 = s.select_one(".item_detail_tit h3")
    name = h3.get_text(" ", strip=True) if h3 is not None else ""
    price = _won(_val(s, "set_goods_price"))
    listed = _won(_val(s, "set_goods_fixedPrice"))
    if not name or not price:
        return None                       # 값을 못 읽은 줄은 쓰지 않는다 — 0원 옷보다 빈 줄이 낫다

    opts: list[str] = []
    sold: list[str] = []
    for o in s.select(".prod-option .sel-opts"):
        v = o.get_text(" ", strip=True)
        if not v or v in opts:
            continue
        opts.append(v)
        if "soldout" in (o.get("class") or []):
            sold.append(v)
    stock = _val(s, "set_goods_stock")
    whole_sold = (bool(opts) and len(sold) == len(opts)) or (stock.isdigit() and int(stock) == 0)

    imgs: list[str] = []
    for im in s.select(".item_photo_big .view-pc img"):
        u = (im.get("src") or "").strip()
        if u and u not in imgs:
            imgs.append(u)
    og = s.select_one('meta[property="og:image"]')
    if not imgs and og is not None and og.get("content"):
        imgs.append(og["content"].strip())

    info = s.select_one(".goods_info_cont")
    text = _lines(info)
    detail_imgs = []
    for im in s.select(".detail_cont img"):
        u = (im.get("src") or im.get("data-src") or "").strip()
        if u and u not in detail_imgs:
            detail_imgs.append(u)

    spec = {"상품명": name}
    mc = re.search(r"프로덕트 코드\s*:?\s*\n?\s*([A-Z0-9-]{6,})", text)
    if mc:
        spec["품번"] = mc.group(1)
    mats = [ln for ln in text.splitlines() if re.search(r"\d{1,3}\s*%", ln) and re.search(r"[가-힣A-Za-z]", ln)]
    if mats:
        spec["소재"] = " / ".join(re.sub(r"^-\s*", "", x) for x in mats)

    path = s.select_one(".item_category_tit")
    cats = [c.strip() for c in (path.get_text(" ", strip=True).split("/") if path is not None else []) if c.strip()]
    for g in _GENDER_OF.get(no, []):
        if g not in cats:
            cats.append(g)

    d = {
        "product_no": no,
        "name": name,
        "price": price,
        "soldout": whole_sold,
        "image_url": imgs[0] if imgs else "",
        "gallery": imgs,
        "source_url": f"{BASE}/goods/goods_view.php?goodsNo={no}",
        "description": text,
        "description_source": "godomall",
        "detail_text": text,
        "size_table": _size_table(s),
        "spec": spec,
        "detail_images": detail_imgs,
        "options": opts,
        "soldout_options": sold,
        # 품절 칩도 class soldout 으로 그대로 그려지므로 칩 목록이 그 상품의 사이즈 전부다(size_from_ocr.trim_unsold_sizes)
        "options_all": list(opts),
        "category_nos": [],
        "category_names": cats,
        "brand_slug": slug,
        "crawled_at": now,
        "platform": "godomall",
    }
    if listed > price:
        d["price_listed"] = listed
    return d
