"""데님 워싱 단계(dw)를 **사진으로** — 이름 · 색 · 옵션 낱말로 못 정한 데님 옷만.

왜 있는가 — 앱 세션 부탁(2026-10-09): 색 조합 가이드가 데님을 연청 · 중청 · 진청 · 생지 · 흑청으로 나눠 말하는데, 판매중 데님 옷
5,972벌 중 2,239벌은 대표색이 「블루 · 인디고 · 네이비」뿐이라 낱말로 단계를 못 정한다(export_app_data.denim_wash). 사람 지시
(2026-10-10 「하이쿠 말고 깃액션으로 판정 안 되나」)로 언어 모델 대신 Actions 에서 돈다.

어떻게 — 사진 밝기만 재는 방법은 먼저 시험했다(600장): 연 · 중 · 진 셋을 68%만 맞혔고, 확실한 것만 골라도 80~88%였다. 모델 피부 ·
배경 · 다른 옷이 섞여서다. 그래서 공개 이미지 모델(CLIP ViT-B-32)로 사진마다 특징값을 뽑고, **이름으로 단계가 분명한 데님
(정답 약 1,700벌)** 으로 작은 분류기(로지스틱 회귀)를 학습한다. 5겹 교차검증으로 단계마다 「확신 몇 이상이면 90% 넘게 맞는가」를
재고, 그 문턱을 넘는 것만 빈칸 상품에 적는다. 90%에 닿는 문턱이 없는 단계(중청일 공산이 크다)는 아예 안 적는다 — 추측 금지.

결과: data/denim_wash_photo.json — {"v", "model", "report": {단계: {threshold, precision, coverage, n}}, "items": {source_url: {"w", "p"}}}.
export_app_data 가 dw 빈칸에만 이 값을 쓴다.

사용: python scripts/denim_wash_photo.py [--limit N] [--min-precision 0.9]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "denim_wash_photo.json"
sys.path.insert(0, str(ROOT / "scripts"))
import export_app_data as E  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2; +https://github.com/20232039-cell/layer-brand-agent)"}
# 정답으로 쓰는 단계 — 사진이 비슷한 블랙 · 화이트 · 컬러도 넣어야 분류기가 「블루 아님」을 배운다.
LEARN = ("연청", "중청", "진청", "생지", "흑청", "블랙", "화이트", "컬러")
# 빈칸 상품에 적을 수 있는 단계 — 빈칸은 대표색이 블루 · 인디고 · 네이비인 옷이라 블루 계열만.
APPLY = ("연청", "중청", "진청", "생지", "흑청")


def is_denim(r: dict, t: dict) -> bool:
    return r.get("category") in E.DW_CATS and (r.get("category") == "Denim" or r.get("subtype") == "데님팬츠"
                                                or (t.get("material") or [""])[0] == "데님")


def fetch(u: str) -> Image.Image | None:
    try:
        b = requests.get(u, headers=UA, timeout=25).content
        im = Image.open(io.BytesIO(b)).convert("RGB")
        im.thumbnail((448, 448))
        return im
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="시험용 — 정답 · 빈칸 각각 이만큼만")
    ap.add_argument("--min-precision", type=float, default=0.9)
    ap.add_argument("--min-support", type=int, default=15, help="문턱을 넘은 표본이 이보다 적으면 그 단계는 안 쓴다")
    # fashion-clip: 패션 상품 사진 80만 장으로 더 배운 CLIP(patrickjohncyh/fashion-clip) — 사람 제안(2026-10-10 「패션클립 그 모델로 하는 건?」).
    ap.add_argument("--model", choices=["clip", "fashion-clip"], default="clip")
    args = ap.parse_args()

    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    lab: list[tuple[str, str, str]] = []
    unl: list[tuple[str, str]] = []
    with open(DATA / "products_full.csv", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if r.get("status") != "ON_SALE" or not r.get("image_url"):
                continue
            t = (tags.get(r["source_url"]) or {}).get("tags") or {}
            if not is_denim(r, t):
                continue
            w = E.denim_wash(r.get("name") or "", r.get("representative_color") or "", r.get("options") or "")
            if w in LEARN:
                lab.append((r["source_url"], r["image_url"], w))
            elif not w:
                unl.append((r["source_url"], r["image_url"]))
    if args.limit:
        lab, unl = lab[: args.limit], unl[: args.limit]
    print(f"정답 {len(lab)}벌 {dict(Counter(w for *_, w in lab))} · 빈칸 {len(unl)}벌")

    import torch
    if args.model == "fashion-clip":
        from transformers import CLIPModel, CLIPProcessor
        fc = CLIPModel.from_pretrained("patrickjohncyh/fashion-clip").eval()
        fp = CLIPProcessor.from_pretrained("patrickjohncyh/fashion-clip")
        model_name = "fashion-clip(patrickjohncyh/fashion-clip) + 로지스틱 회귀"

        def encode(ims):
            o = fc.get_image_features(**fp(images=ims, return_tensors="pt"))
            # 새 transformers 는 텐서 대신 출력 묶음을 돌려준다 — 투영된 특징은 pooler_output(512)에 있다(2026-10-10 Actions 판에서 깨짐)
            return o if isinstance(o, torch.Tensor) else o.pooler_output
    else:
        import open_clip
        model, _, prep = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
        model.eval()
        model_name = "open_clip ViT-B-32 laion2b_s34b_b79k + 로지스틱 회귀"

        def encode(ims):
            return model.encode_image(torch.stack([prep(im) for im in ims]))

    def embed(urls: list[str]) -> np.ndarray:
        with ThreadPoolExecutor(16) as ex:
            ims = list(ex.map(fetch, urls))
        vecs = np.full((len(urls), 512), np.nan, dtype=np.float32)
        idx = [i for i, im in enumerate(ims) if im is not None]
        for s in range(0, len(idx), 64):
            part = idx[s: s + 64]
            with torch.no_grad():
                v = encode([ims[i] for i in part])
                v = v / v.norm(dim=-1, keepdim=True)
            vecs[part] = v.numpy()
        return vecs

    Xl = embed([u for _, u, _ in lab])
    ok = ~np.isnan(Xl).any(1)
    Xl, yl = Xl[ok], np.array([w for *_, w in lab])[ok]
    print(f"정답 사진 받음 {ok.sum()}/{len(lab)}")

    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_predict, StratifiedKFold
    clf = LogisticRegression(max_iter=3000, C=2.0, class_weight="balanced")
    proba = cross_val_predict(clf, Xl, yl, cv=StratifiedKFold(5, shuffle=True, random_state=0), method="predict_proba")
    classes = sorted(set(yl))
    pred = np.array(classes)[proba.argmax(1)]
    conf = proba.max(1)
    print(f"교차검증 전체 정답률 {np.mean(pred == yl):.3f}")
    report: dict[str, dict] = {}
    for c in APPLY:
        if c not in classes:
            continue
        best = None
        for th in np.arange(0.4, 0.99, 0.02):
            m = (pred == c) & (conf >= th)
            n = int(m.sum())
            if n < args.min_support:
                break
            prec = float(np.mean(yl[m] == c))
            if prec >= args.min_precision:
                best = {"threshold": round(float(th), 2), "precision": round(prec, 3), "n": n,
                        "coverage": round(n / max(1, int(np.sum(yl == c))), 3)}
                break
        report[c] = best or {"threshold": None, "note": f"정밀도 {args.min_precision} 에 닿는 문턱 없음 — 안 씀"}
        print(c, report[c])

    clf.fit(Xl, yl)
    items: dict[str, dict] = {}
    if unl and any(v.get("threshold") for v in report.values()):
        Xu = embed([u for _, u in unl])
        oku = ~np.isnan(Xu).any(1)
        pu = clf.predict_proba(Xu[oku])
        cls = clf.classes_
        for (src, _), p in zip([x for x, k in zip(unl, oku) if k], pu):
            c = cls[p.argmax()]
            th = (report.get(c) or {}).get("threshold")
            if th and p.max() >= th:
                items[src] = {"w": str(c), "p": round(float(p.max()), 3)}
    print(f"빈칸 {len(unl)}벌 중 사진으로 정함 {len(items)}벌 {dict(Counter(v['w'] for v in items.values()))}")
    OUT.write_text(json.dumps({
        "v": date.today().isoformat(), "model": model_name,
        "trained_on": dict(Counter(yl.tolist())), "cv_accuracy": round(float(np.mean(pred == yl)), 3),
        "min_precision": args.min_precision, "report": report, "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
