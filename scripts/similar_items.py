"""상품마다 비슷한 옷 40벌 — v3 (2026-10-05 확정 설정).

왜: 앱 상세의 「비슷한 옷」 레일. 지금 앱은 「같은 품목 · 가격 가까운 순」인데 사람 평가(4차, 처음 보는 기준 옷 20개 · 339쌍)에서
그 방식은 57%, 이 설정은 63%였다(둘 중 하나 고르기에서 사람과 같은 쪽을 고르는 비율). 같은 옷의 다른 색이 열 벌 안에 남는 비율 92%.
설계와 평가 기록은 노션 「유사도·추천 설계」 2절 · 6절. 실험 스크립트(scratchpad similar_v2.py)를 CI 용으로 옮긴 것이다.

어떻게:
  후보 풀  = 큰 품목(재킷류 · 코트류 · 티셔츠류 … build_vocab._HIER) 안. 같은 브랜드 · 같은 옷 다른 색 · 성별 안 맞는 것은 뺀다.
  닮음     = (1−PHOTO_W)·T + PHOTO_W·V + FINE_BONUS·[세부 품목 같음] − PEN_W·Σ[기장 · 넥라인 · 소매 태그가 둘 다 있는데 묶음이 안 겹침]
             − LOOK_PEN_W·[기준 옷의 겉보기 소재(가죽 · 스웨이드 · 퍼 · 데님 …)가 후보 소재와 안 겹침]
      T    = 축별 코사인의 가중 평균(양쪽 다 관측된 축만, 묶음 안 IDF) + 관측이 적으면 묶음 평균 μ 로 수축  T=(Σw·sim+.2μ)/(Σw+.2)
             색은 같으면 1 · 같은 계열 .6 · 다르면 0. 소재는 혼용률 비율 벡터(없으면 소재 태그).
      V    = 사진 임베딩(data/emb/siglip_crop 이 90% 이상 덮으면 그것, 아니면 data/emb/siglip · Marqo-FashionSigLIP) 코사인을 묶음 안 무작위 쌍 분포의 백분위로 바꾼 값. 사진 없는 후보는 T 그대로.
  점수 S   = (.95−BRAND_W)·닮음 + BRAND_W·B + .05·P   (B: 브랜드끼리 비슷함 백분위, P=exp(−|log 가격비|))
  후보     = 묶음 안 닮음 상위 150 ∪ 브랜드 이웃 상위 20곳 × 각 상위 5 → S 상위 40 저장(브랜드당 3 · 같은 옷 다른 색 1)
  브랜드 B = data/mood/brand_graph.json 의 장르 비율(사람 칩 · 없으면 예측) 코사인 0.5 + 브랜드 태그 벡터(IDF, 부자재 · 기능 · 넥라인 뺌) 코사인 0.5,
             둘 다 표준화(z) 뒤 더해 전체 쌍 백분위로. 지도에 없는 브랜드끼리는 0.4.
  축 비중  = 3차 정답 988개로 배운 값. 992쌍(3차+4차)으로 다시 배우면 교차검증이 더 나빠서(59% < 70%) 그대로 둔다.

결과: data/similar_items.json — {"version", "config", "index": [상품 id…], "items": {상품 id: [index 번호, 점수×1000, index 번호, 점수×1000, …]}}
      (한 줄에 이웃 ≤40 을 번호 · 점수 번갈아 평평하게 — 겹괄호와 소수점을 빼 70MB → 35MB 쯤)
      상품 id 는 앱 · 창고가 쓰는 "<brand_slug>-<product_no>" (앱 세션 2026-10-05: 주소보다 안정적이고 앱이 이미 이 id 로 움직인다).
      행 번호 대신 index 를 두는 까닭: 카탈로그가 바뀌면 행 번호가 어긋난다(2026-10-05 한 번 당함).
      84,141벌 기준 10분 · 3GB (4코어). 앱용 조각(브랜드별 similar/<slug>.json)은 export_app_data.py 가 이 파일에서 만든다.

  python scripts/similar_items.py                 # 전부
  python scripts/similar_items.py --limit 3       # 큰 묶음 3개만(연기 시험)
  python scripts/similar_items.py --pool urls.txt # 기준 · 후보를 이 주소들로만(앱 상품 집합 시험용)
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import re
import time
from pathlib import Path

import numpy as np
from scipy import sparse

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
OUT = DATA / "similar_items.json"

# ── 설정값(바꾸면 version 도 바뀐다) ──────────────────────────────────────────
PHOTO_W = 0.5          # 사진 반 · 태그 반 — 0.3 · 0.7 · 0.9 보다 4차에서 나았다
FINE_BONUS = 0.10      # 세부 품목 같음
PEN_W = 0.10           # 기장 · 넥라인 · 소매 묶음 어긋남(축마다)
PEN_AXES = ("length", "neckline", "sleeve_length")
# 겉보기 소재 어긋남 — 「모양이 품목, 소재는 태그」로 레더 자켓 품목을 없앤 뒤(2026-10-05), 가죽 겉옷의 이웃 상위 5 중 가죽 · 스웨이드는
# 65%였다(겉옷 전체 19%). 소재 축 비중(.06)이 가볍기 때문이다. 눈으로 보이는 소재(가죽 · 스웨이드 · 퍼 · 데님 · 트위드 · 코듀로이 · 벨벳 · 니트)가
# 기준 옷에 있는데 후보의 소재 태그가 그 묶음과 하나도 안 겹치면 감점한다(후보 소재가 비어 있으면 모름이라 안 뺀다).
# 3 · 4 · 5차 사람 답(기준 옷 96벌): 전체 68.7 → 69.4%(+0.7%p, 기준 옷 단위 95% 구간 +0.1~+1.3), 그런 소재가 있는 기준 옷 21벌은
# 65.1 → 68.2%(+3.1%p, +0.7~+5.9). 0.03 · 0.05 · 0.10 은 같았고 0.15 부터 줄었다(2026-10-06).
LOOK_PEN_W = 0.05
LOOK_MAT = {"가죽": ("가죽", "양가죽", "소가죽", "인조가죽", "에코레더"), "스웨이드": ("스웨이드",), "퍼": ("퍼", "시어링", "무스탕"),
            "데님": ("데님",), "트위드": ("트위드",), "코듀로이": ("코듀로이",), "벨벳": ("벨벳",), "니트": ("니트",)}
BRAND_W = 0.20         # 2차에서 .35 는 −18%
PRICE_W = 0.05
SHRINK = 0.2
AXW = {"silhouette": .15, "material": .06, "design_element": .10, "construction": .08, "length": .12, "pattern": .10,
       "finish_wash": .02, "color": .15, "neckline": .10, "sleeve_length": .06, "pants_type": .04, "hardware": .015, "function": .005}
AXES = [a for a in AXW if a != "color"]
KEEP, PER_BRAND, TOP_T, TOP_BRANDS, PER_NEIGHBOR = 40, 3, 150, 20, 5
RERANK_N = 12          # 앞 12벌은 사진 닮음만으로 다시 줄 세운다(대표님 8차 57 → 73%, 2026-10-07)
# 기준별 목록(앱 「비슷한 옷」 글자 줄 — 색·무늬 · 모양 · 소재, 대표님 결정 2026-10-06). 같은 큰 품목 안에서 그 기준 위주로 다시 찾는다.
# 「전체 40벌 안 재정렬」로는 그 기준으로만 아주 비슷한 옷이 이미 빠져 있을 수 있어서 따로 뽑는다(코덱스 검토).
#   cp 색·무늬 = .5·[무지끼리 · 또는 무늬(패턴 · 프린트)가 하나라도 같음] + .3·색(같음 1 · 계열 .6) + .1·워싱 · 가공 + .1·닮음
#   s  모양   = .5·사진 + .5·태그(실루엣 · 기장 · 소매 · 넥라인 · 디자인 요소 · 봉제 · 바지 종류) + 세부 품목 가산 − 기장 · 넥라인 · 소매 어긋남
#   m  소재   = .6·소재(혼용률 · 소재 태그) + .2·[겉보기 소재 같음] + .2·닮음
# 무지 = 무늬 태그가 단색 · 멜란지뿐이고 그래픽(프린트)이 없음. 작은 로고 · 자수는 무지로 본다(대표님 2026-10-06).
AX_KEEP, AX_PER_BRAND = 12, 2
SHAPE_AXES = ("silhouette", "length", "sleeve_length", "neckline", "design_element", "construction", "pants_type")
PLAIN_PAT = ("단색", "멜란지")
GRAPHIC_MIN = 0.7
CATS = ("tops", "bottoms", "outer", "skirt", "dress")
# 색 계열(같은 계열 .6) — 수집기 COLOR_VOCAB 라벨을 큰 계열로
FAM = {}
for fam, names in {
    "dark": "블랙 차콜 차콜그레이 건메탈 잉크", "navy": "네이비 인디고", "blue": "코발트 블루 스카이블루 터콰이즈 라이트블루 페일블루 데님 틸",
    "white": "화이트 아이보리 크림 내추럴 오트밀 에크루 오프화이트", "grey": "그레이 그레이지 실버 스톤 멜란지그레이 라이트그레이",
    "beige": "베이지 샌드 카멜 탠 토프 라떼 카푸치노 누드", "yellow": "버터 옐로우 머스타드 레몬 골드",
    "brown": "브라운 초코 모카 카라멜 머드 브론즈 클레이 다크브라운", "red": "브릭 레드 버건디 마젠타 와인 체리",
    "pink": "로즈 핑크 코랄 살몬 라이트핑크 피치", "orange": "오렌지 텐저린 앰버", "green": "카키 올리브 그린 민트 라임 세이지 포레스트",
    "purple": "퍼플 라벤더 라일락 모브 그레이프 에그플랜트", "multi": "멀티",
}.items():
    for c in names.split():
        FAM[c] = fam


def load_hier() -> dict[str, str]:
    """item_hier.HIER → 세부 품목 → 큰 품목."""
    import sys
    sys.path.insert(0, str(HERE))
    from item_hier import HIER
    big = {}
    for _sec, cat, base, subs in HIER:
        if base:
            big[base] = cat
        for x in subs:
            big[x] = cat
    return big


# 사진 임베딩 폴더. 옷만 잘라 낸 판(siglip_crop — embed_crops.py --all, 상자로 자른 것과 옷 아닌 픽셀을 지운 것의 평균)이
# 판매중 옷의 90% 이상을 덮으면 그걸 쓰고, 아니면 대표 사진 통째(siglip)를 쓴다. 두 판을 섞지 않는다 — 벡터 공간이 달라 코사인을 못 견준다.
# 3 · 4 · 5차 사람 답(기준 옷 96벌): 사진 항만 바꿔 끼우면 전체 68.7 → 73.0%(+4.3%p, 기준 옷 단위 95% 구간 +0.9~+7.9),
# 처음 본 4 · 5차 62.5 → 69.6%(+7.2%p, +1.0~+13.8). 상세 사진까지 평균 · 최대로 섞으면 오히려 나빴다(−2~−6%p, 2026-10-06).
EMB_DIRS = ("siglip_crop", "siglip")
CROP_MIN_COVER = 0.90


def pick_emb_dir(urls: list[str]) -> str:
    crop = DATA / "emb" / "siglip_crop"
    have = set()
    for p in sorted(crop.glob("part_*.npz")):
        have.update(m["u"] for m in json.loads(str(np.load(p, allow_pickle=False)["meta"])))
    cover = sum(1 for u in urls if u in have) / max(len(urls), 1)
    name = "siglip_crop" if cover >= CROP_MIN_COVER else "siglip"
    print(f"사진 임베딩 {name} (옷만 자른 판이 판매중 옷의 {cover:.0%})", flush=True)
    return name


def load_emb(name: str = "siglip") -> tuple[np.ndarray, dict[str, int]]:
    emb_dir = DATA / "emb" / name
    Es, urls = [], []
    for p in sorted(emb_dir.glob("part_*.npz")):
        z = np.load(p, allow_pickle=False)
        Es.append(np.asarray(z["E"], dtype=np.float32))
        urls.extend(m["u"] for m in json.loads(str(z["meta"])))
    if not Es:
        return np.zeros((0, 768), np.float32), {}
    E = np.concatenate(Es)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    return E, {u: i for i, u in enumerate(urls)}


def brand_matrix(rows: list[dict], tags: dict, brands: list[str]) -> np.ndarray:
    """브랜드끼리 비슷함 B∈[0,1] — 장르 코사인 0.5 + 태그 벡터 코사인 0.5, z 표준화 뒤 전체 쌍 백분위. 지도 밖 브랜드는 0.4."""
    gpath = DATA / "mood" / "brand_graph.json"
    graph = json.loads(gpath.read_text(encoding="utf-8")) if gpath.exists() else {"genres": [], "brands": {}}
    genres = graph.get("genres") or []
    gi = {g: i for i, g in enumerate(genres)}
    # brand_graph.json 의 genre 는 상위 3개(>0.3)만 적힌 요약이다 — 사람 칩은 mood_genre_labels.json 에 전체가 있으니 그걸 먼저 쓴다
    # (코덱스 2026-10-05: 요약본으로 만든 B 는 실험본과 평균 .053 어긋났다). 예측 장르 브랜드는 요약본대로.
    lpath = DATA / "mood_genre_labels.json"
    human = {}
    if lpath.exists():
        L = json.loads(lpath.read_text(encoding="utf-8"))
        human = {s: v["g"] for s, v in (L.get("labels") or {}).items() if v.get("g") and not v.get("unk")}
    bi = {b: i for i, b in enumerate(brands)}
    # 태그 벡터: 브랜드 안 상품 대비 태그 비율 × IDF (부자재 · 기능 · 넥라인 뺌 — 설명 습관만 닮게 해 무드를 헷갈리게 했다)
    F: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    N: collections.Counter = collections.Counter()
    for r in rows:
        t = (tags.get(r["source_url"]) or {}).get("tags") or {}
        s = r["brand_slug"]
        N[s] += 1
        for ax, vals in t.items():
            if ax in ("hardware", "function", "neckline"):
                continue
            for x in set(vals):
                F[s][ax + ":" + x] += 1
        F[s]["item:" + r["item_type"]] += 1
    keys = sorted({k for s in brands for k in F[s]})
    ki = {k: i for i, k in enumerate(keys)}
    df = collections.Counter(k for s in brands for k in F[s] if F[s][k] / max(N[s], 1) >= 0.05)
    idf = np.array([max(np.log(len(brands) / (1 + df[k])), 0.0) for k in keys])
    T = np.zeros((len(brands), len(keys)))
    for s in brands:
        for k, c in F[s].items():
            T[bi[s], ki[k]] = c / max(N[s], 1)
    T *= idf
    T /= np.linalg.norm(T, axis=1, keepdims=True) + 1e-9
    G = np.zeros((len(brands), max(len(genres), 1)))
    known = np.zeros(len(brands), bool)
    for s in brands:
        g = human.get(s) or (graph["brands"].get(s) or {}).get("genre") or {}
        for k, w in g.items():
            if k in gi:
                G[bi[s], gi[k]] = w
        known[bi[s]] = bool(g)
    G /= np.linalg.norm(G, axis=1, keepdims=True) + 1e-9
    n = len(brands)
    off = ~np.eye(n, dtype=bool)
    both = np.outer(known, known) & off

    def z(A):
        v = A[both]
        return (A - v.mean()) / (v.std() + 1e-9)

    H = 0.5 * z(G @ G.T) + 0.5 * z(T @ T.T)
    ranked = np.sort(H[both])
    B = np.searchsorted(ranked, H) / max(len(ranked), 1)
    B[~both] = 0.4
    np.fill_diagonal(B, 0.0)
    return B


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="큰 묶음 몇 개만(연기 시험)")
    ap.add_argument("--pool", help="기준 · 후보를 이 파일의 주소 또는 상품 id(한 줄 하나)로만")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    if args.limit and args.out == str(OUT):
        args.out = str(OUT.with_suffix(".partial.json"))   # 연기 시험이 진짜 결과를 덮지 않게
    t0 = time.time()
    seed = {r["slug"] for r in csv.DictReader(io.StringIO((DATA / "brands_seed.csv").read_text(encoding="utf-8-sig")))}
    import sys
    sys.path.insert(0, str(HERE))
    import platforms
    seed -= set(platforms.APP_HOLD)   # 앱에 안 나가는 매장은 기준에서도 후보에서도 뺀다 — 자리 2.5%를 헛되이 차지하고 있었다(2026-10-05)
    pool = set(Path(args.pool).read_text(encoding="utf-8").split()) if args.pool else None
    rows = [r for r in csv.DictReader(io.StringIO((DATA / "products_full.csv").read_text(encoding="utf-8-sig")))
            if r["status"] == "ON_SALE" and r["category_code"] in CATS and r["brand_slug"] in seed and r["item_type"] and r["price"]
            and r.get("product_no") and (pool is None or r["source_url"] in pool or f'{r["brand_slug"]}-{r["product_no"]}' in pool)]
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    groups_vocab = json.loads((DATA / "vocab_aliases.json").read_text(encoding="utf-8"))["_groups"]
    BIG = load_hier()
    N = len(rows)
    print(f"상품 {N:,}", flush=True)

    # 축별 특징(소재는 혼용률 비율)
    def mat_vec(u, t):
        m = (tags.get(u) or {}).get("mat") or []
        shell = next((x for x in m if not x.get("p")), m[0] if m else None)
        if shell and shell.get("v"):
            tot = sum(p for _f, p in shell["v"]) or 1
            return {("material", f): p / tot for f, p in shell["v"]}
        return {("material", v): 1.0 for v in set(t.get("material") or [])}

    feats = []
    for r in rows:
        t = (tags.get(r["source_url"]) or {}).get("tags") or {}
        f = {}
        for ax in AXES:
            if ax == "material":
                f.update(mat_vec(r["source_url"], t))
            else:
                for v in set(t.get(ax) or []):
                    f[(ax, v)] = 1.0
        feats.append(f)
    vocab: dict = {}
    for f in feats:
        for x in f:
            vocab.setdefault(x, len(vocab))
    VOC = len(vocab)
    color = [(r["representative_color"] or "").split("·")[0] for r in rows]
    cfam = [FAM.get(c, c) if c else "" for c in color]
    brand = np.array([r["brand_slug"] for r in rows])
    price = np.array([float(r["price"]) for r in rows])
    gender = np.array([r["gender_target"] for r in rows])
    famkey = np.array([r["brand_slug"] + ":" + re.sub(r"[\(\[_\-/].*$", "", r["name"]).strip().lower() for r in rows])
    ftype = np.array([r["item_type"] for r in rows])
    gmask = {}
    for ax in PEN_AXES:
        gidx = {v: k for k, (_g, vals) in enumerate(groups_vocab.get(ax, {}).items()) for v in vals}
        gmask[ax] = np.array([sum(1 << gidx[v] for v in set(((tags.get(r["source_url"]) or {}).get("tags") or {}).get(ax) or []) if v in gidx)
                              for r in rows], dtype=np.int64)
    pat_bits: dict[str, int] = {}
    # 그래픽은 사진도 프린트가 크다고 볼 때만(photo_fill 의 graphic ≥ GRAPHIC_MIN) — 작은 로고 프린트는 무지로(대표님 2026-10-06)
    ppath = DATA / "photo_pred.json"
    photo_pred = json.loads(ppath.read_text(encoding="utf-8")) if ppath.exists() else {}
    def patkey(r):
        t = (tags.get(r["source_url"]) or {}).get("tags") or {}
        ks = {v for v in (t.get("pattern") or []) if v not in PLAIN_PAT}
        if "그래픽" in (t.get("design_element") or []) and (photo_pred.get(r["source_url"]) or {}).get("graphic", 0) >= GRAPHIC_MIN:
            ks.add("그래픽")
        return sum(1 << pat_bits.setdefault(v, len(pat_bits)) for v in ks)
    pkey = np.array([patkey(r) for r in rows], dtype=np.int64)
    lk = {v: k for k, vs in enumerate(LOOK_MAT.values()) for v in vs}
    mats = [set(((tags.get(r["source_url"]) or {}).get("tags") or {}).get("material") or []) for r in rows]
    lmask = np.array([sum(1 << lk[v] for v in m if v in lk) for m in mats], dtype=np.int64)
    mobs = np.array([bool(m) for m in mats])
    brands = sorted(set(brand))
    bi = {b: i for i, b in enumerate(brands)}
    BB = brand_matrix(rows, tags, brands)
    EMB_NAME = pick_emb_dir([r["source_url"] for r in rows])
    E, EI = load_emb(EMB_NAME)
    print(f"브랜드 {len(brands)} · 사진 {len(EI):,}장 · 특징 {VOC:,} ({time.time() - t0:.0f}s)", flush=True)

    groups: dict[str, list[int]] = collections.defaultdict(list)
    for i, r in enumerate(rows):
        groups[BIG.get(r["item_type"], r["item_type"])].append(i)
    rng = np.random.default_rng(0)
    out: dict[str, list] = {}
    axes_out: dict[str, dict] = {}
    done_groups = 0
    for gname, idx_list in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if args.limit and done_groups >= args.limit:
            break
        idx = np.array(idx_list)
        n = len(idx)
        if n < 3:
            continue
        done_groups += 1
        # 축별 행렬(묶음 안 IDF) + 관측 마스크
        Ms, obs = {}, {}
        for ax in AXES:
            ri, ci, va = [], [], []
            has = np.zeros(n, bool)
            for k, i in enumerate(idx):
                items = [(x, w) for x, w in feats[i].items() if x[0] == ax]
                if items:
                    has[k] = True
                for x, w in items:
                    ri.append(k); ci.append(vocab[x]); va.append(w)
            Na = has.sum()
            if Na < 2:
                continue
            M = sparse.csr_matrix((va, (ri, ci)), shape=(n, VOC))
            dfv = np.asarray((M > 0).sum(0)).ravel()
            idf = np.minimum(3, 1 + np.log((Na + 1) / (dfv + 1)))
            M = M @ sparse.diags(idf)
            nrm = np.sqrt(M.multiply(M).sum(1)).A1 + 1e-9
            Ms[ax] = (sparse.diags(1 / nrm) @ M).tocsr(); obs[ax] = has
        col = np.array([color[i] for i in idx]); fam = np.array([cfam[i] for i in idx]); cobs = col != ""
        vi = np.array([EI.get(rows[i]["source_url"], -1) for i in idx]); vobs = vi >= 0
        Ev = np.zeros((n, E.shape[1] if E.size else 768), np.float32)
        if vobs.any():
            Ev[vobs] = E[vi[vobs]]
        ok_i = np.where(vobs)[0]
        if len(ok_i) >= 20:
            a_ = rng.choice(ok_i, min(4000, len(ok_i) * 4)); b_ = rng.choice(ok_i, len(a_))
            vs = np.sort(np.einsum("ij,ij->i", Ev[a_], Ev[b_]))
        else:
            vs = np.array([0.0, 1.0])
        bidx = np.array([bi[b] for b in brand[idx]]); pr = price[idx]; gd = gender[idx]; fk = famkey[idx]; ft = ftype[idx]
        gm = {ax: gmask[ax][idx] for ax in PEN_AXES}
        lm = lmask[idx]; mo = mobs[idx]
        pk = pkey[idx]; plain = pk == 0
        shape_obs = np.zeros(n, bool)
        for ax in SHAPE_AXES:
            if ax in obs:
                shape_obs |= obs[ax]
        mat_obs = obs.get("material", np.zeros(n, bool))

        def Tblock(s, e, extra=False):
            num = np.zeros((e - s, n)); den = np.zeros((e - s, n))
            X = {k: np.zeros((e - s, n), np.float32) for k in ("ns", "ds", "nm", "dm", "fs")} if extra else None
            for ax, M in Ms.items():
                w = AXW[ax]; o = obs[ax]
                if not o[s:e].any():
                    continue
                sim = (M[s:e] @ M.T).toarray()
                both = np.outer(o[s:e], o)
                num += w * sim * both; den += w * both
                if extra:
                    if ax in SHAPE_AXES:
                        X["ns"] += w * sim * both; X["ds"] += w * both
                    elif ax == "material":
                        X["nm"] += sim * both; X["dm"] += both
                    elif ax == "finish_wash":
                        X["fs"] += sim * both
            if extra:
                return num, den, X
            w = AXW["color"]
            both = np.outer(cobs[s:e], cobs)
            csim = np.where(col[s:e, None] == col[None, :], 1.0, np.where(fam[s:e, None] == fam[None, :], 0.6, 0.0)) * both
            num += w * csim; den += w * both
            return num, den

        # μ: 묶음 안 무작위 쌍의 평균(수축 목표)
        smp = rng.choice(n, min(n, 300), replace=False)
        mu_num = mu_den = 0.0
        for s in smp[:60]:
            a, b = Tblock(s, s + 1); m = b[0] > 0
            if m.any():
                mu_num += float((a[0][m] / b[0][m]).mean()); mu_den += 1
        mu = mu_num / max(mu_den, 1)
        for s in range(0, n, 600):
            e = min(n, s + 600)
            num, den, X = Tblock(s, e, extra=True)
            Tm = (num + SHRINK * mu) / (den + SHRINK)
            Ts = (X["ns"] + SHRINK * mu) / (X["ds"] + SHRINK)
            for r_ in range(e - s):
                i = s + r_
                row = Tm[r_].copy()
                if PHOTO_W > 0 and vobs[i]:
                    vrow = np.searchsorted(vs, Ev @ Ev[i]) / len(vs)
                    vrow[~vobs] = row[~vobs]           # 사진 없는 후보는 태그 점수 그대로
                    row = (1 - PHOTO_W) * row + PHOTO_W * vrow
                row = row + FINE_BONUS * (ft == ft[i])
                for ax, m in gm.items():
                    if m[i]:
                        row = row - PEN_W * (((m & m[i]) == 0) & (m > 0))
                if lm[i]:
                    row = row - LOOK_PEN_W * (((lm & lm[i]) == 0) & mo)
                row[i] = -1
                same = bidx == bidx[i]
                ok = (~same) & ((gd[i] == "UNISEX") | (gd == "UNISEX") | (gd == gd[i]))
                cand = set(np.argsort(-np.where(ok, row, -9))[:TOP_T].tolist())
                Bi = BB[bidx[i]]
                for b in [b for b in np.argsort(-Bi) if b != bidx[i]][:TOP_BRANDS]:   # 자기 브랜드 빼고 20곳(코덱스 2026-10-05)
                    mask = (bidx == b) & ok
                    if mask.any():
                        js = np.where(mask)[0]
                        cand |= set(js[np.argsort(-row[js])[:PER_NEIGHBOR]].tolist())
                cand = np.array(sorted(c for c in cand if ok[c]))   # 작은 묶음에서 제외 후보가 섞여 들어오던 것(코덱스 2026-10-05)
                if len(cand) == 0:
                    continue
                Sv = (.95 - BRAND_W) * row[cand] + BRAND_W * Bi[bidx[cand]] + PRICE_W * np.exp(-np.abs(np.log(pr[cand] / pr[i])))
                order = cand[np.argsort(-Sv)]; Sv = np.sort(Sv)[::-1]
                keep = []; per: collections.Counter = collections.Counter(); seenf = set(); kj = []
                for j, sv in zip(order, Sv):
                    if per[bidx[j]] >= PER_BRAND or fk[j] in seenf:
                        continue
                    keep += [int(idx[j]), int(round(float(sv) * 1000))]; per[bidx[j]] += 1; seenf.add(fk[j]); kj.append(j)
                    if len(keep) == KEEP * 2:
                        break
                # 앞 RERANK_N 벌은 사진 닮음(옷만 자른 FashionSigLIP 코사인)만으로 다시 줄 세운다. 후보를 고르는 데는 태그 ·
                # 브랜드 · 가격이 돕지만, 고른 뒤 순서는 그것들이 흐트러뜨렸다 — 대표님 블라인드 8차(처음 보는 25문제,
                # 후보 = 이 판 상위 10)에서 지금 순서 57.3% → 사진만 72.6%, 3 · 4 · 5차 140문제에서는 73.0% → 73.6%.
                # 점수는 그대로 두므로 목록이 점수 내림차순이 아닐 수 있다 — 앱은 목록 순서대로 보여 준다(점수로 다시 정렬 금지).
                if RERANK_N and vobs[i] and len(kj) > 1:
                    head = list(range(min(RERANK_N, len(kj))))
                    cs = {p: (float(Ev[kj[p]] @ Ev[i]) if vobs[kj[p]] else -9.0) for p in head}
                    new = sorted(head, key=lambda p: -cs[p]) + list(range(len(head), len(kj)))
                    keep = [x for p in new for x in keep[2 * p:2 * p + 2]]
                # ── 기준별 목록 ──
                base_row = np.clip(row, 0, 1)
                colsim = np.where(col == col[i], 1.0, np.where(fam == fam[i], 0.6, 0.0)) * (cobs & cobs[i])
                same_pat = plain if plain[i] else ((pk & pk[i]) > 0)
                lists = {}
                lists["cp"] = .5 * same_pat + .3 * colsim + .1 * X["fs"][r_] + .1 * base_row
                if vobs[i] or shape_obs[i]:
                    srow = Ts[r_].astype(np.float64)
                    if PHOTO_W > 0 and vobs[i]:
                        srow = .5 * srow + .5 * vrow
                    srow = srow + FINE_BONUS * (ft == ft[i])
                    for ax, m in gm.items():
                        if m[i]:
                            srow = srow - PEN_W * (((m & m[i]) == 0) & (m > 0))
                    lists["s"] = srow
                if mat_obs[i]:
                    tmat = np.where(X["dm"][r_] > 0, X["nm"][r_] / np.maximum(X["dm"][r_], 1e-9), 0.0)
                    look = ((lm & lm[i]) > 0) if lm[i] else np.zeros(n, bool)
                    lists["m"] = .6 * tmat + .2 * look + .2 * base_row
                flags = 1 | (2 if "s" in lists else 0) | (4 if "m" in lists else 0) | (0 if plain[i] else 8)
                axo = {"f": flags}
                for key, sc in lists.items():
                    sc = np.where(ok, sc, -9); sc[i] = -9
                    pick = []; per2: collections.Counter = collections.Counter(); seen2 = set()
                    for j in np.argsort(-sc)[:300]:
                        if sc[j] <= -9:
                            break
                        if per2[bidx[j]] >= AX_PER_BRAND or fk[j] in seen2:
                            continue
                        pick += [int(idx[j]), int(round(float(min(max(sc[j], 0), 1)) * 1000))]; per2[bidx[j]] += 1; seen2.add(fk[j])
                        if len(pick) == AX_KEEP * 2:
                            break
                    if pick:
                        axo[key] = pick
                axes_out[rows[idx[i]]["source_url"]] = axo
                out[rows[idx[i]]["source_url"]] = keep
        print(f"  {gname} {n:,} ({time.time() - t0:.0f}s)", flush=True)

    pid = lambda r: f'{r["brand_slug"]}-{r["product_no"]}'
    index = [pid(r) for r in rows]
    out = {pid(rows[i]): v for i, v in ((ix, out[rows[ix]["source_url"]]) for ix in range(N) if rows[ix]["source_url"] in out)}
    config = {"photo": PHOTO_W, "rerank_photo_top": RERANK_N, "fine_type_bonus": FINE_BONUS, "conflict_penalty": PEN_W, "look_material_penalty": LOOK_PEN_W, "look_material": LOOK_MAT, "brand": BRAND_W, "price": PRICE_W, "shrink": SHRINK, "axes": AXW,
              "pool": "big_category", "keep": KEEP, "photo_emb": EMB_NAME}
    h = hashlib.sha1()   # 설정 · 색 계열표 · 입력 파일 내용 · 임베딩 조각까지 — 같은 주소라도 태그 · 가격이 바뀌면 version 이 바뀐다(코덱스 2026-10-05)
    h.update(json.dumps(config, sort_keys=True).encode())
    h.update(json.dumps(FAM, sort_keys=True).encode())
    for f in ("products_full.csv", "product_tags_full.json", "mood/brand_graph.json", "mood_genre_labels.json", "vocab_aliases.json", "photo_pred.json"):
        fp = DATA / f
        if fp.exists():
            h.update(hashlib.sha1(fp.read_bytes()).digest())
    for p_ in sorted((DATA / "emb" / EMB_NAME).glob("part_*.npz")):
        h.update(p_.name.encode()); h.update(str(p_.stat().st_size).encode())
    h.update(f"n={len(index)}".encode())                      # 상품 집합이 바뀌면(보류 매장 제외 등) version 도 바뀐다 — 2026-10-05 에 같은 version 이 두 번 나왔다
    h.update(json.dumps(sorted(platforms.APP_HOLD)).encode())
    if args.limit:
        h.update(f"limit={args.limit}".encode())
    version = h.hexdigest()[:12]
    Path(args.out).write_text(json.dumps({"version": version, "config": config, "index": index, "items": out}, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    ax_path = Path(args.out).with_name(Path(args.out).name.replace("similar_items", "similar_axes")) \
        if "similar_items" in Path(args.out).name else Path(args.out).with_suffix(".axes.json")
    axes_out = {pid(rows[ix]): axes_out[rows[ix]["source_url"]] for ix in range(N) if rows[ix]["source_url"] in axes_out}
    ax_conf = {"keep": AX_KEEP, "per_brand": AX_PER_BRAND, "shape_axes": SHAPE_AXES, "plain_pattern": PLAIN_PAT, "graphic_min": GRAPHIC_MIN,
               "flags": "1 색·무늬 · 2 모양 · 4 소재 · 8 무늬 있음(없으면 무지)"}
    ax_path.write_text(json.dumps({"version": version, "config": ax_conf, "index": index, "items": axes_out}, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    cnt = collections.Counter(k for v in axes_out.values() for k in v if k != "f")
    print(f"기준별 목록 {len(axes_out):,}벌 · " + " · ".join(f"{k} {c:,}" for k, c in sorted(cnt.items())) + f" · {ax_path.stat().st_size / 1e6:.1f}MB")
    nc = [len(v) // 2 for v in out.values()]
    print(f"저장 {len(out):,}벌 · 후보 평균 {np.mean(nc) if nc else 0:.1f} · 40벌 미만 {sum(1 for c in nc if c < KEEP):,} · version {version} · {time.time() - t0:.0f}s · {Path(args.out).stat().st_size / 1e6:.1f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
