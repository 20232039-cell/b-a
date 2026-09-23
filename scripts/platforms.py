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

# 상품 페이지의 meta description 을 상품 설명으로 쓰는 매장. 렉토는 body_html 이 실측표뿐이고 진짜 설명이
# 여기에 있다(표본 6벌 모두 제목과 맞았다). **Hyein Seo 는 쓰지 않는다** — 매장이 다른 상품 설명을
# 복사해 둔 칸이 있다(니트 미니 원피스의 meta 가 「Multi-strap, adjustable button closure …」).
SHOPIFY_META_DESC: set[str] = {"recto"}

# 상품 페이지까지 여는 매장 — 목록 API 에 없는 것이 페이지에만 있는 곳(렉토 설명 · PAF 소재·실측 탭).
# Hyein Seo 는 목록 API 글에 혼용률(159벌 중 158)과 실측표(149)가 다 있어 열지 않는다. 페이지를
# 여는 만큼 매장에 짐이 되고, 봇 이름으로 연 상품 페이지에는 매장이 429 를 준다(2026-09-23 실측).
SHOPIFY_PAGES: set[str] = {"recto", "post-archive-faction"}

# 걷기는 하지만 앱에는 아직 안 내보내는 매장 — export_app_data 가 뺀다.
# 렉토·Hyein Seo·PAF 는 첫 판에서 값·사진·품절은 찼지만 설명·소재·실측이 덜 찼다: 렉토는 진짜 상품
# 설명이 상품 페이지의 meta description 에만, PAF 는 소재·실측표가 상품 페이지의 접이식 탭
# (메타필드)에만 있어 목록 API(products.json)로는 안 온다(2026-09-23 사람: 「사이즈, 상세설명,
# 스펙 수집이 덜 된 거 같은데」). 상품 페이지까지 읽고 나면 지운다.
APP_HOLD: set[str] = {"recto", "hyein-seo", "post-archive-faction"}
