"""CUSTOMELLOW — KOLONMALL 브랜드관(2026-10-22 curl 목록 2쪽·상세 3개).

/Brands/CUSTOMELLOW/List/4942?page=N HTML의 ApolloSSRDataTransport 공개 초기
데이터를 읽는다. GraphQL·로그인·비공개 API는 부르지 않는다. 목록 brandV2의
4942/CW_B1 및 상세 supplierBrand를 확인한다. 다른 브랜드·추천 상품은 받지 않는다.
price.price는 판매가, wishPrice는 정가(쿠폰 예상구매가는 쓰지 않음).
영숫자 품번은 source_product_code에 보존한다. 공통 수집기가 정수 ID를 요구하므로
품번 SHA-256 앞 52비트를 product_no로 쓴다(프로세스·실행 순서와 무관, JS 안전정수).
"""
import hashlib
import json
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from stores import _brand_mall as mall

BASE = 'https://www.kolonmall.com'
LIST = BASE + '/Brands/CUSTOMELLOW/List/4942'
PAGE_DELAY = 1.1
_BRAND_CODE = 'CW_B1'
# JSON 문자열 안의 "undefined"는 그대로 두고 JS의 값 토큰만 null로 바꾼다.
_JS_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\bundefined\b')


def apollo_data(html):
    """JS 실행 없이 transport.push({...})에서 data 객체만 읽는다."""
    soup = BeautifulSoup(html, 'lxml')
    for script in soup.find_all('script'):
        text = script.string or ''
        if 'Symbol.for("ApolloSSRDataTransport")' not in text or '.push(' not in text:
            continue
        raw = _JS_TOKEN.sub(lambda m: 'null' if m.group() == 'undefined' else m.group(),
                            text.split('.push(', 1)[1])
        try:
            payload, _ = json.JSONDecoder().raw_decode(raw)
        except (ValueError, TypeError):
            continue
        for value in payload.get('rehydrate', {}).values():
            if isinstance(value, dict) and isinstance(value.get('data'), dict):
                yield value['data']
        for event in payload.get('events', []):
            data = (event.get('result') or {}).get('data')
            if event.get('type') == 'data' and isinstance(data, dict):
                yield data


def _data(html, key):
    return next((d[key] for d in apollo_data(html) if isinstance(d.get(key), dict)), None)


def product_no(code):
    return int(hashlib.sha256(code.encode('ascii')).hexdigest()[:13], 16)


def product_url(code):
    return f'{BASE}/Brands/CUSTOMELLOW/Product/{code}'


def parse_list(html):
    data = {}
    for chunk in apollo_data(html):
        for key in ('brandV2', 'productSearchCodes'):
            if isinstance(chunk.get(key), dict):
                data.setdefault(key, chunk[key])
        if len(data) == 2:
            break
    brand = data.get('brandV2') or {}
    products = data.get('productSearchCodes') or {}
    if (brand.get('code') != '4942' or brand.get('supplierBrandCode') != _BRAND_CODE
            or brand.get('brandUrl') != 'CUSTOMELLOW'
            or not isinstance(products.get('results'), list)):
        return None
    urls = []
    for p in products['results']:
        # supplierCode CW만으로는 다른 코오롱 브랜드와 구분할 수 없다.
        if p.get('supplierBrandName') not in ('커스텀멜로우', 'CUSTOMELLOW'):
            return None
        code = p.get('code', '')
        if not re.fullmatch(r'[A-Z0-9_-]+', code):
            return None
        urls.append(product_url(code))
    page = products.get('page') or {}
    if not isinstance(page.get('currentPage'), int) or not isinstance(page.get('totalCount'), int):
        return None
    return list(dict.fromkeys(urls)), page


def list_urls(http, log=print, *, max_pages=None):
    if not mall.allowed(http, BASE, LIST, log):
        return None
    seen = {}
    ids = {}
    for page in range(1, (max_pages or 100) + 1):
        r = mall.get(http, f'{LIST}?page={page}', log)
        parsed = parse_list(r.text) if r is not None else None
        if parsed is None:
            log(f'  [customellow] 목록 {page}쪽 해석 실패 — 상태 보존')
            return None
        urls, meta = parsed
        if meta['currentPage'] != page:
            return None
        new = [u for u in urls if u not in seen]
        if (not new and len(seen) < meta['totalCount']) or len(new) != len(urls):
            log('  [customellow] 목록 중복/누락 — 상태 보존')
            return None
        for url in new:
            code = url.rsplit('/', 1)[-1]
            no = product_no(code)
            if no in ids and ids[no] != code:
                log('  [customellow] 상품 정수 ID 충돌 — 상태 보존')
                return None
            ids[no] = code
            seen[url] = None
        log(f'  [customellow] {page}쪽 {len(urls)}개, 누적 {len(seen)}/{meta["totalCount"]}')
        if len(seen) == meta['totalCount']:
            return list(seen)
        if len(seen) > meta['totalCount']:
            return None
    # 제한 수집은 시험용 호출에서만 쓴다. 기본 수집은 상한에 닿으면 판을 버린다.
    return list(seen) if max_pages is not None else None


def _stock(stock):
    """미확인 재고는 None. lowStock도 판매 가능이다."""
    stock = stock or {}
    if stock.get('stockLevelStatus') == 'outOfStock':
        return False
    if isinstance(stock.get('stockLevel'), (int, float)):
        return stock['stockLevel'] > 0
    if stock.get('stockLevelStatus') in ('inStock', 'lowStock'):
        return True
    return None


_LABELS = {'총길이': '총장', '어깨너비': '어깨', '소매길이': '소매',
           '가슴둘레': '가슴', '허리둘레': '허리', '엉덩이둘레': '엉덩이',
           '허벅지너비': '허벅지', '앞밑위길이': '밑위', '밑단너비': '밑단',
           '소매통너비': '소매통', '소매단너비': '소매부리'}


def size_table(groups):
    """사이즈 이름으로 정렬한다. 둘레는 앱의 단면 기준으로 /2, 원문도 별도 보존."""
    names = list(dict.fromkeys(s['legacySize'] for g in groups for s in g.get('sizeSpecs', [])
                               if s.get('legacySize')))
    out = {}
    for group in groups:
        raw_label = group.get('useRegnNm')
        label = _LABELS.get(raw_label)
        if not label:
            continue
        values = {s.get('legacySize'): s.get('sizeSpecVal') for s in group.get('sizeSpecs', [])}
        if not names or any(not re.fullmatch(r'\d+(?:\.\d+)?', str(values.get(n, ''))) for n in names):
            continue
        out[label] = [float(values[n]) / (2 if '둘레' in raw_label else 1) for n in names]
    return {'_names': names, **out} if out else {}


def _content(html):
    soup = BeautifulSoup(html or '', 'lxml')
    for el in soup.select('style,script'):
        el.decompose()
    return soup


def parse_page(html, url, slug, now, http=None):
    path = urlparse(url)
    match = re.fullmatch(r'/Brands/CUSTOMELLOW/Product/([A-Z0-9_-]+)', path.path)
    if path.netloc != urlparse(BASE).netloc or not match:
        return None
    code = match.group(1)
    detail = _data(html, 'productDetail')
    p = (detail or {}).get('product') or {}
    if p.get('code') != code or not p.get('name'):
        return None
    brand = p.get('supplierBrand') or {}
    if not brand.get('brandCode'):
        return None
    if brand.get('brandCode') != _BRAND_CODE or brand.get('brandUrl') != 'CUSTOMELLOW':
        return {'product_no': product_no(code), '_skip': '다른 브랜드'}
    price = p.get('price') or {}
    if price.get('currencyIso') != 'KRW':
        return None
    value, listed = price.get('price'), price.get('wishPrice')
    if not isinstance(value, (int, float)) or value <= 0 or int(value) != value:
        return None
    opts = {}
    for variant in p.get('variantOptions') or []:
        size = variant.get('legacySize') or next((q.get('value') for q in variant.get('variantOptionQualifiers', [])
                                                  if q.get('qualifier') == 'size'), None)
        available = _stock(variant.get('stock'))
        if not size or available is None:
            return None
        opts[size] = opts.get(size, False) or available
    available = _stock(p.get('stock'))
    if available is None:
        return None
    soldout = not available or (bool(opts) and not any(opts.values()))
    # 판매 중지된 상품의 재고 수가 남아 있어도 살 수 없다.
    status = p.get('productSaleStatus')
    if status != 'SALE_ACTIVE':
        return None  # 미확인 상태 코드를 임의로 품절로 바꾸지 않는다.
    soup = _content(p.get('description'))
    text = soup.get_text('\n', strip=True)
    spec = {'상품명': p['name'], '품번': code, '브랜드': 'CUSTOMELLOW'}
    for key, source in (('소재', 'fabric'), ('제조국', 'originCountry'), ('제조자', 'manufacturerName')):
        if p.get(source):
            spec[key] = p[source]
    gallery = list(dict.fromkeys(im['imageLZ']['url'] for im in p.get('images', [])
                                 if (im.get('imageLZ') or {}).get('url')))
    detail_imgs = [im.get('src') or im.get('data-src') for im in soup.select('img')]
    detail_imgs += [im['url'] for im in p.get('imageLLs', []) if im.get('url')]
    d = {'product_no': product_no(code), 'source_product_code': code,
         'name': p['name'], 'price': int(value), 'source_currency': 'KRW', 'soldout': soldout,
         'source_url': product_url(code), 'image_url': p.get('mainImageUrl') or (gallery[0] if gallery else ''),
         'gallery': gallery, 'description': text, 'description_source': 'kolonmall-apollo-ssr',
         'detail_text': '\n'.join(filter(None, [text, p.get('fabric')])), 'spec': spec,
         'detail_images': list(dict.fromkeys(u for u in detail_imgs if u and not u.startswith('data:'))),
         'size_table': size_table(p.get('sizeSpecs') or []), 'size_specs_raw': p.get('sizeSpecs') or [],
         'options': list(opts), 'options_all': list(opts),
         'soldout_options': [s for s, available in opts.items() if soldout or not available],
         'category_nos': [4942], 'category_names': ['CUSTOMELLOW'],
         'brand_slug': slug, 'crawled_at': now, 'platform': 'kolonmall'}
    if isinstance(listed, (int, float)) and listed > value and int(listed) == listed:
        d['price_listed'] = int(listed)
    return d
