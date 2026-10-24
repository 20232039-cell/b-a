"""카키스(khakis) — khakis2020.com 은 React SPA 인데 뒤는 헤드리스 Shopify(khakis2020.myshopify.com)다.

JS 묶음(/assets/index-*.js)에 Storefront API 주소와 공개 토큰이 박혀 있다(브라우저가 매 방문 쓰는 것):
    https://khakis2020.myshopify.com/api/2025-01/graphql.json  X-Shopify-Storefront-Access-Token
그리고 myshopify 쪽 `products.json` 도 열려 있다(robots 도 막지 않는다). 그래서 목록 API 로 줄을 통째로 받는다:

- products.json — 값·정가·사이즈별 재고(variants[].available)·사진·설명. crawl_shopify 의 fetch_all·to_row 를
  그대로 쓴다(원화 못 박기·소수점 값 거르기가 한 곳에 있게).
- Storefront GraphQL — products.json 에 안 오는 메타필드: 실측표(size.chart) · 모델 착용(model.size) ·
  고시 정보(info.information) · 숨김(operation.hidden). 사이트가 보여 주는 상품 목록도 이쪽이 기준이다.

카키스는 편집숍이다 — 1,570벌 중 자체 상품(vendor「Khakis」)은 432벌, 나머지는 아크테릭스·파타고니아 등.
**자체 상품만 걷는다.** 앱은 브랜드마다 그 브랜드 옷을 보여 주는데, 남의 브랜드 옷이 「카키스」 이름으로
들어가면 틀린 값이다(2026-09-27 — 해석기 시험에서 편집숍임을 알고 거른다). 브랜드는 spec["브랜드"] 에도 둔다.
"""
import html as _html
import json
import re
import time

import crawl_cafe24 as cc
import crawl_shopify as cs

BASE = "https://khakis2020.com"
SHOP = "https://khakis2020.myshopify.com"
GQL = f"{SHOP}/api/2025-01/graphql.json"
# 사이트 JS 묶음에 든 공개 Storefront 토큰(비밀이 아니다 — 모든 방문자 브라우저가 쓴다). 사이트가 토큰을
# 바꾸면 401 이 오고 fetch_rows 가 None 을 돌려 판을 버린다 — 그때 index-*.js 에서 새 값을 찾는다.
TOKEN = "dd765a3e56f230626aad9ae872a38ae1"
PAGE_DELAY = 3.0

_Q = """query($after: String) { products(first: 250, after: $after) {
  pageInfo { hasNextPage endCursor }
  edges { node { id
    sizeChart: metafield(namespace: "size", key: "chart") { value }
    model: metafield(namespace: "model", key: "size") { value }
    information: metafield(namespace: "info", key: "information") { value }
    warning: metafield(namespace: "warning", key: "content") { value }
    notice: metafield(namespace: "notice", key: "content") { value }
    hidden: metafield(namespace: "operation", key: "hidden") { value }
  } } } }"""

# 태그 가운데 분류가 아닌 것 — 직원·지인 할인 코드(EMPLOYEE30 · FRIENDS20), 창고 표시(SEONGSU_INVENTORY),
# 판촉 묶음. 대문자·숫자·밑줄뿐인 태그는 다 운영용이다.
# (re.I 를 전체에 걸면 「Sneakers」 같은 한 단어 태그까지 대문자 규칙에 걸려 사라진다 — 대소문자는 뒤 칸에만)
_TAG_NOISE = re.compile(r"^[A-Z0-9_]+$|(?i:^(sale|new arrivals|store exclusive|gift curation|collaboration)$|drop \d)")


def _gql_meta(http, log) -> dict[int, dict] | None:
    """Storefront API 로 메타필드를 250벌씩 — requests 세션을 쓰되 호스트 간격은 PoliteSession 과 같게 지킨다."""
    out: dict[int, dict] = {}
    after = None
    for _ in range(40):
        # PoliteSession 에는 POST 가 없다 — 그 세션(머리말·UA)을 빌려 쓰고 간격은 직접 쉰다
        time.sleep(http.delay)
        try:
            r = http.s.post(GQL, json={"query": _Q, "variables": {"after": after}}, timeout=60,
                            headers={"X-Shopify-Storefront-Access-Token": TOKEN, "Content-Type": "application/json"})
            http.requests_made += 1
            j = r.json()
        except Exception as e:           # 네트워크·JSON 오류 — 판을 버린다
            log(f"  [khakis] Storefront API 실패: {type(e).__name__}: {e}")
            return None
        if r.status_code != 200 or j.get("errors") or not j.get("data"):
            log(f"  [khakis] Storefront API {r.status_code}: {str(j.get('errors'))[:200]}")
            return None
        p = j["data"]["products"]
        for e in p["edges"]:
            n = e["node"]
            out[int(n["id"].rsplit("/", 1)[-1])] = n
        if not p["pageInfo"]["hasNextPage"]:
            return out
        after = p["pageInfo"]["endCursor"]
    log("  [khakis] Storefront API 쪽수가 너무 많다 — 판을 버린다")
    return None


def _val(n: dict, k: str) -> str:
    return ((n.get(k) or {}).get("value") or "").strip()


def _list(v: str) -> list[str]:
    try:
        x = json.loads(v)
    except ValueError:
        return [v] if v else []
    return [str(i).strip() for i in x if str(i).strip()] if isinstance(x, list) else [str(x)]


def _sizes(p: dict) -> tuple[list[str], list[str]]:
    """사이즈 축만 — 옵션은 Size · Color · Style Code 꼴이다. 색과 품번 축은 뺀다.
    사이즈 하나가 품절이려면 그 사이즈의 변형이 **다** 안 팔려야 한다."""
    names = [o.get("name") or "" for o in p.get("options") or []]
    idx = next((i for i, nm in enumerate(names) if re.search(r"(?i)size|사이즈", nm)), None)
    if idx is None:
        return [], []
    key = f"option{idx + 1}"
    order: list[str] = []
    avail: dict[str, bool] = {}
    for v in p.get("variants") or []:
        s = (v.get(key) or "").strip()
        if not s:
            continue
        if s not in avail:
            order.append(s)
            avail[s] = False
        avail[s] = avail[s] or bool(v.get("available"))
    return order, [s for s in order if not avail[s]]


def _size_table(chart: list[str], sizes: list[str]) -> dict:
    """「Length/71/73/76/79」 한 줄이 부위 하나다(값은 사이즈 차례). 가로 표로 짜서 cc.extract_size_any 에 태운다 —
    부위 이름을 다른 Shopify 매장 줄과 같게 읽으려고."""
    rows = [c.split("/") for c in chart if "/" in c]
    rows = [(r[0].strip(), [x.strip() for x in r[1:]]) for r in rows if r[0].strip()]
    if not rows:
        return {}
    n = max(len(v) for _, v in rows)
    # 값 수가 사이즈 수와 다르면(Trail Shorts Gobi: 값 넷, 파는 사이즈 둘) 이름을 붙이지 않는다 — 어긋나게 붙이느니
    names = sizes if len(sizes) == n else [str(i + 1) for i in range(n)]
    esc = _html.escape
    head = "<tr><th>Size</th>" + "".join(f"<th>{esc(k)}</th>" for k, _ in rows) + "</tr>"
    body = "".join("<tr><td>" + esc(names[i]) + "</td>"
                   + "".join(f"<td>{esc(v[i]) if i < len(v) else ''}</td>" for _, v in rows) + "</tr>"
                   for i in range(n))
    t = cc.extract_size_any(f"<table>{head}{body}</table>")
    if len(sizes) != n:
        t.pop("_names", None)
    return t


def fetch_rows(http, slug, now, log) -> list[dict] | None:
    prods = cs.fetch_all(http, SHOP, log)
    if prods is None:
        return None
    meta = _gql_meta(http, log)
    if meta is None:
        return None                   # 숨김·실측을 모르고 쓰면 시험 상품이 나간다 — 판을 버린다
    rows: list[dict] = []
    for p in prods:
        pid = int(p["id"])
        m = meta.get(pid)
        # Storefront(사이트)에 안 오는 상품은 사이트에서 못 산다 — products.json 에만 있는 빈 「Khakis」 0원 상품 따위.
        # operation.hidden 은 시험 상품([TEST] Product · GIFTING TEST Tee)에 켜져 있다.
        if m is None or _val(m, "hidden") == "true":
            continue
        if (p.get("vendor") or "").strip().casefold() != "khakis":
            continue                  # 편집숍의 남의 브랜드 옷(아크테릭스·On·파타고니아 …) — 머리말
        try:
            d = cs.to_row(p, slug, SHOP, now)
        except ValueError as e:       # 원화가 아닌 값 — 판을 버린다(to_row 주석)
            log(f"  [khakis] {e}")
            return None
        if not d["price"]:
            continue                  # 0원 줄은 쓰지 않는다
        sizes, sold = _sizes(p)
        d["options"] = sizes
        d["soldout_options"] = sold
        d["source_url"] = f"{BASE}/products/{p['handle']}"
        d["description_source"] = "khakis"
        d["platform"] = "shopify-headless"

        extra: list[str] = []
        mdl = _list(_val(m, "model"))
        # 모델 정보는 「["180","L"]」(키 · 입은 사이즈)로 온다
        if len(mdl) == 2 and re.fullmatch(r"\d{3}(?:\.\d)?", mdl[0]):
            extra.append(f"모델 {mdl[0]}cm · {mdl[1]} 착용")
        elif mdl:
            extra.append("모델 " + " / ".join(mdl))
        info = _val(m, "information")
        if info:
            extra.append(info)
        if _val(m, "warning"):
            extra.append(_val(m, "warning"))      # 세탁·착용 주의(상품마다 다르다)
        extra += _list(_val(m, "notice"))         # 「향료로 인해 내용물 색상이…」 같은 상품 안내
        if extra:
            d["detail_text"] = "\n".join(x for x in [d["description"], *extra] if x).strip()

        chart = _list(_val(m, "sizeChart"))
        if chart:
            t = _size_table(chart, sizes)
            if any(not k.startswith("_") for k in t):
                d["size_table"] = t
            d["spec"]["실측"] = " · ".join(chart)

        spec = d["spec"]
        if p.get("vendor"):
            spec["브랜드"] = p["vendor"]
        # 설명 끝에 「Body: 100% Recycled Nylon」「Materials: Polyester」「61% Cotton, 29% Nylon」처럼 소재 줄이 온다
        mats = [ln for ln in d["description"].splitlines()
                if re.match(r"(?i)(materials?|body|shell|fabric|upper|lining|composition|소재)\s*:|\d{1,3}\s*%\s*[A-Za-z가-힣]", ln)]
        if mats:
            spec["소재"] = " / ".join(mats)
        d["category_names"] = list(dict.fromkeys(
            [p.get("product_type") or ""] + [t for t in (p.get("tags") or []) if not _TAG_NOISE.search(t)]))
        d["category_names"] = [c for c in d["category_names"] if c]
        rows.append(d)
    log(f"  [khakis] products.json {len(prods)}벌 · 사이트에 보이는 자체 상품 {len(rows)}벌")
    return rows
