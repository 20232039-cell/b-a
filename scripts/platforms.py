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
    # 2026-10-01 사람이 성별을 판정한 새 매장(NEW_1001) — meta.json 이 KR · KRW, products.json 이 원화로 열린다(345 · 589벌).
    # thug-club.com 은 thugclub.store 로 넘어간다. 실측표·소재 탭은 상품 페이지에만 있다(SHOPIFY_PAGES · page_size_html).
    "sansan-gear": "https://sansangear.com",
    "thug-club": "https://thugclub.store",
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
    # 2026-10-01 새 매장 — sitemap.xml 상품 549줄. 상세는 거의 그림이고, 그림이 무신사 · 29CM 서버(msscdn · 29cm)에 있다.
    "travel": "https://travelwebsite.kr",
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
    # 2026-09-28 relay 005 — 「카페24로 보임」이던 다섯 곳이 국내에서 열어 보니 imweb 이었다(stores/_imweb.py).
    # 사람 PC 의 Claude 세션이 해석기를 짜서 걷어 왔다. 매장이 해외 IP 를 막아 CI 에서는 사이트맵부터 못 받는다 —
    # list_urls 가 None 이라 가드레일이 상태를 안 바꾼다(값·재고가 굳는다). 국내 러너(작업 #11)가 서기 전까지.
    "eenk": "https://eenk.co.kr",
    "label-archive": "https://label-archive.com",
    "my-joyful-decisions": "https://www.myjoyfuldecisions.com",
    "preoccupy": "https://www.preoccupy-center.com",
    "taille": "https://taille.kr",
    # 2026-10-01 사람이 성별을 판정한 새 매장(NEW_1001) 가운데 카페24 · Shopify · 식스샵이 아닌 곳. 해석기 머리말에 길과 한계.
    "rolarola": "https://www.rolarola.com",               # 위사몰 — 목록(rows=200) · 실측 · 소재는 상세 그림(OCR)
    "thisisneverthat": "https://thisisneverthat.com",     # 헤드리스 Shopify(국내몰 ko) — products.json + Storefront 실측
    "we11done": "https://we11-done.com",                  # 고도몰5 — 국내몰은 /us/ 없는 주소 · 성별 칸 목록 ajax
    # 아래 넷은 이 컨테이너(미국)에서 첫 화면부터 403 이다 — eenk · taille 와 **같은 차단 화면**(CloudFront · S3,
    # ETag 2b9893bf…)이고 이름 서버도 같은 hostcocoa 무리라 imweb 국외 차단으로 본다. 해석기는 _imweb 을 묶기만 했고
    # **아직 한 번도 열어 보지 못했다** — 국내 PC 에서 `stores/_try.py <slug>` 로 먼저 확인한다. CI(해외)에서는
    # list_urls 가 None 이라 가드레일이 판을 버린다(데이터는 안 바뀐다).
    "samo-ondoh": "https://www.samoondoh.co.kr",
    "savage": "https://savage.co.kr",
    "urago": "https://u-rago.com",
    "welter-experiment": "https://welter-experiment.com",
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
# 2026-10-01 thug-club(소재 · 세탁 탭 + 실측 <table>) · sansan-gear(실측 창)도 실측이 페이지에만 있다 — body_html 로
# 실측표가 읽힌 상품이 589벌 · 345벌 중 0벌이었다(crawl_shopify.page_size_html 주석).
SHOPIFY_PAGES: set[str] = {"recto", "post-archive-faction", "thug-club", "sansan-gear"}

# 남의 브랜드도 같이 파는 Shopify 매장 — 이 태그(소문자)가 붙은 상품만 걷는다(편집숍은 자체 상품만 — 사람 결정 2026-09-28).
# sansan-gear 는 345벌 중 27벌이 러닝 브랜드 TOJI(vendor TOJI · 태그 toji — 「TOJI의 다용도 하프 슬리브 티셔츠」,
# CNOC 물병)다. 자체 상품 318벌에는 모두 「san san gear」 태그가 있다(협업 SAN SAN X SEALSON · YUMINHA 포함, 2026-10-01).
SHOPIFY_OWN_TAG: dict[str, str] = {"sansan-gear": "san san gear"}

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
# 2026-09-30 사람이 아홉 곳을 풀었다(「너 추천대로 하고 풀어도 되는건 풀자」). 판매중 옷 기준 실측 · 소재 · 상세가 모두
# 95% 넘은 곳 — generalidea 99.6 · khakis 100 · areuban 100 · antome 100 · arend 99.6 · le 99.3 · minju-kim 96.9 ·
# alyss 95.3 · doucan 97.2(대표색만 21%). 국외 차단 여섯 곳(anglan · PAGES 의 imweb 다섯)은 데이터는 찼어도
# 해외 CI 가 주간 갱신을 못 해 품절 · 값이 9/28 에 굳어 있어 묶어 둔다.
RELEASED_0930: set[str] = {"generalidea", "khakis", "areuban", "antome", "arend", "le", "minju-kim", "alyss", "doucan"}
NEW_HOLD: set[str] = ({"egnarts", "freckle", "eudon-choi", "goen-j", "finoacinque", "merely-made", "polyteru", "anglan"}
                      | set(PAGES)) - RELEASED_0930
# anglan 은 해외 IP 를 막는 카페24 매장이라 사람 PC(국내)에서 걷어 왔다(2026-09-28, 코덱스 중계 005 · 638벌).
# CI(해외)에서는 주간 갱신이 목록을 못 받아 가드레일이 상태를 안 바꾼다 — 품절·값이 굳는다. 국내 러너(작업 #11)가 서기 전까지.
# 같은 날 PC 에서 연 나머지 다섯 곳은 imweb 이었고 PAGES 에 올렸다(paco-sply 도 imweb — 아직 안 걷음).
# 2026-10-01 사람이 남은 32곳을 판정했다(둘다 13 · 남성 4 · 여성 3 · 제외 11 · 보류 1)하고 「오늘 새로운 브랜드도 수집하자」고 했다.
# 처음 걷는 곳은 9/27 처럼 사람이 값 · 설명 · 실측을 보기 전까지 앱에 안 내보낸다.
NEW_1001: set[str] = {"rolarola", "samo-ondoh", "sansan-gear", "savage", "sieg", "studio-tomboy", "system", "thisisneverthat",
                      "thug-club", "tngt", "travel", "urago", "we11done", "welter-experiment"}
NEW_HOLD |= NEW_1001
# 「제외」 판정 가운데 이미 걷혀 있던 곳 — rocket-x-lunch(10-01) · bourie(9/27 판정인데 빠져 있었다, 950벌 전부 품절). 앱에만 안 내보낸다.
APP_HOLD: set[str] = {"numbering", "kijun", "crump", "jeanbach", "maison-marais", "tripleroot",
                      "rocket-x-lunch", "bourie"} | NEW_HOLD
