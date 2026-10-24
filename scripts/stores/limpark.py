"""림팍(limpark) — Cargo 사이트. **아직 파는 상품이 없다** — 가게가 열렸는지만 본다.

2026-09-27 조사: 사이트맵 16쪽은 홈·메뉴·룩북(ss27 · aw26-1 · ss26-1)·info 뿐이다. /shop 은 사진 한 장과
「LAUNCHING SOON」(인스타그램 링크) 글뿐이고, 페이지에 박힌 window.__PRELOADED_STATE__ 에서
site.has_commerce_addon=false · site.shop_id=null · commerce.products={} 다(info · aw26-1 도 같다).
Cargo 상점(Commerce)을 켜지 않았으니 상품 API 도 없다. 룩북 쪽은 사진만 있고 값·사이즈가 없다.

그래서 list_urls 는 가게가 닫힌 동안 빈 목록을 돌려주고, 상점이 켜진 흔적이 보이면 None 을 돌려
「목록 받기 실패」로 멈춘다 — 모르는 틀을 빈 목록으로 읽고 넘어가지 않게, 사람이 해석기를 새로 쓰게 한다.
"""
import json
import re

BASE = "https://limparkofficial.com"
PAGE_DELAY = 3.0


def _state(html_text: str) -> dict:
    m = re.search(r"window\.__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", html_text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def list_urls(http, log=print) -> list[str] | None:
    r = http.get(f"{BASE}/shop")
    if r is None or r.status_code != 200:
        log(f"  [limpark] /shop 받기 실패({getattr(r, 'status_code', '없음')})")
        return None
    st = _state(r.text)
    if not st:
        log("  [limpark] /shop 에 __PRELOADED_STATE__ 가 없다 — 사이트 틀이 바뀌었다")
        return None
    site = st.get("site") or {}
    com = st.get("commerce") or {}
    opened = (site.get("has_commerce_addon") or site.get("shop_id")
              or (com.get("products") or {}) or "<shop-product" in r.text)
    if opened:
        log("  [limpark] Cargo 상점이 켜졌다 — 상품 해석기를 새로 써야 한다(이 판은 상태를 안 바꾼다)")
        return None
    log("  [limpark] 상점 준비 중(LAUNCHING SOON) — 파는 상품 0벌")
    return []


def parse_page(html_text: str, url: str, slug: str, now: str, http=None) -> dict | None:
    return None     # 파는 상품이 없어 불릴 일이 없다 — 상점이 열리면 list_urls 가 먼저 멈춘다
