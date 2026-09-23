"""카페24 가 아닌 매장 — 수집기가 따로 있다.

카페24 수집기(crawl_cafe24 · weekly_update · refetch_sizes)는 이 매장들을 건드리지 않고
제 수집기에 넘긴다. 카페24 방식으로 목록을 훑으면 0벌이 나오고, 상품 페이지를 카페24
방식으로 다시 읽으면 멀쩡한 값을 덮어쓴다.

주소는 아스트라가 매장마다 문을 찾아 준 것을 내가 받아 확인했다(2026-09-23,
store_collection_routes_15). 렉토는 앞문(www.recto.co)이 해외에서 「글로벌 스토어 준비 중」
화면만 보여 주고, 상품은 뒤의 Shopify(checkout.recto.co)에 있다 — 상품 주소도 그쪽이
한국어 상품 페이지를 그대로 준다.
"""

# slug → Shopify 가게 주소. `<주소>/products.json` 이 공개 상품 목록, `<주소>/products/<handle>` 이 상품 페이지다.
SHOPIFY: dict[str, str] = {
    "recto": "https://checkout.recto.co",
    "hyein-seo": "https://hyeinseo.com",
    "post-archive-faction": "https://postarchivefaction.com",
}

NOT_CAFE24: set[str] = set(SHOPIFY)
