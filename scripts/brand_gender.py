"""브랜드 성별을 **매장이 스스로 말하는 대로** 다시 센다.

지금 값은 사람이 붙인 것과 무신사 크롤을 섞은 것이라 근거가 남아 있지 않다. 매장 자신이
가진 증거로 다시 재고, 증거가 무엇이었는지 함께 적는다.

## 성별은 브랜드가 아니라 **상품**에 붙는다

브랜드가 유니섹스여도 그 안에 여성 전용 상품이 있다(스커트·원피스). 그래서 두 층으로 둔다:

    브랜드 성별   매장 전체 성향 — 브랜드를 고르고 거를 때 쓴다
    상품 성별     상품마다 따로 — 옷을 고르고 거를 때 쓴다. **브랜드 값이 덮어쓰지 않는다**

상품 성별의 증거는 센 것부터: ① 매장이 그 상품을 넣어 둔 분류(WOMEN/MEN) ② 품목(스커트·
원피스는 여성) ③ 상세글의 모델 정보 ④ 그래도 모르면 브랜드 성별.

여기서는 ①을 매장 단위로 모으고, 매장 상단 메뉴를 함께 받아 두 증거가 맞는지 본다.
"""
from __future__ import annotations
import argparse, collections, csv, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DATA, CRAWL = ROOT / "data", ROOT / "data" / "crawl"
OUT = DATA / "_brand_gender.csv"
HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
       "Accept-Language": "ko,en;q=0.8"}

W = re.compile(r"\b(WOMEN'?S?|WOMAN|LADIES|여성|여자)\b", re.I)
M = re.compile(r"\b(MEN'?S?|MAN)\b", re.I)          # WOMEN 이 MEN 을 머금으니 아래에서 순서로 가른다
MK = re.compile(r"남성|남자")
U = re.compile(r"\b(UNISEX|유니섹스|공용)\b", re.I)


def label(text: str) -> str | None:
    """한 토막의 글이 가리키는 성별. WOMEN 안에 MEN 이 있으니 여성을 먼저 뗀다."""
    if U.search(text):
        return "UNISEX"
    w = bool(W.search(text))
    rest = W.sub(" ", text)
    m = bool(M.search(rest)) or bool(MK.search(rest))
    if w and not m:
        return "WOMENSWEAR"
    if m and not w:
        return "MENSWEAR"
    return None


def menu_of(home: str) -> tuple[str, str, str]:
    """매장 상단 메뉴에서 성별 탭을 찾는다. (판정, 본 메뉴 글, 걸린 링크) — 못 찾으면 ('', '', '')

    개수만 세어 두면 나중에 그 판정을 되짚을 수가 없다. 「WOMENSWEAR 1 · MENSWEAR 1」이
    진짜 성별 칸인지, 룩북 제목에 든 낱말인지 글자만 봐서는 못 가린다. 그래서 걸린 링크의
    글과 주소를 함께 적는다 — /category/men/79/ 처럼 분류번호가 붙어 있으면 훑을 수 있는
    칸이고, /collection/lookbook25fw-mens.html 이면 룩북일 뿐이다(munn).
    """
    try:
        r = requests.get(home, headers=HDR, timeout=25)
        if r.status_code != 200:
            return "", f"HTTP {r.status_code}", ""
    except Exception as e:
        return "", type(e).__name__, ""
    soup = BeautifulSoup(r.text, "lxml")
    seen = collections.Counter()
    hit: dict[str, list[str]] = collections.defaultdict(list)
    for a in soup.find_all("a"):
        t = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
        if not 1 <= len(t) <= 24:
            continue
        g = label(t)
        if not g:
            continue
        seen[g] += 1
        one = f"{t}→{(a.get('href') or '')[:48]}"
        if one not in hit[g]:
            hit[g].append(one)
    if not seen:
        return "", "", ""
    if {"WOMENSWEAR", "MENSWEAR"} <= set(seen):
        v = "BOTH"
    else:
        v = seen.most_common(1)[0][0]
    links = " | ".join(x for g, _ in seen.most_common() for x in hit[g][:2])
    return v, " · ".join(f"{k} {n}" for k, n in seen.most_common()), links


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", default="")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    want = set(args.brands.split()) if args.brands.strip() else None

    # 상품 쪽 증거 — 매장이 그 상품을 넣어 둔 분류
    cat = collections.defaultdict(collections.Counter)
    ours = collections.defaultdict(collections.Counter)
    home = {}
    for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig")):
        b = r["brand_slug"]
        if want and b not in want:
            continue
        ours[b][r.get("gender_target") or ""] += 1
        g = label(r.get("category_path") or "")
        if g:
            cat[b][g] += 1
        if b not in home:
            m = re.match(r"(https?://[^/]+)", r.get("source_url") or "")
            if m:
                home[b] = m.group(1)

    brands = sorted(ours)
    print(f"매장 {len(brands)}곳", flush=True)
    rows = []

    def one(b):
        v, note, links = menu_of(home[b]) if b in home else ("", "주소 없음", "")
        c = cat.get(b, collections.Counter())
        o = ours[b]
        n = sum(c.values())
        by_cat = ""
        if n:
            top, cnt = c.most_common(1)[0]
            by_cat = top if cnt / n >= 0.8 else "MIXED"
        return {"brand_slug": b, "지금 값": o.most_common(1)[0][0] if o else "",
                "지금 값 분포": " · ".join(f"{k} {x}" for k, x in o.most_common()),
                "매장 분류가 말하는 것": by_cat, "분류 근거 수": n,
                "매장 메뉴가 말하는 것": v, "메뉴 근거": note, "메뉴 링크": links,
                "홈": home.get(b, "")}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for r in ex.map(one, brands):
            rows.append(r)
            time.sleep(0.03)

    # --brands 로 몇 곳만 돌렸으면 나머지 줄은 그대로 둔다. 통째로 덮으면 217곳짜리
    # 표가 4줄로 줄어든다 — 한 번 겪었다.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    keep: dict[str, dict] = {}
    if want and OUT.exists():
        with OUT.open(encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                if r["brand_slug"] not in want:
                    keep[r["brand_slug"]] = r
    fields = list(rows[0].keys())
    merged = list(rows) + [{k: r.get(k, "") for k in fields} for r in keep.values()]
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in sorted(merged, key=lambda x: x["brand_slug"]):
            w.writerow(r)

    t = collections.Counter()
    for r in rows:
        cur = r["지금 값"]
        ev = r["매장 분류가 말하는 것"] or r["매장 메뉴가 말하는 것"]
        if not ev:
            t["증거 없음"] += 1
        elif ev == "BOTH" or ev == "MIXED":
            t["매장이 남녀 다 판다"] += 1
        elif ev == cur:
            t["맞음"] += 1
        else:
            t[f"다름 — 지금 {cur} · 매장 {ev}"] += 1
    print("\n== 매장 단위로 견주면")
    for k, v in t.most_common():
        print(f"  {v:4d}  {k}")
    print("\n다른 곳:")
    for r in rows:
        cur, ev = r["지금 값"], (r["매장 분류가 말하는 것"] or r["매장 메뉴가 말하는 것"])
        if ev and ev not in ("BOTH", "MIXED") and ev != cur:
            print(f"  {r['brand_slug']:22s} 지금 {cur:12s} → 매장 {ev:12s} "
                  f"(분류 {r['분류 근거 수']}벌 · 메뉴 {r['메뉴 근거'][:30]})")
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
