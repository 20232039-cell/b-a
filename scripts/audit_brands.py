"""브랜드마다 수집 상태를 점검한다 — 사람이 상품 페이지를 열어보던 일을 대신한다.

왜: 2026-09-04 하루에만 서로 다른 오류가 네 종류 나왔다.
  · 칸 밀림      rough-side 843벌이 「1 size 95」의 95(한국 사이즈 번호)를 기장으로 먹어 전부 한 칸씩 밀림
  · 매장 공용표   espionage·wkndrs 는 키링·신발·청바지가 전부 같은 값, thebarnnet 은 모델 치수가 붙어 있었음
  · 둘레/단면    moif 는 영어 라벨(chest 132)이라 둘레인 걸 못 알아채고 값을 통째로 버림
  · 낡은 기록    open-yy 742벌은 아코디언 그릇을 넣기 전 기록이라 본문 0자 (지금 받으면 1,886자)
전부 브랜드 단위로 보면 드러났다. 상품 하나하나가 아니라 브랜드의 분포와 표본을 본다.

사용: py scripts/audit_brands.py            # 저장된 데이터만 (빠름)
      py scripts/audit_brands.py --live     # 브랜드마다 판매 중인 상품 2벌을 실제로 받아 낡은 기록을 찾는다
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CRAWL = DATA / "crawl"
GARM = {"Tops", "Pants", "Outerwear", "Knitwear", "Shirts", "Denim", "Skirts", "Dresses"}
KEY_LABELS = ("어깨", "가슴", "총장", "허리")


def load():
    rows = {(r["brand_slug"], str(r["product_no"])): r
            for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))}
    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    u2k = {r["source_url"]: k for k, r in rows.items()}
    crawl: dict[tuple, dict] = {}
    for p in sorted(CRAWL.glob("*.jsonl")):
        if p.name.startswith("_"):
            continue
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                d = json.loads(l)
                crawl[(d["brand_slug"], str(d["product_no"]))] = d
    return rows, sizes, u2k, crawl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="브랜드마다 상품 2벌을 실제로 받아 저장된 기록과 견준다")
    ap.add_argument("--brands", nargs="*")
    args = ap.parse_args()
    rows, sizes, u2k, crawl = load()
    sized = {u2k[u] for u in sizes if u in u2k}

    # 라벨별 중앙값 — 브랜드 중앙값이 크게 벗어나면 재는 기준이 다르거나 표가 밀린 것이다.
    # 성별로 나눠 견준다: glowny 는 여성 크롭 피티드 브랜드라 어깨 32.5cm 가 맞는 값인데, 전체 중앙값(52)과
    # 비교해서 오탐이 났다(2026-09-04 사람 지적). 남녀를 섞으면 여성 브랜드가 전부 걸린다.
    glob_vals = defaultdict(list)
    brand_vals = defaultdict(lambda: defaultdict(list))
    brand_gender = {}
    for u, v in sizes.items():
        k = u2k.get(u)
        if not k or rows[k].get("category") not in GARM:
            continue
        g = (rows[k].get("gender_target") or "UNISEX").upper()
        g = "WOMEN" if "WOMEN" in g else ("MEN" if "MEN" in g else "UNISEX")
        brand_gender.setdefault(k[0], Counter())[g] += 1
        for lab in KEY_LABELS:
            for x in v["sizes"].get(lab, []):
                if isinstance(x, (int, float)):
                    glob_vals[(g, lab)].append(x)
                    brand_vals[k[0]][lab].append(x)
    gmed_all = {key: statistics.median(xs) for key, xs in glob_vals.items() if xs}
    brand_gender = {b: c.most_common(1)[0][0] for b, c in brand_gender.items()}

    # 같은 표가 품목 셋 이상에 열 벌 넘게 = 매장 공용표
    shared = defaultdict(lambda: [0, set()])
    for k, d in crawl.items():
        st = d.get("size_table")
        if isinstance(st, dict) and st:
            sig = (k[0], json.dumps(st, sort_keys=True, ensure_ascii=False))
            shared[sig][0] += 1
            r = rows.get(k)
            if r and r.get("category"):
                shared[sig][1].add(r["category"])
    shop_chart = Counter(b for (b, _), (n, c) in shared.items() if n >= 10 and len(c) >= 3)

    brands = args.brands or sorted({k[0] for k in rows})
    print(f"{'브랜드':20s} {'옷':>5s} {'사이즈':>6s} {'본문':>6s} {'그림':>6s}  주의")
    flagged = []
    for b in brands:
        ks = [k for k in rows if k[0] == b and rows[k].get("category") in GARM]
        if len(ks) < 20:
            continue
        n = len(ks)
        sz = sum(1 for k in ks if k in sized) / n
        txt = sum(1 for k in ks if len(crawl.get(k, {}).get("detail_text") or "") >= 100) / n
        img = sum(1 for k in ks if crawl.get(k, {}).get("detail_images")) / n
        notes = []
        g = brand_gender.get(b, "UNISEX")
        for lab in KEY_LABELS:
            xs = brand_vals[b].get(lab) or []
            ref = gmed_all.get((g, lab))
            if len(xs) >= 20 and ref:
                m = statistics.median(xs)
                if m > ref * 1.35 or m < ref * 0.7:
                    notes.append(f"{lab} 중앙 {m:.0f} ({g} 중앙 {ref:.0f})")
        if shop_chart.get(b):
            notes.append(f"매장 공용표 {shop_chart[b]}종 (걸러내는 중)")
        if sz < 0.5:
            notes.append("사이즈 절반 미만")
        if txt < 0.3:
            notes.append("본문 거의 없음")
        line = f"{b:20s} {n:5d} {sz:6.0%} {txt:6.0%} {img:6.0%}  {' · '.join(notes)}"
        print(line)
        if notes:
            flagged.append((b, notes))
    print(f"\n주의가 붙은 브랜드 {len(flagged)}곳")

    if not args.live:
        return
    # 낡은 기록 찾기 — 지금 파서로 받으면 저장된 것보다 많이 나오는가
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import requests
    import crawl_cafe24 as cc
    print("\n■ 낡은 기록 점검 (브랜드마다 2벌)")
    UA = {"User-Agent": cc.UA if hasattr(cc, "UA") else "Mozilla/5.0 (compatible; LayerCatalog/0.2)"}
    for b, _ in (flagged or [(x, None) for x in brands]):
        cand = [d for k, d in crawl.items() if k[0] == b and not d.get("soldout") and d.get("source_url")]
        if not cand:
            continue
        base = "/".join(cand[0]["source_url"].split("/")[:3])
        shop = cc.Shop(slug=b, base=base, brand_gender="unisex")
        for d in cand[-2:]:
            try:
                h = requests.get(d["source_url"], headers=UA, timeout=25).text
            except Exception as e:
                print(f"   {b:16s} 받기 실패 {e}")
                continue
            p = cc.parse_detail(h, d["source_url"], shop) or {}
            old_t, new_t = len(d.get("detail_text") or ""), len(p.get("detail_text") or "")
            old_s, new_s = len(d.get("size_table") or {}), len(p.get("size_table") or {})
            mark = "낡음" if (new_t > old_t * 2 + 100 or new_s > old_s) else ("나빠짐" if (new_t * 2 + 100 < old_t or new_s < old_s) else "같음")
            print(f"   {b:16s} {mark:5s} 본문 {old_t:5d}→{new_t:5d} · 사이즈칸 {old_s}→{new_s}  {(p.get('name') or d['name'])[:24]}")
            time.sleep(1.2)


if __name__ == "__main__":
    main()
