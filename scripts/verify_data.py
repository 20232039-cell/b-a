#!/usr/bin/env python3
"""수집한 데이터가 스스로 모순되지 않는지 본다 — 상품 ↔ 카테고리 ↔ 사이즈 ↔ 설명.

왜 따로 두나: 커버리지(coverage.py)는 「얼마나 모았나」를 재고, 이 파일은 「모은 게
맞나」를 잰다. xlim 을 영문몰(USD)로 훑어 921건이 통째로 사라진 걸 두 주 동안 못 봤다
— 퍼센트만 보면 안 보이는 종류의 고장이라 검사를 따로 박아 둔다(2026-09-05).

각 검사는 「이건 확실히 틀렸다」만 센다. 애매한 건 안 센다 — 거짓 경보가 쌓이면
아무도 안 본다.

    py scripts/verify_data.py            # 요약
    py scripts/verify_data.py --show 8   # 검사마다 예시 8개
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_cafe24 import classify_category, match_acc      # noqa: E402
from coverage import is_apparel                            # noqa: E402

# 카테고리마다 「이 라벨이 있어야 말이 된다」 — 하의 표에 어깨, 상의 표에 밑위는 서로 다른
# 옷의 표를 붙인 것이다(사이즈 표를 상품이 아니라 매장 안내에서 긁어 온 경우).
EXPECT = {
    "tops":    ({"가슴", "어깨", "총장", "소매길이", "암홀", "화장"}, {"밑위", "허벅지"}),
    "outer":   ({"가슴", "어깨", "총장", "소매길이", "암홀", "화장"}, {"밑위", "허벅지"}),
    "bottoms": ({"허리", "밑위", "허벅지", "총장", "밑단", "엉덩이"}, {"어깨", "가슴", "암홀"}),
    "skirt":   ({"허리", "총장", "밑단", "엉덩이"}, {"어깨", "암홀", "밑위"}),
}
# 값이 이 밖이면 단위를 잘못 읽었거나 남의 표다(cm 기준, 사람 몸 치수의 바깥 테두리).
SANE = {"총장": (20, 175), "어깨": (25, 85), "가슴": (25, 100), "허리": (20, 80),
        "밑위": (10, 55), "허벅지": (12, 60), "엉덩이": (25, 90), "밑단": (6, 65),
        "소매길이": (5, 90), "암홀": (10, 50), "화장": (40, 110)}
JUNK_NAME = re.compile(r"개인\s*결제|결제창|test|테스트|샘플|sample|배송비|추가금|적립금|"
                       r"^[¥₩\W]{2,}$|documentation|campaign film|스탭스냅", re.I)


def latest_rows():
    for f in sorted(glob.glob(str(DATA / "crawl" / "*.jsonl"))):
        slug = os.path.basename(f)[:-6]
        if slug.startswith("_"):
            continue
        seen = {}
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            seen[d["product_no"]] = d
        for d in seen.values():
            yield slug, d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", type=int, default=5)
    args = ap.parse_args()

    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    fails: dict[str, list] = collections.defaultdict(list)
    per_brand_price: dict[str, list] = collections.defaultdict(list)
    n_live = 0

    for slug, d in latest_rows():
        name = d.get("name") or ""
        url = d.get("source_url") or ""
        if d.get("soldout"):
            continue
        n_live += 1
        code = classify_category(name, d.get("category_names") or [], d.get("description") or "")
        per_brand_price[slug].append(int(d.get("price") or 0))

        # 1. 상품이 아닌 행이 상품으로 올라와 있다
        if JUNK_NAME.search(name) or len(name.strip()) < 2:
            fails["상품이 아닌 행"].append((slug, name, url))

        # 2. 사진이 없다 — 앱이 보여줄 수 없다
        if not d.get("image_url") and not d.get("gallery"):
            fails["대표 사진 없음"].append((slug, name, url))

        st = (sizes.get(url) or {}).get("sizes") or {}

        # 3. 카테고리와 사이즈 표의 라벨이 서로 다른 옷을 가리킨다
        if st and code in EXPECT:
            ok, never = EXPECT[code]
            wrong = set(st) & never
            if wrong and not (set(st) & ok):
                fails["카테고리 ↔ 사이즈 라벨 불일치"].append(
                    (slug, f"{name} [{code}] {sorted(st)}", url))

        # 4. 값이 사람 몸 치수 밖이다
        for lab, vals in st.items():
            lo, hi = SANE.get(lab, (0, 999))
            bad = [v for v in vals if isinstance(v, (int, float)) and not (lo <= v <= hi)]
            if bad:
                fails["사이즈 값이 범위 밖"].append((slug, f"{name} {lab}={bad}", url))
                break

        # 5. 같은 표 안에서 사이즈 수가 라벨마다 다르다 — 값이 밀려 들어간 것
        if st and len({len(v) for v in st.values() if isinstance(v, list)}) > 1:
            fails["라벨마다 사이즈 개수가 다름"].append(
                (slug, f"{name} {[(k, len(v)) for k, v in st.items()]}", url))

        # 6. 사이즈가 커질수록 커져야 하는데 뒤집혔다(가슴·허리 — 두 벌 이상일 때만)
        for lab in ("가슴", "허리", "어깨", "총장"):
            v = st.get(lab)
            v = [x for x in v if isinstance(x, (int, float))] if isinstance(v, list) else None
            if v and len(v) >= 3 and v == sorted(v, reverse=True) and v[0] != v[-1]:
                fails["사이즈 순서가 거꾸로"].append((slug, f"{name} {lab}={v}", url))
                break

        # 7. 옷인데 소재가 「가죽 100%」처럼 옷이 아닌 것뿐 — 표기 자체가 없는 건 커버리지 몫
        mat = ((tags.get(url) or {}).get("tags") or {}).get("material")
        if mat and isinstance(mat, list) and code in EXPECT:
            if all(re.fullmatch(r"[\d.%\s]+", str(m) or "") for m in mat):
                fails["소재 값이 숫자뿐"].append((slug, f"{name} {mat}", url))

        # 8. 잡화인데 옷 사이즈 표가 붙어 있다(매장 공용 안내표를 물어 온 것)
        if st and code in ("bags", "accessories", "jewelry", "headwear", "lifestyle"):
            if set(st) & {"어깨", "가슴", "밑위", "허벅지"}:
                fails["잡화에 옷 사이즈 표"].append((slug, f"{name} [{code}] {sorted(st)}", url))

        # 9. 이름은 잡화인데 옷으로 세고 있다(또는 그 반대)
        acc = match_acc(name)
        if acc and is_apparel(d):
            fails["잡화 이름인데 의류로 셈"].append((slug, f"{name} → {acc}", url))

    # 10. 매장 통째로 값이 이상하다 — 외화 매장(xlim/en.xlim.link)이 이 꼴이었다
    for slug, ps in per_brand_price.items():
        if len(ps) < 20:
            continue
        cheap = sum(1 for p in ps if p <= 1000)
        if cheap / len(ps) > 0.8:
            fails["매장 값이 통째로 1,000 이하"].append(
                (slug, f"{cheap}/{len(ps)}건 — 외화 매장을 훑은 게 아닌지 official_url 확인", ""))

    print(f"판매중 {n_live}건 검사\n")
    if not fails:
        print("  걸린 것 없음")
    for k, v in sorted(fails.items(), key=lambda x: -len(x[1])):
        by = collections.Counter(s for s, _, _ in v)
        print(f"  {k}: {len(v)}건  ({', '.join(f'{b} {n}' for b, n in by.most_common(4))})")
        for s, t, u in v[:args.show]:
            print(f"      {s} · {t}")
            if u:
                print(f"        {u}")
    print(f"\n합계 {sum(len(v) for v in fails.values())}건")


if __name__ == "__main__":
    main()
