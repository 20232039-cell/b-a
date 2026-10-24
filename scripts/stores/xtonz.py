"""XTONZ — FOLIX Cafe24 편집숍. 567 분류만 걷는다(하위 메뉴/사이트맵 탐색 금지).

2026-10-22 목록 1~2쪽·상세 3개 curl 확인. 목록 카드와 상세 양쪽에서 XTONZ를
확인하고 공통 Cafe24 상세 해석기를 재사용한다. 이미지뿐인 실측/혼용률은 추정하지
않고 상세 이미지 주소를 남긴다. 공개 HTML 외 /exec/front/ 옵션 API는 부르지 않는다.
"""
from urllib.parse import parse_qs, urlparse, urljoin

from bs4 import BeautifulSoup

import crawl_cafe24 as cc
from stores import _brand_mall as mall

BASE = 'https://folixstore.com'
CATEGORY = 567
LIST = f'{BASE}/product/list.html?cate_no={CATEGORY}'
PAGE_DELAY = 1.1


def product_url(no):
    return f'{BASE}/product/detail.html?product_no={no}&cate_no={CATEGORY}'


def parse_list(html):
    soup = BeautifulSoup(html, 'lxml')
    container = soup.select_one('.xans-product-listnormal')
    paging = soup.select_one('.xans-product-normalpaging')
    if container is None or paging is None:
        return None
    urls = []
    for card in container.select('.prdList > li'):
        brand = card.select_one('[rel="브랜드"]')
        if brand is None:
            return None
        if brand.get_text(strip=True).upper() != 'XTONZ':
            continue
        link = card.select_one('a[href*="product_no="], a[name^="anchorBoxName_"]')
        no = cc.product_no_of(link.get('href', '')) if link else None
        if no is None:
            return None
        urls.append(product_url(no))
    pages = []
    for link in paging.select('a[href]'):
        args = parse_qs(urlparse(link['href']).query)
        if args.get('cate_no') == [str(CATEGORY)] and args.get('page', [''])[0].isdigit():
            pages.append(int(args['page'][0]))
    if not pages:
        return None
    return list(dict.fromkeys(urls)), max(pages)


def list_urls(http, log=print, *, max_pages=None):
    if not mall.allowed(http, BASE, LIST, log):
        return None
    seen = {}
    last = 1
    for page in range(1, (max_pages or 100) + 1):
        r = mall.get(http, f'{LIST}&page={page}', log)
        parsed = parse_list(r.text) if r is not None else None
        if parsed is None:
            log(f'  [xtonz] 목록 {page}쪽 실패 — 상태 보존')
            return None
        urls, last = parsed
        new = [u for u in urls if u not in seen]
        if not new or len(new) != len(urls):
            log('  [xtonz] 빈/중복 목록 — 상태 보존')
            return None
        seen.update(dict.fromkeys(new))
        log(f'  [xtonz] {page}/{last}쪽 {len(urls)}개, 고유 {len(seen)}개')
        if page >= last:
            return list(seen)
    return list(seen) if max_pages is not None else None


def parse_page(html, url, slug, now, http=None):
    parsed = urlparse(url)
    if parsed.netloc != urlparse(BASE).netloc or parse_qs(parsed.query).get('cate_no') != [str(CATEGORY)]:
        return None
    no = cc.product_no_of(url)
    if no is None:
        return None
    soup = BeautifulSoup(cc.strip_html_comments(html), 'lxml')
    brand = soup.select_one('.infoArea .prd_brand_css .con')
    if brand is None:
        return None
    if brand.get_text(strip=True).upper() != 'XTONZ':
        return {'product_no': no, '_skip': '다른 브랜드'}
    shop = cc.Shop(slug, BASE, 'UNISEX', names=('XTONZ', '엑스톤즈'),
                   categories={CATEGORY: 'XTONZ'}, membership={no: {CATEGORY}})
    row = cc.parse_detail(html, url, shop, http=http)
    if row is None or row['product_no'] != no:
        return None
    # 공통 파서가 배너/다른 브랜드 이미지까지 줍지 않게 실제 상품 설명 칸만 쓴다.
    detail = soup.select_one('#prdDetail .product_detail')
    if detail is not None:
        row['detail_images'] = list(dict.fromkeys(urljoin(BASE, u) for im in detail.select('img')
            if (u := im.get('ec-data-src') or im.get('data-src') or im.get('src')) and not u.startswith('data:')))
    row.update(source_url=product_url(no), source_currency='KRW', platform='cafe24-brand',
               category_nos=[CATEGORY], category_names=['XTONZ'], brand_slug=slug, crawled_at=now)
    # 이 몰은 SIZE GUIDE가 주석 처리되어 있다. 없는 표/혼용률을 상품명으로 추정하지 않는다.
    if not row.get('size_table'):
        row['size_unavailable'] = 'HTML 실측표 없음 — 상세 이미지 확인 필요'
    if not row.get('spec', {}).get('소재'):
        row['material_unavailable'] = 'HTML 혼용률 없음 — 상세 이미지 확인 필요'
    return row
