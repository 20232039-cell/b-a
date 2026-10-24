"""주간 수집의 묶음 나누기 — weekly.yml 의 plan 잡이 부른다(2026-11-11, 워크플로 안에 박혀 있던 것을 꺼냈다).

왜 고쳤나: 예전 무게는 「상품 수」(카페24 가 아닌 매장만 3배)였다. 2026-11-09 판(run 42526643058)은 묶음마다 무게가
7,600 안팎으로 고르게 나뉘었는데도(묶음 10 이 7,593 · 묶음 16 이 7,584) 걸린 시간은 77분 ~ 330분(상한)으로 네 배 넘게
벌어졌다 — 상품 하나를 확인하는 데 드는 시간이 매장마다 1.5초(nuria)에서 14초(art-if-acts)까지 다르기 때문이다.
상품 수는 시간을 말해 주지 않는다. (그 차이는 갤러리 사진 수를 따라간다 — 판매중 평균 9장 넘는 매장은 확인 한 번에 7.4초,
2장 밑은 1.7초. parse_detail 이 갤러리 사진마다 큰 칸이 있는지 매장에 묻는 탓이다: crawl_cafe24._big_slot_ok.)

그래서 **지난 판에 실제로 걸린 시간**을 무게로 쓴다. weekly_update 가 매장마다 crawl/_weekly_log.jsonl 에 sec 를 남긴다
(다른 틀 매장도 2026-11-11 부터). 기록이 없는 매장은 상품 수 × (기록 있는 매장들의 상품당 시간 중앙값)으로 어림한다.
상품 수는 products_full.csv 에서 세되, 거기 없는 매장은 수집 파일의 줄 수로 센다 — eenk · we11done 은 CSV 에 한 줄도 없어
무게가 0 이었고, 무게 0 인 매장이 몰리는 묶음 16 에 얹혀 4시간 37분 · 1시간 36분을 돌았다(같은 판).
씨앗(brands_seed)에서 빠진 매장은 weekly_update 가 바로 건너뛰므로 무게 0 이다.

  - 어림 = 최근 세 줄의 sec 가운데 가장 큰 값. 주중에 손으로 돌린 짧은 판(값 갱신이 거의 없는 판)에 속지 않으려고.
  - 지난 판에 마감(--hours)에 걸렸거나(cut), 전체 판에 들었어야 하는데 기록이 없는(러너가 꺼진) 매장은 「굶은 매장」 —
    어림을 두 배로 치고 묶음의 맨 앞에 세운다. 일꾼 수보다 매장이 많은 묶음에서도 가장 먼저 시작한다.
  - 한 매장은 한 묶음에만 둔다(매장 서버에 초당 한 번). 그래서 **혼자서 마감보다 오래 걸리는 매장은 나눠서 못 줄인다** —
    그런 매장은 경고로 알리고, weekly_update --hours 가 본 데까지 저장한다(못 본 값 갱신은 다음 판의 맨 앞).

사용: python scripts/weekly_plan.py [--brands "a b"] [--buckets 20] [--max-brands 6] [--hours 5]
      `matrix=<json>` 한 줄을 $GITHUB_OUTPUT 에 붙인다(없으면 화면에). 사람 읽을 요약은 화면과 GITHUB_STEP_SUMMARY 에.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
# 기록이 **하나도** 없을 때(첫 판)만 쓰는 옛 무게 — 다른 틀 매장은 상품 페이지를 하나씩 2초 간격으로 연다(2026-10-26).
# 기록이 있으면 곱하지 않는다: 2026-11-09 판에서 다른 틀 매장은 상품당 2 ~ 5초(eenk 5.3 · we11done 4.4 · doucan 3.1)로
# 카페24 매장의 중앙값과 비슷했다 — 거기에 3을 또 곱하면 ader-error 한 곳이 646분으로 잡힌다(실제는 96분 안쪽).
OTHER_WEIGHT = 3
STARVED_FACTOR = 2.0    # 굶은 매장의 어림에 곱한다 — 끝까지 가 본 적이 없어 지난 기록이 실제보다 작다
RECENT = 3              # 최근 몇 줄의 sec 를 볼지
FULL_RUN_RATIO = 0.6    # 하루치 기록이 가장 많던 날의 이 비율을 넘으면 「전체 판」으로 본다(손으로 돌린 몇 곳짜리 판을 가른다)


def read_log(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("slug") and d.get("date"):
            rows.append(d)
    return rows


def last_full_run(log: list[dict]) -> str:
    per_day = collections.Counter(r["date"] for r in log)
    if not per_day:
        return ""
    most = max(per_day.values())
    return max(d for d, n in per_day.items() if n >= FULL_RUN_RATIO * most)


def estimate(counts: dict[str, int], log: list[dict], other: set[str],
             dead: frozenset[str] | set[str] = frozenset()) -> tuple[dict[str, float], set[str]]:
    """매장별 어림(초)과 굶은 매장. counts: 매장 → 상품 수 · other: 카페24 가 아닌 매장 · dead: 스크립트가 건너뛸 매장."""
    by: dict[str, list[dict]] = collections.defaultdict(list)
    for r in sorted(log, key=lambda r: r["date"]):
        by[r["slug"]].append(r)
    full = last_full_run(log)
    est: dict[str, float] = {}
    starved: set[str] = set()
    for b, rows in by.items():
        if b not in counts or b in dead:
            continue
        secs = [r["sec"] for r in rows if isinstance(r.get("sec"), (int, float)) and r["sec"] > 0][-RECENT:]
        last = rows[-1]
        # 굶음: 지난 줄이 마감에 걸렸거나, 지난 전체 판에 줄이 없다(상품이 있는 매장인데 — 러너가 꺼져 기록도 못 남겼다).
        # 다른 틀 매장은 2026-11-11 전에는 기록을 안 남겼다 — 그때 줄이 없는 것은 굶은 것이 아니다.
        missed = full and last["date"] < full and counts[b] > 0 and secs and (b not in other or last.get("other"))
        if last.get("cut") or missed:
            starved.add(b)
        if secs:
            est[b] = max(secs) * (STARVED_FACTOR if b in starved else 1.0)
    # 기록 없는 매장 — 상품 수 × 상품당 시간(기록 있는 카페24 매장의 중앙값). 기록이 하나도 없으면 상품 수 그대로.
    per = [est[b] / counts[b] for b in est if counts[b] >= 20 and b not in other and b not in starved]
    k = statistics.median(per) if per else 1.0
    for b, n in counts.items():
        if b in dead:
            est[b] = 0.0
        elif b not in est:
            est[b] = n * k * (OTHER_WEIGHT if b in other and not per else 1) * (STARVED_FACTOR if b in starved else 1.0)
    return est, starved


def split(est: dict[str, float], starved: set[str], buckets: int, max_brands: int) -> list[list[str]]:
    """무거운 매장부터 가장 가벼운 묶음에. 한 매장은 한 묶음에만 — 매장당 초당 1회를 지키려고."""
    groups: list[list[str]] = [[] for _ in range(buckets)]
    load = [0.0] * buckets
    for b in sorted(est, key=lambda b: (-est[b], b)):
        cand = [j for j in range(buckets) if len(groups[j]) < max_brands] or list(range(buckets))
        i = min(cand, key=lambda j: (load[j], len(groups[j])))
        groups[i].append(b)
        load[i] += est[b]
    # 묶음 안 차례 = 시작 차례. 굶은 매장이 맨 앞, 그다음 오래 걸리는 순.
    return [sorted(g, key=lambda b: (b not in starved, -est[b], b)) for g in groups if g]


def load_counts(data: Path) -> dict[str, int]:
    cnt: collections.Counter = collections.Counter()
    with open(data / "products_full.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            cnt[r["brand_slug"]] += 1
    for p in os.listdir(data / "crawl"):          # CSV 에 없는 매장(새 매장 · 앱에 안 내보내는 매장)은 수집 파일의 줄 수로
        if p.endswith(".jsonl") and not p.startswith("_") and p[:-6] not in cnt:
            with open(data / "crawl" / p, "rb") as f:
                cnt[p[:-6]] = sum(1 for line in f if line.strip())
    return dict(cnt)


def dead_brands(data: Path, slugs, other: set[str]) -> set[str]:
    """weekly_update 가 「brands_seed 에 없거나 official_url 없음」으로 바로 건너뛰는 매장(카페24 쪽만 씨앗을 본다)."""
    with open(data / "brands_seed.csv", encoding="utf-8-sig") as f:
        alive = {r["slug"] for r in csv.DictReader(f) if (r.get("official_url") or "").strip()}
    return {b for b in slugs if b not in other and b not in alive}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", default="", help="공백으로 나눈 slug 목록. 비우면 전부")
    ap.add_argument("--buckets", default="20")
    ap.add_argument("--max-brands", default="6")
    ap.add_argument("--hours", type=float, default=5.0, help="묶음의 마감(weekly_update --hours 와 같은 값) — 경고 기준")
    args = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import platforms

    want = args.brands.split()
    counts = load_counts(DATA)
    other = set(platforms.NOT_CAFE24)
    est, starved = estimate(counts, read_log(DATA / "crawl" / "_weekly_log.jsonl"), other, dead_brands(DATA, counts, other))
    if want:
        est = {b: v for b, v in est.items() if b in want}
    groups = split(est, starved, max(1, int(args.buckets or 20)), max(1, int(args.max_brands or 6)))
    # load = 묶음에서 가장 오래 걸릴 매장의 어림(분). 매장들이 나란히 도니 묶음 시간은 대개 이 값이다.
    inc = [{"bucket": i + 1, "brands": " ".join(g), "load": round(max(est[b] for b in g) / 60)}
           for i, g in enumerate(groups)]
    matrix = "matrix=" + json.dumps({"include": inc}, ensure_ascii=False)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(matrix + "\n")
    else:
        print(matrix)

    limit = args.hours * 3600
    slow = sorted((b for b in est if est[b] >= 0.8 * limit), key=lambda b: -est[b])
    lines = [f"브랜드 {len(est)} → 묶음 {len(inc)} · 묶음별 가장 긴 매장(분) {sorted((x['load'] for x in inc), reverse=True)}",
             f"묶음별 매장 시간 합(분) {sorted((round(sum(est[b] for b in g) / 60) for g in groups), reverse=True)}"]
    if starved & set(est):
        lines.append(f"굶은 매장(지난 판에 마감 · 중단) {len(starved & set(est))}곳 — 묶음 맨 앞에 세움: {' '.join(sorted(starved & set(est)))}")
    print("\n".join(lines), flush=True)
    if slow:
        # 나눠서는 못 줄인다 — 한 매장은 한 러너에서만 돈다. --hours 가 본 데까지 저장한다.
        print(f"::warning title=혼자서도 마감에 걸릴 매장::어림이 마감 {args.hours:g}시간의 80% 를 넘는 매장 {len(slow)}곳: "
              + ", ".join(f"{b} {est[b] / 60:.0f}분" for b in slow), flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 묶음 나누기\n\n" + "\n\n".join(lines) + "\n\n| 묶음 | 가장 긴 매장(분) | 시간 합(분) | 매장 |\n|---|---|---|---|\n")
            for x, g in zip(inc, groups):
                f.write(f"| {x['bucket']} | {x['load']} | {round(sum(est[b] for b in g) / 60)} | {' '.join(g)} |\n")


if __name__ == "__main__":
    main()
