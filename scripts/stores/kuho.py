"""KUHO — SSF SHOP 브랜드관. 2026-10-22 robots 금지로 구현/추가 수집 보류.

main·WOMEN 목록은 curl 200이지만 robots.txt의 User-agent:* Disallow:/가
LayerCatalog에 적용된다. 허용된 일부 봇의 이름을 빌리지 않는다. 현재는 가드레일만
등록해 Cafe24로 잘못 보내거나 수집 불가를 상품 0개로 쓰지 않는다. 정책/허가 확인 후
목록·상세를 실제로 검증하기 전까지 해석기를 추측해 만들지 않는다.
"""
from stores import _brand_mall as mall

BASE = 'https://m.ssfshop.com'
BRAND_SHOP_NO = 'BDMA07A02'
BRAND_SHOP_ID = 'WMBKF'
BRAND_URL = f'{BASE}/KUHO/main?brandShopNo={BRAND_SHOP_NO}&brndShopId={BRAND_SHOP_ID}'
PAGE_DELAY = 1.1


def list_urls(http, log=print):
    if mall.allowed(http, BASE, BRAND_URL, log):
        log('  [kuho] robots 허용으로 변경됨 — 실목록/상세 해석기 검증 필요, 상태 보존')
    return None


def parse_page(html, url, slug, now, http=None):
    return None
