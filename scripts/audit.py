"""데이터에 난 이상을 매번 같은 잣대로 세어 둔다 → data/_audit.json · data/_audit.csv

왜 만들었나: 손으로 하루에 열 개 넘는 버그를 찾았는데(2026-09-05: 표 칸 밀림 · 뭉개진 칸 ·
눕힌 표 오해 · 색 어휘 두 벌 · CHERRY 가 레드 · soft pink 가 화이트 · 사이즈 이름이 HEM),
사람이 매번 눈으로 볼 수는 없다(사람 지시: 「버그도 계속 주기적으로 찾아내」).

무엇을 보나 — 「있을 수 없는 것」만 센다. 애매한 것은 세지 않는다.
  사이즈  사이즈가 커지는데 값이 작아짐 · 이름이 치수 이름이거나 색 · 이름 중복 ·
          라벨 범위 밖 · 한 상품 안에서 라벨마다 값 개수가 다름
  태그    한 축 안에서 서로 반대인 값이 함께 붙음 · 어휘에 없는 값
  색      rollup 에 없는 색 · 색이 빈 상품
  설명    남의 상품이 섞인 것으로 보이는 상품(가격이 네 번 넘게 나옴)
  상품    같은 브랜드·이름·색이 여럿 · 가격이 말이 안 됨

전판과 견준다: data/_audit.json 에 지난 수치가 있으면 늘어난 항목을 ⚠ 로 짚는다.
늘어났다는 것은 방금 고친 무엇이 딴 데를 깨뜨렸다는 뜻이다.

    py scripts/audit.py            # 세고 저장하고 견준다
    py scripts/audit.py --no-save  # 세기만
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "scripts"))

OUT_JSON = DATA / "_audit.json"
OUT_CSV = DATA / "_audit.csv"

ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL"]
# 한 축 안에서 함께 붙으면 말이 안 되는 짝. 「후드+터틀넥」은 진짜 있는 옷이라 넣지 않는다
# (「후디드 터틀넥 티셔츠」, 2026-09-05).
CONTRADICT = [
    ("sleeve_length", "반팔", "롱슬리브"), ("sleeve_length", "슬리브리스", "롱슬리브"),
    ("sleeve_length", "슬리브리스", "반팔"),
    ("length", "크롭", "맥시"), ("length", "크롭", "롱기장"), ("length", "쇼츠", "맥시"),
    ("silhouette", "슬림핏", "오버핏"), ("silhouette", "슬림핏", "루즈핏"),
    ("silhouette", "타이트", "오버핏"),
    ("pattern", "단색", "스트라이프"), ("pattern", "단색", "체크"), ("pattern", "단색", "카모"),
    ("material", "인조가죽", "천연가죽"),
]
PRICE_RX = re.compile(r"(?:krw|won)\s*\d{1,3},\d{3}|\d{1,3},\d{3}\s*(?:krw|won|원)", re.I)


def rank(n: str):
    n = (n or "").upper().strip()
    if n in ORDER:
        return ORDER.index(n)
    m = re.fullmatch(r"0*(\d{1,3})", n)
    return 100 + int(m.group(1)) if m else None


def load_latest(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[d.get("product_no")] = d
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    vocab = json.loads((DATA / "vocab_aliases.json").read_text(encoding="utf-8"))
    labels = json.loads((DATA / "size_labels.json").read_text(encoding="utf-8"))
    ranges = labels["_ranges_cm"]
    rollup = vocab.get("_color_rollup", {})
    try:
        import crawl_cafe24 as cc
        colors = {a.strip().lower() for vs in cc.COLOR_VOCAB.values() for a in vs}
        colors |= {k.lower() for k in cc.COLOR_VOCAB}
    except Exception:
        colors = set()
    try:
        import size_from_ocr as sz
        canon = sz.canon_label
    except Exception:
        canon = lambda x: None  # noqa: E731

    n = Counter()
    rows: list[dict] = []

    def flag(kind: str, url: str, detail: str) -> None:
        n[kind] += 1
        if len(rows) < 4000:
            rows.append({"무엇": kind, "링크": url, "자세히": detail})

    # ── 사이즈
    for u, e in sizes.items():
        names = e.get("size_names") or []
        s = e.get("sizes") or {}
        low = [str(x).strip() for x in names]
        if low and colors and len(low) > 1 and all(x.lower() in colors for x in low):
            flag("사이즈 이름이 색이다", u, str(low))
        if low and all(canon(x) for x in low):
            flag("사이즈 이름이 치수 이름이다", u, str(low))
        if len(low) != len(set(low)) and len(low) > 1:
            flag("사이즈 이름이 겹친다", u, str(low))
        lens = {len(v) for v in s.values()}
        if len(lens) > 1:
            flag("라벨마다 값 개수가 다르다", u, json.dumps(
                {k: len(v) for k, v in s.items()}, ensure_ascii=False))
        # 사람이 사진을 보고 적어 준 값(manual_sizes.csv)은 범위보다 우선이다 — 여기서 안 센다.
        for lab, vals in ({} if e.get("source") == "manual" else s).items():
            lo, hi = ranges.get(lab, (0, 1e9))[:2] if isinstance(ranges.get(lab), (list, tuple)) else (0, 1e9)
            for x in vals:
                if x is not None and not (lo <= x <= hi):
                    flag("값이 라벨 범위 밖", u, f"{lab}={x} (범위 {lo}~{hi})")
        if len(low) >= 2:
            rs = [rank(x) for x in low]
            if all(r is not None for r in rs) and sorted(rs) == rs and len(set(rs)) == len(rs):
                for lab, vals in s.items():
                    v = [x for x in vals if x is not None]
                    if len(v) == len(low) and any(b < a - 1.0 for a, b in zip(v, v[1:])):
                        flag("사이즈가 커지는데 값이 작아진다", u, f"{low} {lab}={v}")
                        break

    # ── 태그
    axes = [a for a in vocab if not a.startswith("_")]
    known = {ax: set(vocab[ax]) for ax in axes}
    for u, p in tags.items():
        t = p.get("tags") or {}
        for ax, v1, v2 in CONTRADICT:
            got = t.get(ax) or []
            if v1 in got and v2 in got:
                flag(f"서로 반대인 태그 — {v1}+{v2}", u, ax)
        for ax, vals in t.items():
            if ax == "color":
                for x in vals:
                    if rollup and x not in rollup:
                        flag("색이 rollup 에 없다", u, x)
                continue
            for x in vals:
                if ax in known and x not in known[ax]:
                    flag("어휘에 없는 태그 값", u, f"{ax}/{x}")

    # ── 설명에 남의 상품
    for f in sorted(glob.glob(str(DATA / "crawl" / "*.jsonl"))):
        if os.path.basename(f).startswith("_"):
            continue
        for d in load_latest(Path(f)).values():
            txt = (d.get("description") or "") + " " + (d.get("detail_text") or "")
            if len(PRICE_RX.findall(txt)) >= 4:
                flag("설명에 남의 상품이 섞였다", d.get("source_url", ""), f"가격 {len(PRICE_RX.findall(txt))}회")

    # ── 상품 목록
    seen = defaultdict(list)
    for r in csv.DictReader((DATA / "products_full.csv").open(encoding="utf-8-sig")):
        seen[(r["brand_slug"], (r["name"] or "").strip().lower(),
              (r.get("representative_color") or "").strip())].append(r)
        try:
            price = float(r["price"] or 0)
        except ValueError:
            price = -1
        if price <= 1000 or price > 3_000_000:
            flag("가격이 말이 안 된다", r["source_url"], r["price"])
        if not (r.get("representative_color") or "").strip():
            flag("색이 비었다", r["source_url"], r["name"][:40])
    for key, v in seen.items():
        if len(v) > 1:
            flag("브랜드·이름·색이 똑같은 상품이 여럿", v[0]["source_url"], f"{len(v)}벌 — {key[1][:40]}")

    now = dict(n)
    prev = {}
    if OUT_JSON.exists():
        try:
            prev = json.loads(OUT_JSON.read_text(encoding="utf-8")).get("counts", {})
        except json.JSONDecodeError:
            prev = {}

    lines = []
    for k in sorted(now, key=lambda x: -now[x]):
        was = prev.get(k)
        if was is None:
            mark, note = "🆕", ""
        elif now[k] > was:
            mark, note = "⚠", f" (전 {was} → +{now[k] - was})"
        elif now[k] < was:
            mark, note = "✓", f" (전 {was} → -{was - now[k]})"
        else:
            mark, note = " ", ""
        lines.append(f"{mark} {now[k]:6d}  {k}{note}")
    for k in sorted(set(prev) - set(now)):
        lines.append(f"✓      0  {k} (전 {prev[k]} → 사라짐)")
    print("\n".join(lines) or "이상 없음")

    if not args.no_save:
        OUT_JSON.write_text(json.dumps({"counts": now}, ensure_ascii=False, indent=1), encoding="utf-8")
        with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, ["무엇", "링크", "자세히"])
            w.writeheader()
            w.writerows(rows)
        print(f"\n→ {OUT_JSON.name} · {OUT_CSV.name} ({len(rows)}행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
