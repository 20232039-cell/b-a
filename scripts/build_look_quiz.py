"""룩 사진 속 「다른 부위」의 옷을 카탈로그에서 찾게 하는 문제를 만든다.

왜 이렇게 만드나(2026-09-04): 앞선 시험은 내가 후보 8개를 뽑아 주고 고르게 해서,
정답이 후보에 없는 일이 잦았다(6건 중 2건). 후보를 표본으로 뽑으면 시험이 아니라
요행이 된다. 그래서 여기서는 표본을 쓰지 않는다 — 그 브랜드·그 시즌·그 부위의
상품을 전부 후보로 놓는다.

부위로 가르는 이유: 사진에 걸린 상품(확정)이 상의면 상의는 이미 아는 것이고,
찾을 것은 하의나 아우터다. 같은 부위를 또 찾게 하면 문제가 성립하지 않는다
(앞선 q3 이 그 실수였다 — 확정이 하의인데 데님을 찾으라고 했다).

시즌으로 좁히는 이유: 한 부위 전체가 수백 벌이면 한눈에 볼 수 없다. insilence 는
사진 주소에 26FW·26SS 가 박혀 있어 시즌이 확실하고, 한 시즌 한 부위가 20~70벌로
줄어 격자에 담긴다. 시즌 단서가 없는 브랜드는 이 문제를 만들지 않는다.

사진은 원본 그대로만 쓴다 — 자르면 변형이고 저작권상 불리하다(사람 결정 2026-09-04).
그래서 이어붙인 띠가 주력인 브랜드(frizmworks·kirsh)는 대상이 아니다.

사용: py scripts/build_look_quiz.py --brand insilence --seasons 25FW,24SS --n 4 --out /tmp/quiz
"""
from __future__ import annotations
import argparse, base64, collections, csv, io, json, random, re, time
from pathlib import Path
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UA = {"User-Agent": "Mozilla/5.0 (compatible; LayerCatalog/0.2)"}
GARM = {"Tops", "Pants", "Outerwear", "Knitwear", "Shirts", "Denim", "Skirts", "Dresses"}
PART = {"Tops": "상의", "Knitwear": "상의", "Shirts": "상의", "Dresses": "상의",
        "Pants": "하의", "Denim": "하의", "Skirts": "하의", "Outerwear": "아우터"}
SEA = re.compile(r"/(\d\d(?:SS|FW))/", re.I)


def fetch(url: str, w: int, sess, q=74):
    if url.startswith("//"):
        url = "https:" + url
    for k in range(3):
        try:
            r = sess.get(url, headers=UA, timeout=20)
            if r.status_code != 200:
                return None
            im = Image.open(io.BytesIO(r.content)).convert("RGB")
            if im.width > w:
                im = im.resize((w, max(1, int(im.height * w / im.width))), Image.LANCZOS)
            b = io.BytesIO()
            im.save(b, "JPEG", quality=q, optimize=True)
            return im, "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
        except Exception:
            time.sleep(1.2 * (k + 1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="insilence")
    ap.add_argument("--seasons", default="", help="비우면 후보가 15~70벌인 시즌을 알아서 고른다")
    ap.add_argument("--n", type=int, default=4)
    ap.add_argument("--out", required=True)
    ap.add_argument("--delay", type=float, default=0.22)
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)

    rows = {r["source_url"]: r for r in csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig"))
            if r["brand_slug"] == args.brand and r.get("category") in GARM and r.get("image_url")}
    look = json.loads((DATA / "look_photos.json").read_text(encoding="utf-8"))

    def season_of(u):
        for ph in (look.get(u) or {}).get("photos", []):
            m = SEA.search(ph)
            if m:
                return m.group(1).upper()
        return None

    pool = collections.defaultdict(list)       # (시즌, 부위) -> [source_url]
    for u, r in rows.items():
        s = season_of(u)
        if s:
            pool[(s, PART[r["category"]])].append(u)

    seasons = [x for x in args.seasons.split(",") if x] or sorted(
        {k[0] for k, v in pool.items() if 15 <= len(v) <= 70})
    rnd = random.Random(4242)
    sess = requests.Session()
    meta = []
    for se in seasons:
        if len(meta) >= args.n:
            break
        # 후보가 격자에 담기는 부위
        parts = [p for p in ("하의", "아우터", "상의") if 15 <= len(pool[(se, p)]) <= 70]
        for target in parts:
            if len(meta) >= args.n:
                break
            # 확정은 목표와 다른 부위여야 한다
            anchors = [u for p in ("상의", "하의", "아우터") if p != target for u in pool[(se, p)]]
            rnd.shuffle(anchors)
            got = None
            for au in anchors:
                phs = (look.get(au) or {}).get("photos", [])
                phs = [p for p in phs if SEA.search(p)]
                if not phs:
                    continue
                g = fetch(phs[0], 340, sess); time.sleep(args.delay)
                if not g:
                    continue
                im, src = g
                if im.width / im.height > 0.95:      # 세로 사진만
                    continue
                got = (au, phs[0], src); break
            if not got:
                continue
            au, phurl, looksrc = got
            cands = sorted(pool[(se, target)], key=lambda u: rows[u]["name"])
            cimgs = []
            for cu in cands:
                g = fetch(rows[cu]["image_url"], 150, sess); time.sleep(args.delay)
                if not g:
                    continue
                cimgs.append({"url": cu, "name": rows[cu]["name"], "price": int(rows[cu]["price"] or 0),
                              "cat": rows[cu]["category"], "img": g[1], "im": g[0]})
            if len(cimgs) < 10:
                continue
            q = len(meta) + 1
            # 격자 판 — 글자는 넣지 않는다(PIL 기본 글꼴에 한글이 없다)
            per = 8
            cw = max(i["im"].width for i in cimgs) + 6
            chh = max(i["im"].height for i in cimgs) + 6
            rowsn = (len(cimgs) + per - 1) // per
            lk = Image.open(io.BytesIO(base64.b64decode(looksrc.split(",", 1)[1])))
            lk.thumbnail((300, 420))
            cv = Image.new("RGB", (max(lk.width + 14 + per * cw, 400), max(lk.height, rowsn * chh) + 8), (255, 255, 255))
            cv.paste(lk, (0, 4))
            for i, c in enumerate(cimgs):
                x = lk.width + 14 + (i % per) * cw
                y = 4 + (i // per) * chh
                cv.paste(c["im"], (x, y))
            cv.save(out / f"q{q}.png")
            meta.append({"q": q, "brand": args.brand, "season": se, "target": target,
                         "anchor": {"url": au, "name": rows[au]["name"], "cat": rows[au]["category"],
                                    "part": PART[rows[au]["category"]]},
                         "look_url": phurl, "look": looksrc,
                         "cands": [{k: v for k, v in c.items() if k != "im"} for c in cimgs]})
            print(f"  q{q} {se} · 확정 {PART[rows[au]['category']]}({rows[au]['category']}) "
                  f"→ {target} 찾기 · 후보 {len(cimgs)}벌 전체", flush=True)
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print(f"\n문제 {len(meta)}개 → {out}")


if __name__ == "__main__":
    main()
