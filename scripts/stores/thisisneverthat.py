"""디스이즈네버댓(thisisneverthat) — thisisneverthat.com 은 React SPA, 뒤는 헤드리스 Shopify 셋(ko · en · jp).

2026-10-01 첫 시험. 카키스(stores/khakis.py)와 같은 틀이다 — JS 묶음(/assets/index-*.js)에 나라별 Storefront 주소와
공개 토큰이 박혀 있고(MK = {ko: {uri, token}, en: …, jp: …}), 사이트는 api.country.is 로 방문자 나라를 보고 고른다.
**국내몰은 ko = thisisneverthatstore.myshopify.com** 이다(meta.json: 「thisisneverthat® KR」 · KR · KRW · 1,027벌).
en 은 thisisneverthat-intl(USD) 이라 쓰지 않는다. myshopify 쪽 주소는 나라와 상관없이 열려, 해외 CI 에서도 같은
국내몰을 받는다(이 컨테이너 — 미국 IP — 에서 확인).

- products.json(myshopify) — 값 · 정가 · 사이즈별 재고 · 사진 · 설명(소재 줄 「Shell : Cotton 100%」 포함).
  crawl_shopify.fetch_all · to_row 를 그대로 쓴다(원화 못 박기 · 소수점 값 거르기).
- Storefront GraphQL — products.json 에 안 오는 메타필드:
  · size-fit.values — 실측표. 「["pants","Length/102/104.5/107/109.5","Waist/36/…",…]」 — 첫 칸은 틀 이름(tee ·
    pants · hat …), 그 뒤 한 줄이 부위 하나다. 1,027벌 중 1,014벌에 있다. 값은 단면이다(Waist 36 = 허리 72 의 반).
  · model.info · model.info2 — 모델 키 · 입은 사이즈(「["182","L"]」). 몸 치수라 실측표에 넣지 않고 글로만 둔다.
  · operation.hidden — 「true」 8벌은 사이트에 안 보이는 상품이라 뺀다.
robots.txt(thisisneverthat.com · myshopify)는 /admin · /cart · /checkout 따위만 막는다.
vendor 는 브랜드가 아니라 묶음 이름이다(thisisneverthat · Collaboration · Carryover · 시즌) — 협업도 자체 상품이라 다 걷는다.
"""
import re
import time

import crawl_shopify as cs
from stores import khakis

BASE = "https://thisisneverthat.com"
SHOP = "https://thisisneverthatstore.myshopify.com"
GQL = f"{SHOP}/api/2025-01/graphql.json"
# 사이트 JS 묶음의 ko 토큰(공개 — 모든 방문자 브라우저가 쓴다). 바뀌면 401 → fetch_rows 가 None(판을 버린다).
TOKEN = "7ecaa2c65d1bc33c697ca48720eadae3"
PAGE_DELAY = 3.0
_PROMO = re.compile(r"%|(?i:season\s*off|family\s*sale|^gmc_|_\d|^\d{4}|\bend\b)")

_Q = """query($after: String) { products(first: 250, after: $after) {
  pageInfo { hasNextPage endCursor }
  edges { node { id
    sizeFit: metafield(namespace: "size-fit", key: "values") { value }
    model: metafield(namespace: "model", key: "info") { value }
    model2: metafield(namespace: "model", key: "info2") { value }
    hidden: metafield(namespace: "operation", key: "hidden") { value }
  } } } }"""


def _gql_meta(http, log) -> dict[int, dict] | None:
    """Storefront 메타필드를 250벌씩(khakis._gql_meta 와 같은 걸음 — 주소 · 토큰 · 물음만 다르다)."""
    out: dict[int, dict] = {}
    after = None
    for _ in range(40):
        time.sleep(http.delay)
        try:
            r = http.s.post(GQL, json={"query": _Q, "variables": {"after": after}}, timeout=60,
                            headers={"X-Shopify-Storefront-Access-Token": TOKEN, "Content-Type": "application/json"})
            http.requests_made += 1
            j = r.json()
        except Exception as e:
            log(f"  [thisisneverthat] Storefront API 실패: {type(e).__name__}: {e}")
            return None
        if r.status_code != 200 or j.get("errors") or not j.get("data"):
            log(f"  [thisisneverthat] Storefront API {r.status_code}: {str(j.get('errors'))[:200]}")
            return None
        p = j["data"]["products"]
        for e in p["edges"]:
            n = e["node"]
            out[int(n["id"].rsplit("/", 1)[-1])] = n
        if not p["pageInfo"]["hasNextPage"]:
            return out
        after = p["pageInfo"]["endCursor"]
    log("  [thisisneverthat] Storefront API 쪽수가 너무 많다 — 판을 버린다")
    return None


def _size_table(rows: list[str], sizes: list[str]) -> dict:
    """「Length/68/70/72/74」 줄들을 가로 표로 — khakis._size_table 과 같되, 끝의 빈 칸(「Arm/61/62.5/64/65.5/」 —
    Cable Knit Zip Polo)은 뗀다. 빈 칸을 값으로 세면 사이즈 수가 하나 많아져 사이즈 이름이 떨어진다."""
    clean = []
    for c in rows:
        parts = [x.strip() for x in c.split("/")]
        while len(parts) > 1 and not parts[-1]:
            parts.pop()
        clean.append("/".join(parts))
    return khakis._size_table(clean, sizes)


def fetch_rows(http, slug, now, log) -> list[dict] | None:
    prods = cs.fetch_all(http, SHOP, log)
    if prods is None:
        return None
    meta = _gql_meta(http, log)
    if meta is None:
        return None                   # 숨김을 모르고 쓰면 사이트에 없는 상품이 나간다 — 판을 버린다
    rows: list[dict] = []
    for p in prods:
        m = meta.get(int(p["id"]))
        if m is None or khakis._val(m, "hidden") == "true":
            continue
        try:
            d = cs.to_row(p, slug, SHOP, now)
        except ValueError as e:       # 원화가 아닌 값 — 판을 버린다(to_row 주석)
            log(f"  [thisisneverthat] {e}")
            return None
        if not d["price"]:
            continue                  # 사은품(GWP) 같은 0원 줄
        sizes, sold = khakis._sizes(p)
        if sizes:
            d["options"] = sizes
            d["soldout_options"] = sold
        d["source_url"] = f"{BASE}/products/{p['handle']}"
        d["description_source"] = "thisisneverthat"
        d["platform"] = "shopify-headless"

        extra = []
        for k in ("model", "model2"):
            mdl = khakis._list(khakis._val(m, k))
            if len(mdl) == 2 and re.fullmatch(r"\d{3}(?:\.\d)?", mdl[0]):
                extra.append(f"모델 {mdl[0]}cm · {mdl[1]} 착용")
        if extra:
            d["detail_text"] = "\n".join(x for x in [d["description"], *extra] if x).strip()

        fit = khakis._list(khakis._val(m, "sizeFit"))
        chart = [c for c in fit[1:] if "/" in c]
        if chart:
            t = _size_table(chart, sizes)
            if any(not k.startswith("_") for k in t):
                d["size_table"] = t
            d["spec"]["실측"] = " · ".join(chart)
        # 소재 줄은 「Shell 1 : Cotton 100%」 꼴과 라벨 없는 「Cotton 100%」 · 「Nylon 90%, Spandex 10%」 꼴 둘이다
        mats = [ln for ln in d["description"].splitlines()
                if re.match(r"(?i)(shell|lining|fabric|body|materials?|composition|소재|겉감|안감)\s*\d?\s*:", ln)
                or re.fullmatch(r"(?:[A-Za-z][A-Za-z .-]*\s*\d{1,3}(?:\.\d)?\s*%[\s,/]*)+", ln.strip())]
        if mats:
            d["spec"]["소재"] = " / ".join(mats)
        # 태그에는 직원·지인 할인(EMPLOYEE50 · FRIENDS30) · 판촉 묶음이 섞인다 — 카키스와 같은 거름
        # 여기는 판촉 묶음이 소문자 · 퍼센트로도 온다(「0526end 20%」 · 「2026_SEP_60%」 · 「25SS SEASON OFF 40%」).
        d["category_names"] = [c for c in dict.fromkeys(
            [p.get("product_type") or ""] + [t for t in (p.get("tags") or [])
                                             if not khakis._TAG_NOISE.search(t) and not _PROMO.search(t)]) if c]
        rows.append(d)
    log(f"  [thisisneverthat] products.json {len(prods)}벌 · 사이트에 보이는 상품 {len(rows)}벌")
    return rows
