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
    # 2026-09-27 사람이 성별을 판정한 새 매장 — products.json 이 열리는 것을 확인했다(96 · 250벌+)
    "eudon-choi": "https://www.eudonchoi.com",
    "goen-j": "https://kr.goenj.com",
}

# slug → 식스샵 가게 주소. `<주소>/sitemap.xml` 에 상품 주소가 다 있다(crawl_sixshop 주석).
# 아스트라 D 가 문을 찾아 줬고(2026-09-23) 내가 사이트맵·상품 페이지를 열어 확인했다.
SIXSHOP: dict[str, str] = {
    "forcesensitive": "https://forcesensitive.kr",
    "gonak": "https://www.gonak.co.kr",
    "ir-ryu": "https://www.ilryu.kr",
    # 2026-09-27 사람이 성별을 판정한 새 매장 — sitemap.xml 의 상품 주소 133 · 382 · 2,356줄을 확인했다
    "finoacinque": "https://www.finoacinque.co.kr",
    "merely-made": "https://www.merelymade.com",
    "polyteru": "https://www.polyteru-store.com",
}

# slug → 가게 주소. 틀이 매장마다 달라(고도몰 · 위사몰 · 메이크샵 · Cargo · 직접 만든 사이트) 매장별 해석기
# (scripts/stores/<slug>.py)가 페이지를 읽고, 걷고 합치는 일은 crawl_pages 가 한다(그 머리말).
# 2026-09-27 사람이 성별을 판정한 매장 가운데 앞의 셋 어디에도 안 드는 여덟 곳 — 해석기마다 머리말에
# 목록을 얻는 길과 한계를 적어 두었다. 카키스·인세인개러지는 편집숍이라 자체 상품만 걷는다.
# 림팍은 가게를 아직 안 열었다(LAUNCHING SOON) — 0벌로 돌다가 상점이 켜지면 판을 멈추고 알린다.
PAGES: dict[str, str] = {
    "concepts1one": "https://www.concepts1one.co.kr",     # 위사몰 — 사이트맵 · 복제 상품은 원본 번호로 접는다
    "insane-garage": "https://www.insanegarage.shop",     # 고도몰5 편집숍 — ALL ITEM 목록
    "ader-error": "https://adererror.com",                # 자체 — 사이트맵 /kr/shop
    "khakis": "https://khakis2020.com",                   # 헤드리스 Shopify 편집숍 — products.json + Storefront
    "doucan": "https://doucan.net",                       # 자체(Spring) — SHOP 칸 목록 ajax
    "minju-kim": "https://www.minjukim.co",               # 자체(dcore) — View all 쪽 넘김
    "carlyn": "https://www.carlynmall.com",               # 메이크샵 — 목록 JSON
    "limpark": "https://limparkofficial.com",             # Cargo — 상점 준비 중
}

NOT_CAFE24: set[str] = set(SHOPIFY) | set(SIXSHOP) | set(PAGES)


def crawl_other(http, slug: str, log=print) -> dict:
    """카페24 가 아닌 매장을 제 수집기로 — crawl_cafe24 · weekly_update 가 부른다."""
    if slug in SHOPIFY:
        import crawl_shopify
        return crawl_shopify.crawl_one(http, slug, log)
    if slug in SIXSHOP:
        import crawl_sixshop
        return crawl_sixshop.crawl_one(http, slug, log)
    if slug in PAGES:
        import crawl_pages
        return crawl_pages.crawl_one(http, slug, log)
    raise KeyError(slug)

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
# 식스샵 셋(고낙·포스센스티브·일류)은 첫 수집이라 사람이 값·설명·실측을 보기 전까지 같이 묶어 두었다.
# 2026-09-24 사람이 여섯 곳을 풀었다(「6곳만 풀자. 못 채운 건 계속 채우고」) — 그때 실측 렉토 321/327 ·
# Hyein 100 · PAF 95/98 · 고낙 100 · 포스센스티브 57/59 · 일류 56/58, 소재 87~100(포스센스티브 65).
# 넘버링(카페24, 주얼리)은 메뉴가 자바스크립트라 0벌이었다가 이번에 507벌이 걷혔는데, 매장이 품절이라는
# 100벌 중 87벌이 옵션에 품절 표시가 없어 판매중으로 나간다(is_soldout 주석). 품절 판정을 사람이 정할 때까지 묶는다.
# 기준(kijun)은 공식몰을 닫았다(2026-09-27 사람: 「기준은 아예 공식몰을 종료했네.. 목록에서 뺄게」 · 「앱에서도 빼고」).
# 그때 752벌 중 판매중 22벌. 걷은 자료는 두고 앱에만 안 내보낸다 — 다시 열면 여기서 지운다.
# 2026-09-27 사람이 성별 판정 페이지에서 「목록에서 뺀다」를 누른 매장 가운데 이미 걷혀 있던 곳 —
# crump 780 · jeanbach 31 · maison-marais 1 · tripleroot 1벌. 걷은 자료는 두고 앱에만 안 내보낸다.
# 2026-09-27 밤 처음 걷는 매장 13곳과 PAGES 여덟 곳 — 그날 사람이 성별을 판정했고 「상품 수집 함 하자」고 했다. 식스샵 셋 때처럼
# 사람이 값·설명·실측을 보기 전까지 앱에 안 내보낸다. 게시 판이 수집보다 먼저 돌든 나중에 돌든 앱이 같게.
NEW_HOLD: set[str] = {"alyss", "antome", "arend", "areuban", "egnarts", "freckle", "generalidea", "le",
                      "eudon-choi", "goen-j", "finoacinque", "merely-made", "polyteru"} | set(PAGES)
APP_HOLD: set[str] = {"numbering", "kijun", "crump", "jeanbach", "maison-marais", "tripleroot"} | NEW_HOLD
