"""Kiwi Sizing 에 매장이 직접 넣어 둔 실측표를 받는다 — Shopify 수집기(crawl_shopify)가 부른다.

    GET https://app.kiwisizing.com/api/getSizingChart?shop=<가게>.myshopify.com&product=<상품 번호>
        (&tags=&vendor=&type=&title= — 매장 위젯이 붙여 보내는 것을 같게 붙인다)

고엔제이(goen-j)는 상품 글(body_html)에도 상품 페이지에도 실측이 없다. 매장이 Kiwi Sizing 에 표를
넣어 두었고, 지금 매장 화면에는 위젯이 안 불려 와 안 보인다. 사람이 이 출처를 쓰기로 했다
(2026-09-29 — 「매장이 스스로 적은 표」라서). 519벌 전수 조사에서 실측표가 있는 것은 182벌이었다.

응답은 sizings[] 하나하나에 표(tables{id: {data: [[칸]]}})와 배치(layout)가 있다. 배치가 탭 이름
(「Body Measurements」·「Size Conversion」)과 모델 안내 글을 준다. 여기서 하는 일:
- 옷 실측표만 받는다. 「Size Conversion」(UK/US/IT 줄)과 몸 치수표(키·몸무게)는 버린다.
- 0번 줄은 사이즈 이름, 그 아래 줄은 라벨 + 값. 라벨은 **매장이 쓴 글자 그대로** 둔다 —
  정식 라벨로 바꾸고 「둘레」 값을 반으로 나누는 것은 뒤 단계(size_from_ocr.normalize_html)가 한다.
  여기서 나누면 두 번 나뉜다.
- 인치/센티는 표마다 값으로 확인한다(decide_unit 주석). Kiwi 의 unitType 은 대개 맞지만 틀린 표가 있다
  (트렌치 두 벌이 센티 값에 'in'), 비어 있는 표도 있다(세트의 베스트 탭). 인치는 cm 로 바꿔 0.5 단위로 둔다.
- 모델 안내(「모델은 177cm이고 M 사이즈를 착용」)는 detail_text 로 보낸다. 표에는 절대 안 넣는다.

다른 Kiwi 매장을 붙이려면 KIWI_SHOPS 에 slug → myshopify 주소 한 줄이면 된다. 여기에 없는
매장에서는 client() 가 None 을 돌려주고 수집기는 아무 것도 안 부른다.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from urllib.parse import urlencode

import crawl_cafe24 as cc

# slug → Kiwi 가 아는 가게 이름(myshopify 주소). 매장 앞문 주소(kr.goenj.com)가 아니다 —
# 위젯이 보내는 window.KiwiSizing.data.shop 값이다(상품 페이지 소스, 2026-09-29).
KIWI_SHOPS: dict[str, str] = {
    "goen-j": "goenj-kr.myshopify.com",
}

API = "https://app.kiwisizing.com/api/getSizingChart"
# app.kiwisizing.com 에 보내는 간격(초). 남의 앱 서버라 매장보다 느긋하게 — 519벌 한 판이 13분쯤.
DELAY = 1.5
# 상품이 안 바뀌어도 이만큼 지나면 다시 받는다. Kiwi 에서 표만 고치면 Shopify 의 updated_at 이
# 안 바뀌어서, 이게 없으면 고친 표를 영영 못 받는다.
MAX_AGE_DAYS = 35
# 연달아 이만큼 못 받으면 이 판에서는 그만 부른다(지난 판 것은 그대로 쓴다) — 막힌 서버를 두드리지 않는다.
MAX_FAILS = 5

# 사이즈 변환표의 줄 이름 — 이 가운데 하나라도 있으면 옷 치수표가 아니다.
_CONV_ROW = re.compile(r"(?i)^(?:uk|us|usa|it|ita|fr|eu|eur|kr|korea|jp|japan|cn|china.*|denim|au|intl?|"
                       r"international|mexico|brazil|국내|한국)\b")
_CONV_TITLE = re.compile(r"(?i)conversion|변환|환산|international")
# 몸 치수표 — 「이 사이즈는 키 165~170 · 가슴둘레 82~86 인 사람에게」. 옷 치수가 아니다.
_BODY_ROW = re.compile(r"(?i)^(?:height|weight|키|신장|몸무게|체중)")
_BODY_TITLE = re.compile(r"(?i)body\s*size|신체|권장|recommend|fit\s*guide")
_NUM = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(?:cm|in|inch|\"|″)?\s*$", re.I)

_ranges: dict | None = None


def _canon(label: str) -> str | None:
    # 뒤 단계와 같은 사전으로 읽는다 — 여기서 옷 라벨로 본 것을 뒤에서 버리거나 그 반대가 되지 않게.
    import size_from_ocr
    return size_from_ocr.canon_label(label)


def _range(c: str):
    global _ranges
    if _ranges is None:
        _ranges = json.loads((cc.DATA / "size_labels.json").read_text(encoding="utf-8"))["_ranges_cm"]
    return _ranges.get(c)


def _num(s: str) -> float | None:
    m = _NUM.match(s or "")
    return float(m.group(1).replace(",", ".")) if m else None


def _note_text(raw: str) -> str:
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", raw or "")
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return "\n".join(ln for ln in (re.sub(r"[ \t\xa0]+", " ", x).strip() for x in t.splitlines()) if ln)


def tables_in(resp: dict) -> list[dict]:
    """응답의 표를 **화면에 보이는 차례대로** 편다 — 탭이 「(Blazer)」·「(Vest)」로 갈린 세트는 앞 탭이
    본 옷이다. 표 사전(tables{})의 차례는 화면과 다르다(베스트가 앞에 온 상품이 있었다)."""
    out: list[dict] = []
    for s in (resp or {}).get("sizings") or []:
        tables = s.get("tables") or {}
        placed: list[tuple[str, str | None]] = []
        notes: list[str] = []

        def walk(node, title=None):
            for it in (node or {}).get("data") or []:
                ty = it.get("type")
                if ty == 1:
                    placed.append((it.get("value"), title))
                elif ty == 0:
                    n = _note_text(it.get("value") or "")
                    if n and n not in notes:
                        notes.append(n)
                elif isinstance(it.get("data"), list):          # 탭 묶음(type 3)
                    for tab in it["data"]:
                        walk(tab.get("layout"), tab.get("title") or title)

        walk(s.get("layout"))
        if not placed:          # 배치가 비었으면 표 사전 차례로라도 본다
            placed = [(tid, None) for tid in tables]
        for tid, title in placed:
            tb = tables.get(tid)
            if not tb:
                continue
            out.append({"id": tid, "sizing": s.get("name"), "title": title, "hide": bool(tb.get("hide")),
                        "direction": tb.get("direction") or "col", "data": tb.get("data") or [],
                        "note": "\n".join(notes)})
    return out


def _grid(t: dict) -> tuple[list[str], list[tuple[str, list[str]]], str | None]:
    data = t["data"]
    cell = lambda c: str((c or {}).get("value") or "").strip()
    names = [cell(c) for c in data[0][1:]] if data else []
    rows = [(cell(r[0]), [cell(c) for c in r[1:]]) for r in data[1:] if r]
    units = [c.get("unitType") for r in data[1:] for c in r[1:] if c.get("unitType") in ("cm", "in")]
    declared = max(set(units), key=units.count) if units else None
    return names, rows, declared


def classify(t: dict) -> str:
    """받을 표면 ""를, 아니면 버리는 까닭을 돌려준다."""
    if t["hide"]:
        return "hidden"
    if t["direction"] != "col":
        # 0번 줄이 사이즈 이름인 꼴(col)만 봤다. 뒤집힌 꼴을 추측으로 읽느니 비워 둔다.
        return f"direction={t['direction']}"
    if len(t["data"]) < 2:
        return "empty"
    names, rows, _ = _grid(t)
    title = t["title"] or ""
    labels = [k for k, _ in rows]
    if _CONV_TITLE.search(title) or any(_CONV_ROW.match(k) for k in labels):
        return "size conversion"
    if _BODY_TITLE.search(title) or any(_BODY_ROW.search(k) for k in labels):
        return "body size"
    garment = [k for k in labels if _canon(k) and _range(_canon(k))]
    if not (re.search(r"(?i)measurement|실측", title) or garment):
        return "not a measurement table"
    if not garment:
        return "no garment rows"
    return ""


def _fits(k: str, vs: list[float], f: float) -> bool | None:
    """라벨 k 의 값이 모두 범위에 드는가(f 를 곱해 읽을 때). 범위가 없는 라벨이면 None.
    「둘레」 라벨은 둘레라 범위의 두 배와 댄다 — 뒤 단계가 반으로 나눌 값이다."""
    c = _canon(k)
    rg = _range(c) if c else None
    if not rg:
        return None
    g = 2.0 if ("둘레" in k or "circum" in k.lower()) else 1.0
    return all(rg[0] * g <= v * f <= rg[1] * g for v in vs)


def decide_unit(rows: list[tuple[str, list[float]]], declared: str | None) -> tuple[str | None, str]:
    """인치인가 센티인가 — 줄마다 ×1 과 ×2.54 로 읽어 size_labels.json 범위에 드는지로 가린다.

    Kiwi 의 unitType 은 매장이 칸마다 고른 값이라 틀릴 때가 있다: 트렌치 「LENGTH 114 · BUST 59 ·
    SLEEVE 84.5」가 'in' 으로 적혀 있다(인치면 총장 290cm). 베스트 표는 unitType 이 비었는데
    「어깨 18.13」은 인치다(센티면 어깨 18cm). 그래서 값으로 본다:
    - 범위가 있는 줄(정식 라벨이 되는 줄)마다 값이 **전부** 범위에 드는지 본다. 「둘레」 라벨은 둘레라
      범위의 두 배와 댄다(뒤 단계가 반으로 나눌 값이다).
    - unitType 이 있으면 그 읽기가 줄의 과반에서 맞는 한 그것을 믿는다. 과반이 안 맞고 다른 읽기가
      과반에서 맞으면 그쪽으로 바꾼다(트렌치는 인치로 읽으면 한 줄도 안 맞는다). 둘 다 안 되면 버린다.
    - unitType 이 없으면 한쪽만 과반이 맞을 때 그쪽. 둘 다 맞거나 둘 다 안 맞으면 버린다 —
      틀린 치수보다 빈칸이 낫다.
      과반으로 둔 것은 후드 울 코트 「소매 20.25 · 50.5 · 50.75」(오타) · 「밑단둘레 48~52인치」(122cm)처럼
      오버핏 코트가 다섯 줄 중 두 줄을 범위 밖에 두기 때문이다(3/5 — 2/3 로는 떨어졌다).

    처음에는 너비 줄(어깨·가슴·밑단…)만으로 「전부 드는 쪽」을 골랐다. 그랬더니 인치 표 둘이 센티로
    읽혔다 — 퀼팅 후드 재킷 「총장 26 · 밑단둘레 44~48」(인치로 읽으면 밑단 둘레 122cm 가 범위 120 을
    살짝 넘는다)과 개더 블라우스 「총장 26.15 · 밑단둘레 52.5~56.5」. 둘 다 센티로 두면 총장 26cm 다.
    너비 한 줄이 범위 끝에 걸렸다고 표가 적은 단위를 뒤집으면 안 된다(2026-09-29 goen-j 전수).
    고엔제이는 윗옷 「HEM 67·69·71」을 「둘레」 없이 둘레로 적고 「SHOULDER 39.5 · 10.5 · 41.5」 같은
    오타 줄도 있어서, 「전부 맞아야」로 두면 멀쩡한 표 마흔 개가 떨어진다 — 그런 줄은 to_size_table 이 뺀다.
    """
    probe = [(k, vs) for k, vs in rows if _fits(k, vs, 1.0) is not None]
    if not probe:
        return None, "no ranged rows"
    n = len(probe)
    fit = {u: sum(bool(_fits(k, vs, f)) for k, vs in probe) / n for u, f in (("cm", 1.0), ("in", 2.54))}
    ok = {u: fit[u] > 0.5 for u in fit}
    if declared in fit:
        other = "in" if declared == "cm" else "cm"
        if ok[declared]:
            return declared, ""
        if ok[other]:
            return other, ""
        return None, "no unit fits ranges"
    if ok["cm"] and ok["in"]:
        return None, "unit ambiguous"
    if ok["cm"] or ok["in"]:
        return ("cm" if ok["cm"] else "in"), ""
    return None, "no unit fits ranges"


def to_size_table(t: dict) -> tuple[dict | None, str, str, list[str]]:
    """(size_table, 단위, 버린 까닭, 뺀 줄). size_table 은 {라벨: [cm 값], "_names": [...]}."""
    names, grid, declared = _grid(t)
    rows: list[tuple[str, list[float]]] = []
    for k, cells in grid:
        vs = [_num(x) for x in cells[:len(names)]]
        # 칸이 비거나 글인 줄은 통째로 뺀다 — 값만 추려 두면 사이즈 이름과 차례가 어긋난다.
        if k and len(vs) == len(names) and vs and all(v is not None for v in vs):
            rows.append((k, vs))
    if not names or not rows:
        return None, "", "no numeric rows", []
    unit, why = decide_unit(rows, declared)
    if not unit:
        return None, "", why, []
    if unit == "in":
        rows = [(k, [round(v * 2.54 * 2) / 2 for v in vs]) for k, vs in rows]
    # 어깨 줄이 없는 표의 70cm 넘는 「SLEEVE LENGTH」는 뒷목 중심에서 잰 화장이다 — 어깨선에서 잰
    # 소매는 사람 팔 길이를 못 넘는다(size_from_ocr.sleeve_to_hwajang 주석). 트렌치 84.5 · 85 · 85.5.
    has_shoulder = any(_canon(k) == "어깨" for k, _ in rows)
    if not has_shoulder and not any(_canon(k) == "화장" for k, _ in rows):
        rows = [("화장" if _canon(k) == "소매길이" and min(vs) >= 70 else k, vs) for k, vs in rows]
    # 단위를 정한 뒤에도 범위를 벗어나는 줄은 뺀다 — 줄 안의 몇 칸만 버리면(뒤 단계 fix_value 가 그렇게 한다)
    # 남은 값이 사이즈 이름과 어긋나고, 「밑단 55.1 · 60.1 · 65.1」(둘레)의 앞 두 칸이 단면으로 남는다.
    # 둘레 값이 단면 자리에 남으면 브랜드 둘레 판정(brand_girth)이 흔들려 바지 밑단까지 반으로 나뉜다.
    dropped = [k for k, vs in rows if _fits(k, vs, 1.0) is False]
    rows = [(k, vs) for k, vs in rows if _fits(k, vs, 1.0) is not False]
    if not any(_fits(k, vs, 1.0) for k, vs in rows):
        return None, "", "no rows in range", dropped
    st: dict = {}
    for k, vs in rows:
        st.setdefault(k, [float(v) for v in vs])
    st["_names"] = names
    return st, unit, "", dropped


def pick(resp: dict) -> dict:
    """응답 하나 → 받을 표 하나와 그 까닭들. 세트(블레이저+베스트)는 앞 탭 한 벌만 받고 개수를 적는다."""
    out = {"table": None, "unit": "", "title": "", "note": "", "n_tables": 0, "dropped_rows": [], "rejected": []}
    seen: set[str] = set()
    for t in tables_in(resp):
        why = classify(t)
        st, unit, dropped = None, "", []
        if not why:
            st, unit, why, dropped = to_size_table(t)
        if why:
            out["rejected"].append([t["title"] or "", why])
            continue
        key = json.dumps(st, sort_keys=True, ensure_ascii=False)
        if key in seen:          # 같은 표를 두 sizing 이 똑같이 들고 오는 상품이 있다(바지 두 벌)
            continue
        seen.add(key)
        out["n_tables"] += 1
        if out["table"] is None:
            out.update(table=st, unit=unit, title=t["title"] or "", note=t["note"], dropped_rows=dropped)
    return out


class Client:
    def __init__(self, slug: str, shop: str, http: cc.PoliteSession | None = None, log=print):
        self.slug, self.shop, self.log = slug, shop, log
        self.http = http or cc.PoliteSession(delay=DELAY)
        self.fails = 0
        self.calls = 0

    def fetch(self, p: dict) -> dict | None:
        if self.fails >= MAX_FAILS:
            return None
        q = {"shop": self.shop, "product": str(p["id"]), "tags": ",".join(p.get("tags") or []),
             "vendor": p.get("vendor") or "", "type": p.get("product_type") or "", "title": p.get("title") or ""}
        self.calls += 1
        r = self.http.get(f"{API}?{urlencode(q)}", retries=1)
        try:
            j = r.json() if r is not None and r.status_code == 200 else None
        except ValueError:
            j = None
        if not isinstance(j, dict):
            self.fails += 1
            if self.fails == MAX_FAILS:
                self.log(f"  [{self.slug}] Kiwi Sizing 을 연달아 {MAX_FAILS}번 못 받았다 — 이 판은 지난 판 표를 쓴다")
            return None
        self.fails = 0
        return j

    def enrich(self, d: dict, old: dict | None, p: dict) -> None:
        """상품 한 벌에 Kiwi 표를 붙인다. 상품이 안 바뀌었고 받은 지 오래지 않으면 지난 판 것을 다시 쓴다
        (crawl_shopify.enrich 의 page_updated_at 과 같은 꼴)."""
        updated_at = p.get("updated_at") or ""
        res = None
        if old and old.get("kiwi_updated_at") == updated_at and isinstance(old.get("kiwi"), dict):
            try:
                age = (datetime.now(cc.KST) - datetime.fromisoformat(old["kiwi"]["fetched_at"])).days
            except (KeyError, TypeError, ValueError):
                age = MAX_AGE_DAYS
            if age < MAX_AGE_DAYS:
                res = old["kiwi"]
                d["kiwi_updated_at"] = updated_at
        if res is None:
            j = self.fetch(p)
            if j is not None:
                res = {**pick(j), "fetched_at": datetime.now(cc.KST).isoformat(timespec="seconds")}
                d["kiwi_updated_at"] = updated_at
            elif old and isinstance(old.get("kiwi"), dict):
                # 못 받았으면 지난 판 표를 그대로 쓴다. kiwi_updated_at 은 안 바꾸니 다음 판에 다시 받는다.
                res = old["kiwi"]
                d["kiwi_updated_at"] = old.get("kiwi_updated_at")
        if res is None:
            return
        d["kiwi"] = res
        apply(d, res)


def apply(d: dict, res: dict) -> None:
    st = res.get("table")
    # 매장 글(body_html)에서 이미 더 나은 표를 읽었으면 덮지 않는다 — 수집기들이 쓰는 같은 잣대.
    if st and cc.table_is_better(st, d.get("size_table") or {}):
        d["size_table"] = dict(st)
    note = (res.get("note") or "").strip()
    if note and note not in (d.get("detail_text") or ""):
        # 모델 키·착용 사이즈는 설명 쪽 정보다(detail_from_ocr·product_desc 가 읽는다). 표에 넣으면
        # 「모델 177」이 치수로 읽힌다.
        d["detail_text"] = "\n".join(x for x in ((d.get("detail_text") or "").strip(), note) if x)


def client(slug: str, log=print) -> Client | None:
    """이 매장이 Kiwi Sizing 에 표를 두면 Client, 아니면 None — 수집기는 None 이면 아무 것도 안 부른다."""
    shop = KIWI_SHOPS.get(slug)
    return Client(slug, shop, log=log) if shop else None
