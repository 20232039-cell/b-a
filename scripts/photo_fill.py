"""사진으로 빈칸 채우기 — 소매 길이 · 넥라인을 상품 사진(SigLIP 임베딩)에서 읽는다.

왜: 이름에 소매 말이 없는 티셔츠 · 셔츠 가운데 실측(소매길이) · 글 태그가 둘 다 없는 것이 2,500벌쯤이고(2026-10-05),
넥라인 태그는 상의 · 원피스의 20%에만 있다. 사람이 「반팔 긴팔은 소매 길이로 더 확실하게 판정 필요」라고 했다.
사진은 거의 틀리지 않는다 — 브랜드 단위 교차검증(2026-10-05, 40,159벌): 소매 95.5%, 확신 ≥0.8 인 94%에서 97.0%.
넥라인은 15갈래 72.9% 지만 확신 ≥0.8 인 36%에서 97.6% 다. 그래서 **확신 ≥0.8 일 때만** 쓴다.

어떻게: data/emb/siglip/part_*.npz(PC 에서 만든 Marqo-FashionSigLIP 768차원, 사진 한 장 = 한 줄) 위에 소프트맥스 회귀를
numpy 만으로 학습한다(CI 에 sklearn 이 없다). 라벨은 믿을 만한 것만 —
  소매:   실측 소매 ≤38 → 반팔 · ≥50 → 롱슬리브, 이름이 말한 품목(반팔 · 롱슬리브 · 슬리브리스 · 캐미솔 · 튜브탑 · 브라탑),
          슬리브리스는 글 태그(sleeve_length 가 슬리브리스 하나)도 라벨로 쓴다 — 민소매는 실측 소매가 없어 그것만으론 못 배운다
  넥라인: 글 태그 neckline 이 딱 하나인 상품. 앞선 실행이 사진으로 채운 것(text_sources 에 photo)은 라벨로 안 쓰고 다시 판정한다
          — 안 그러면 두 번째 실행부터 제 예측을 정답으로 배워 검증이 부푼다(코덱스 2026-10-05)
학습은 매번 새로 한다(카탈로그가 자라면 같이 자라게). 브랜드 10%를 떼어 정확도를 찍고, 전부로 다시 학습해 예측한다.

결과: data/photo_pred.json — source_url → {"sleeve": [값, 확신], "neckline": [값, 확신]} (확신 ≥ MIN_P 인 것만, CI 생성물).
  · refine_items.py 가 sleeve 를 읽어 티셔츠 → 반팔 · 롱슬리브, 셔츠 → 하프셔츠 를 가른다(실측 다음, 글 태그 앞).
  · neckline 은 product_tags_full.json 의 빈 neckline 에 채워 넣고 text_sources 에 "photo" 를 적는다.
사진이 없는 상품(임베딩에 없음)은 건드리지 않는다.

  python scripts/photo_fill.py            # 학습 · 예측 · 저장 · 태그에 넥라인 채움
  python scripts/photo_fill.py --dry-run  # 숫자만
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import json
import time
import zlib
from collections import Counter
from pathlib import Path

import numpy as np

DATA = Path(__file__).resolve().parent.parent / "data"
CSV = DATA / "products_full.csv"
TAGS = DATA / "product_tags_full.json"
SIZES = DATA / "product_sizes.json"
EMB = DATA / "emb" / "siglip"
OUT = DATA / "photo_pred.json"

MIN_P = 0.8               # 이보다 덜 확신하면 안 쓴다
SHORT_CM, LONG_CM = 38.0, 50.0   # refine_items 와 같다
SLEEVE_BY_ITEM = {"반팔": "반팔", "롱슬리브": "롱슬리브", "슬리브리스": "슬리브리스", "캐미솔": "슬리브리스", "튜브탑": "슬리브리스",
                  "브라탑": "슬리브리스"}
SLEEVE_TARGETS = {"티셔츠", "셔츠"}          # 뭉뚱그린 품목만 가른다(refine_items 와 같다)
NECK_CATS = {"tops", "dress"}
MIN_CLASS = 150           # 이보다 적은 갈래는 배우지 않는다
CLOTH_CATS = {"tops", "bottoms", "outer", "skirt", "dress", "suiting"}   # 옷 — 큰 품목을 사진으로 채우는 범위
# 품목이 빈 옷은 **큰 품목**(팬츠 · 데님 · 티셔츠 …)만 사진으로 채운다 — 세부 52갈래는 69%(확신 ≥0.8 구간이 6%뿐)라
# 못 쓰고, 큰 품목 14갈래는 83%, 확신 ≥0.8 구간(절반)에서 96.5%다(2026-10-06, 빈 품목 1,441벌 조사). 매장 갈래(아우터 ·
# 상의 · 하의)와 어긋나는 예측은 버린다 — 셋업 수트가 하의 칸에 있는 식의 사례라 어느 쪽이 맞는지 사진만으로 못 정한다.
# 큰 품목 이름이 곧 품목 값인 것(base 가 있는 것)만 채운다 — 「맨투맨·후드」 같은 묶음 이름은 품목 값이 아니다.
from item_hier import HIER
CAT_OF = {}; BASE_OF = {}; SEC_OF = {}
for _sec, _cat, _base, _subs in HIER:
    for _s in _subs:
        CAT_OF[_s] = _cat
    if _base:
        CAT_OF[_base] = _cat; BASE_OF[_cat] = _base
    SEC_OF[_cat] = _sec
GROUP_OF_SEC = {"아우터": "아우터", "상의": "상의", "하의": "하의", "원피스": "상의"}   # products_full.csv 의 group 칸은 원피스를 상의에 둔다


def load_emb() -> tuple[np.ndarray, dict[str, int]]:
    Es, urls = [], []
    for p in sorted(glob.glob(str(EMB / "part_*.npz"))):
        z = np.load(p, allow_pickle=False)
        Es.append(np.asarray(z["E"], dtype=np.float32))
        urls.extend(m["u"] for m in json.loads(str(z["meta"])))
    if not Es:
        return np.zeros((0, 768), np.float32), {}
    E = np.concatenate(Es)
    E /= np.linalg.norm(E, axis=1, keepdims=True) + 1e-9
    return E, {u: i for i, u in enumerate(urls)}


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def train(X: np.ndarray, y: np.ndarray, k: int, iters: int = 300, lr: float = 0.05, l2: float = 1e-4) -> tuple[np.ndarray, np.ndarray]:
    """소프트맥스 회귀, Adam 전체 배치. X 는 단위 벡터라 로짓이 작으니 lr 을 크게 둔다."""
    n, d = X.shape
    W = np.zeros((d, k), np.float32); b = np.zeros(k, np.float32)
    mW = np.zeros_like(W); vW = np.zeros_like(W); mb = np.zeros_like(b); vb = np.zeros_like(b)
    Y = np.eye(k, dtype=np.float32)[y]
    # 갈래가 치우쳐 있으니(롱슬리브 24k · 슬리브리스 4k) 드문 갈래에 무게를 더 준다 — 다수 갈래로 쏠리지 않게
    cnt = np.bincount(y, minlength=k).astype(np.float32)
    sw = (n / (k * np.maximum(cnt, 1)))[y] ** 0.5
    sw /= sw.mean()
    for t in range(1, iters + 1):
        P = softmax(X @ W + b)
        G = (P - Y) * sw[:, None] / n
        gW = X.T @ G + l2 * W; gb = G.sum(0)
        for prm, g, m, v in ((W, gW, mW, vW), (b, gb, mb, vb)):
            m *= 0.9; m += 0.1 * g
            v *= 0.999; v += 0.001 * g * g
            prm -= lr * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
    return W, b


def fit_report(name: str, X: np.ndarray, labels: list[str], brands: list[str]) -> tuple[list[str], np.ndarray, np.ndarray] | None:
    """브랜드 10%를 떼어 정확도를 찍고(확신 ≥MIN_P 구간 따로), 전부로 다시 학습한 W · b 를 돌려준다."""
    cnt = Counter(labels)
    classes = sorted(c for c, v in cnt.items() if v >= MIN_CLASS)
    if len(classes) < 2:
        print(f"{name}: 배울 갈래가 모자란다 {dict(cnt)}")
        return None
    ci = {c: i for i, c in enumerate(classes)}
    keep = [i for i, l in enumerate(labels) if l in ci]
    X = X[keep]; y = np.array([ci[labels[i]] for i in keep]); br = np.array([brands[i] for i in keep])
    hold = np.array([zlib.crc32(b.encode()) % 10 == 0 for b in br])     # 브랜드 단위로 떼야 같은 상품 사진이 양쪽에 안 간다
    if hold.all() or not hold.any():
        print(f"{name}: 브랜드가 너무 적어 검증을 못 뗀다({len(set(br))}곳) — 전부로 학습만")
    else:
        W, b = train(X[~hold], y[~hold], len(classes))
        P = softmax(X[hold] @ W + b); pred = P.argmax(1); conf = P.max(1); yt = y[hold]
        acc = float((pred == yt).mean())
        hi = conf >= MIN_P
        acc_hi = float((pred[hi] == yt[hi]).mean()) if hi.any() else float("nan")
        mid = (conf >= 0.5) & ~hi
        acc_mid = float((pred[mid] == yt[mid]).mean()) if mid.any() else float("nan")
        print(f"{name}: 갈래 {len(classes)} · 학습 {int((~hold).sum()):,} · 검증 {int(hold.sum()):,}(브랜드 10%) · 정확도 {acc:.1%}"
              f" · 확신≥{MIN_P} 비율 {hi.mean():.0%} 그 안 정확도 {acc_hi:.1%} · 0.5~{MIN_P} 비율 {mid.mean():.0%} 그 안 {acc_mid:.1%}")
        per = Counter(); tot = Counter()
        for p_, t_ in zip(pred, yt):
            tot[classes[t_]] += 1; per[classes[t_]] += int(p_ == t_)
        print("   갈래별:", " · ".join(f"{c} {per[c] / tot[c]:.0%}({tot[c]:,})" for c in classes if tot[c]))
    W, b = train(X, y, len(classes))
    return classes, W, b


def sleeve_cm(sizes: dict, url: str) -> float | None:
    v = [x for x in ((sizes.get(url) or {}).get("sizes") or {}).get("소매길이", []) if x is not None]
    return min(v) if v else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    E, EI = load_emb()
    print(f"사진 임베딩 {len(EI):,}장 ({time.time() - t0:.0f}s)")
    if not EI:
        if not args.dry_run:
            OUT.write_text("{}\n", encoding="utf-8")   # 임베딩이 없는데 옛 판정이 남아 있으면 refine_items 가 그걸 믿는다 — 비운다(파일은 둬서 워크플로 cp 가 안 깨지게)
        return 0
    rows = list(csv.DictReader(io.StringIO(CSV.read_text(encoding="utf-8-sig"))))
    tags = json.loads(TAGS.read_text(encoding="utf-8")) if TAGS.exists() else {}
    sizes = json.loads(SIZES.read_text(encoding="utf-8")) if SIZES.exists() else {}
    cloth = [r for r in rows if r["source_url"] in EI and r.get("category_code") in CLOTH_CATS]
    rows = [r for r in cloth if r.get("category_code") in NECK_CATS]
    tg = lambda r: ((tags.get(r["source_url"]) or {}).get("tags") or {})
    pred: dict[str, dict] = {}

    # ── 큰 품목(품목이 빈 옷만) ──────────────────────────────────────────────
    lab, brs, xs = [], [], []
    for r in cloth:
        c = CAT_OF.get(r["item_type"])
        if c:
            lab.append(c); brs.append(r["brand_slug"]); xs.append(EI[r["source_url"]])
    m = fit_report("큰 품목", E[xs], lab, brs)
    if m:
        classes, W, b = m
        tgt = [r for r in cloth if not r["item_type"] and r["status"] == "ON_SALE"]
        n_item, n_drop = 0, Counter()
        if tgt:
            P = softmax(E[[EI[r["source_url"]] for r in tgt]] @ W + b)
            for r, p in zip(tgt, P):
                if p.max() < MIN_P:
                    n_drop["확신 부족"] += 1; continue
                c = classes[int(p.argmax())]
                if GROUP_OF_SEC.get(SEC_OF.get(c, ""), "") != (r.get("group") or ""):
                    n_drop["매장 갈래와 어긋남"] += 1; continue
                if c not in BASE_OF:
                    n_drop["품목 값 없는 묶음"] += 1; continue
                pred.setdefault(r["source_url"], {})["item"] = [BASE_OF[c], round(float(p.max()), 3)]
                n_item += 1
        print(f"   품목 빈 옷 {len(tgt):,}벌 중 {n_item:,}벌 채움 (버림: " + " · ".join(f"{k} {v:,}" for k, v in n_drop.items()) + "): "
              + " · ".join(f"{c} {n:,}" for c, n in Counter(v["item"][0] for v in pred.values() if "item" in v).most_common()))

    # ── 그래픽(프린트) 크기 확인 ──────────────────────────────────────────────
    # 글의 「그래픽」 태그는 큰 프린트와 작은 로고 프린트를 못 가른다 — 「9999 로고 프린트」 스웨트팬츠도 그래픽이다.
    # 「비슷한 옷」의 색·무늬 기준은 작은 로고를 무지로 본다(대표님 2026-10-06). 그래서 그래픽 태그가 있는 옷마다
    # 사진이 그래픽이라고 보는 확률을 적어 두고, similar_items 는 0.7 이상일 때만 무늬로 친다
    # (판매중 그래픽 태그 상의 10,705벌 중 6,631 · 바지 1,915 중 536 — 0.7 이상은 눈으로 봐도 큰 프린트였다).
    tg_of = lambda r: (tg(r).get("design_element") or [])
    lab, brs, xs = [], [], []
    for r in cloth:
        d = tg_of(r)
        if d:
            lab.append("g" if "그래픽" in d else "n"); brs.append(r["brand_slug"]); xs.append(EI[r["source_url"]])
    m = fit_report("그래픽", E[xs], lab, brs)
    if m:
        classes, W, b = m
        tgt = [r for r in cloth if "그래픽" in tg_of(r)]
        if tgt and "g" in classes:
            P = softmax(E[[EI[r["source_url"]] for r in tgt]] @ W + b)[:, classes.index("g")]
            for r, p_ in zip(tgt, P):
                pred.setdefault(r["source_url"], {})["graphic"] = round(float(p_), 3)
            print(f"   그래픽 태그 {len(tgt):,}벌 · 사진 확률 ≥0.7 {int((P >= 0.7).sum()):,}벌")

    # ── 소매 ────────────────────────────────────────────────────────────────
    lab, brs, xs = [], [], []
    for r in rows:
        cm = sleeve_cm(sizes, r["source_url"])
        l = SLEEVE_BY_ITEM.get(r["item_type"]) or ("반팔" if cm is not None and cm <= SHORT_CM else "롱슬리브" if cm is not None and cm >= LONG_CM else None)
        if l is None and (tg(r).get("sleeve_length") or []) == ["슬리브리스"]:
            l = "슬리브리스"
        if l:
            lab.append(l); brs.append(r["brand_slug"]); xs.append(EI[r["source_url"]])
    m = fit_report("소매", E[xs], lab, brs)
    if m:
        classes, W, b = m
        tgt = [r for r in rows if r["item_type"] in SLEEVE_TARGETS]
        P = softmax(E[[EI[r["source_url"]] for r in tgt]] @ W + b)
        n_hi = 0
        for r, p in zip(tgt, P):
            if p.max() >= MIN_P:
                pred.setdefault(r["source_url"], {})["sleeve"] = [classes[int(p.argmax())], round(float(p.max()), 3)]
                n_hi += 1
        print(f"   티셔츠 · 셔츠 {len(tgt):,}벌 중 확신 ≥{MIN_P} {n_hi:,}벌: "
              + " · ".join(f"{c} {sum(1 for v in pred.values() if v.get('sleeve', [None])[0] == c):,}" for c in classes))

    # ── 넥라인 ──────────────────────────────────────────────────────────────
    from_photo = lambda r: "photo" in ((tags.get(r["source_url"]) or {}).get("text_sources") or [])
    lab, brs, xs = [], [], []
    for r in rows:
        v = tg(r).get("neckline") or []
        if len(v) == 1 and not from_photo(r):
            lab.append(v[0]); brs.append(r["brand_slug"]); xs.append(EI[r["source_url"]])
    m = fit_report("넥라인", E[xs], lab, brs)
    n_neck = 0
    if m:
        classes, W, b = m
        tgt = [r for r in rows if not tg(r).get("neckline") or from_photo(r)]
        P = softmax(E[[EI[r["source_url"]] for r in tgt]] @ W + b)
        for r, p in zip(tgt, P):
            if p.max() >= MIN_P:
                pred.setdefault(r["source_url"], {})["neckline"] = [classes[int(p.argmax())], round(float(p.max()), 3)]
                n_neck += 1
        print(f"   넥라인 빈 상품 {len(tgt):,}벌 중 확신 ≥{MIN_P} {n_neck:,}벌 채움")

    print(f"사진 판정 {len(pred):,}벌 ({time.time() - t0:.0f}s)")
    if args.dry_run:
        return 0
    OUT.write_text(json.dumps(pred, ensure_ascii=False, indent=0, sort_keys=True) + "\n", encoding="utf-8")
    if n_neck and tags:
        for u, e in tags.items():
            src = e.get("text_sources") or []
            if "photo" in src:                      # 앞선 실행이 채운 것은 비우고 이번 판정으로 다시 채운다
                (e.get("tags") or {}).pop("neckline", None)
                src.remove("photo")
        for u, v in pred.items():
            if "neckline" in v and u in tags:
                t = tags[u].setdefault("tags", {})
                if not t.get("neckline"):
                    t["neckline"] = [v["neckline"][0]]
                    tags[u].setdefault("text_sources", []).append("photo")
        TAGS.write_text(json.dumps(tags, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
