"""크리마 핏(CREMA FIT) 위젯의 실측표를 받아 둔다.

왜 있는가 — roem 은 결손 옷 185벌의 상세 그림이 사진뿐이고, 상품 페이지 서버 HTML 에도
사이즈가 없다. 브라우저로 열어 오가는 요청을 잡아 보니(2026-09-25) 「고객님 사이즈를
찾아보세요」 칸이 크리마 핏 위젯이고, 그 위젯의 실측표는 그냥 주소 하나다:

    https://fit3.cre.ma/<크리마 매장 코드>/fit/products/<상품 번호>/combined_fit_product

자바스크립트 없이 GET 한 번에 「SIZE INFO」 표가 HTML 로 온다(요청에 따라 JS 문자열에 싼 조각).
roem 결손 상품 12벌을 찔러 12벌 모두 표가 있었다.

크리마 매장 코드는 대개 매장 도메인이다(roem.com). 등록 안 된 상품은 204 로 빈 응답이다.

무엇을 남기나 — data/crawl/cremafit/<slug>.jsonl 에 {product_no, source_url, size_text,
girth}. size_text 는 fetch_sizeguide 와 같은 「라벨 | 값 | 값」 줄이라 size_from_ocr 의
사이즈가이드 글 갈래가 그대로 읽는다. 크롤 기록을 덮지 않는다.

**둘레는 단면으로 바꿔 적는다.** 크리마 표는 「가슴둘레 98 104」처럼 둘레로 온다. 사이즈가이드 글
갈래(parse_grid_rows)는 둘레를 모르므로 여기서 반으로 나누고 라벨에서 「둘레」를 뗀다 —
size_from_ocr 가 다른 자리에서 하는 것과 같은 규칙이다(fix_value girth). 바꾼 라벨은 girth 에 남긴다.

사용: python scripts/fetch_cremafit.py --brands roem
      python scripts/fetch_cremafit.py --brands roem --code roem=roem.com --only-missing
"""
from __future__ import annotations
import argparse, csv, html, json, re, time
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA, CRAWL = ROOT / "data", ROOT / "data" / "crawl"
OUT = CRAWL / "cremafit"
FIT = "https://fit3.cre.ma/{code}/fit/products/{no}/combined_fit_product"
NON = {"shoes", "bags", "accessories", "headwear", "jewelry", "lifestyle", "pet", "other"}
GIRTH = re.compile(r"\s*둘레\s*$")
# 둘레 → 단면 라벨. 「팔둘레」를 그냥 「팔」로 두면 소매길이로 읽힌다 — 팔 둘레의 반은 소매통이다.
# 표에 없는 둘레는 버린다(목둘레 같은 것 — 앱 칸이 없다).
GIRTH_TO = {"가슴": "가슴", "허리": "허리", "엉덩이": "엉덩이", "힙": "엉덩이", "허벅지": "허벅지",
            "밑단": "밑단", "팔": "소매통", "소매": "소매통", "팔통": "소매통"}


def table_rows(fragment: str) -> list[list[str]]:
    """「SIZE INFO」 표의 줄들 — 한 줄이 한 라벨(맨 윗줄은 Size | 사이즈 이름들)."""
    i = fragment.find("size_table")
    if i < 0:
        return []
    seg = fragment[i:fragment.find("</table>", i)]
    rows = []
    for tr in re.findall(r"<tr\b.*?</tr>", seg, re.S):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<t[hd]\b.*?</t[hd]>", tr, re.S)]
        cells = [c for c in cells if c]
        if len(cells) >= 2:
            rows.append(cells)
    return rows


def fetch(sess: requests.Session, code: str, no: str) -> list[list[str]]:
    r = sess.get(FIT.format(code=code, no=no), timeout=25)
    if r.status_code != 200:
        return []
    # 받는 쪽에 따라 몸통이 둘로 온다 — 그냥 HTML(표가 그대로) 또는 JS 문자열에 싼 조각
    if "updates:" in r.text:
        m = re.search(r':"((?:[^"\\]|\\.)*)"', r.text[r.text.find("updates:") + 8:])
        return table_rows(json.loads('"' + m.group(1) + '"')) if m else []
    return table_rows(r.text)


def to_text(rows: list[list[str]]) -> tuple[str, list[str]]:
    """「라벨 | 값 …」 줄로. 둘레 줄은 반으로 나눠 단면으로 적는다."""
    out, girth = [], []
    for cells in rows:
        lab, vals = cells[0], cells[1:]
        if GIRTH.search(lab) and lab.lower() != "size":
            try:
                vals = [f"{float(v) / 2:g}" for v in vals]
            except ValueError:
                continue          # 둘레인데 수가 아니면 버린다 — 반으로 못 나눈 둘레는 틀린 값이다
            base = GIRTH.sub("", lab).replace(" ", "")
            if base not in GIRTH_TO:
                continue
            girth.append(lab)
            lab = GIRTH_TO[base]
        out.append(" | ".join([lab] + vals))
    return "\n".join(out), girth


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", required=True, help="쉼표로 나눈 slug")
    ap.add_argument("--code", default="", help="slug=크리마코드 (쉼표로 여럿). 없으면 매장 도메인")
    ap.add_argument("--only-missing", action="store_true", help="사이즈 없는 옷만")
    ap.add_argument("--delay", type=float, default=1.0, help="한 매장에 초당 하나")
    a = ap.parse_args()
    codes = dict(x.split("=", 1) for x in a.code.split(",") if "=" in x)
    have = set()
    if a.only_missing and (DATA / "product_sizes.json").exists():
        have = set(json.load(open(DATA / "product_sizes.json", encoding="utf-8")))
    rows = list(csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig")))
    OUT.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    for slug in [b.strip() for b in a.brands.split(",") if b.strip()]:
        mine = [r for r in rows if r["brand_slug"] == slug and r["category_code"] not in NON
                and (r["source_url"] not in have)]
        if not mine:
            print(f"{slug}: 볼 상품 없음")
            continue
        code = codes.get(slug) or urlparse(mine[0]["source_url"]).netloc.replace("www.", "")
        path = OUT / f"{slug}.jsonl"
        done = {}
        if path.exists():
            for ln in open(path, encoding="utf-8"):
                d = json.loads(ln)
                done[d["source_url"]] = d
        got = 0
        for n, r in enumerate(mine, 1):
            if r["source_url"] in done:
                continue
            try:
                tb = fetch(sess, code, r["product_no"])
            except requests.RequestException as e:
                print(f"  {slug} {r['product_no']} 실패 {e.__class__.__name__}")
                time.sleep(a.delay)
                continue
            text, girth = to_text(tb) if tb else ("", [])
            done[r["source_url"]] = {"brand_slug": slug, "product_no": int(r["product_no"]),
                                     "source_url": r["source_url"], "size_text": text, "girth": girth}
            got += bool(text)
            if n % 50 == 0:
                print(f"  {slug} {n}/{len(mine)} · 표 {got}", flush=True)
            time.sleep(a.delay)
        with open(path, "w", encoding="utf-8") as f:
            for d in done.values():
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print(f"{slug}: 코드 {code} · 본 상품 {len(mine)} · 표 있음 {sum(bool(d['size_text']) for d in done.values())}")


if __name__ == "__main__":
    main()
