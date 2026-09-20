#!/usr/bin/env python3
"""앱이 읽을 모양으로 데이터를 내보낸다 — 얇은 목록 하나 + 브랜드 조각 여럿.

통째로 넣으면 38MB 다(상품 + 태그 + 사이즈, 사진 목록 제외). 지금 앱은 카탈로그를
정적 import 로 읽어(shoppableProducts.ts) 시작할 때 통째로 파싱하므로 그대로는 못 넣는다.
그래서 둘로 가른다.

  products_index.json   목록·검색·필터가 도는 데 필요한 최소 필드만. 37,811벌 9.1MB(gzip 1.6MB).
  brands/<slug>.json    그 매장 상품 전부 + 태그 + 사이즈. 평균 574KB, 제일 큰 곳 1.9MB.
  brands.json           매장 목록(이름·상품 수·대표 사진).

상세 화면은 그 상품의 브랜드 조각만 받으면 된다. 사진은 URL 만 넣는다 — 이미지는 매장
CDN 에서 온다(look_photos.json 33MB 는 앱에 넣지 않는다).

이 모양은 제안이다. 데이터를 받을 구조를 다른 세션에서 짜고 있으므로, 그쪽이 정한 이름과
그릇에 맞춰 바꾼다. 바꿀 자리는 thin() 과 full() 둘뿐이다.

    python scripts/export_app_data.py --out ../layer-web/src/app/data/catalog
    python scripts/export_app_data.py --out /tmp/app --dry     # 크기만 재 본다
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from datetime import datetime, timezone
from collections import Counter, defaultdict
from urllib.parse import urlsplit
from pathlib import Path

import product_desc
import size_from_ocr
import tag_items

WITH_DESC = False
# 그림에서 읽어 둔 상품 글 — (brand_slug, product_no) → detail_from_ocr 이 뽑아 둔 줄.
# 매장 글이 아무 말도 안 할 때 이걸로 메운다(설명이 빈 29,083벌 중 22,214벌이 여기 있다).
MINED: dict[tuple, dict] = {}
# 같은 옷의 다른 색을 한 묶음으로 묶는 번호. 매장은 색마다 상품을 따로 올린다 —
# badblood 「Everyday Scoop Neck Long Sleeve T-Shirt」는 여덟 색이 여덟 상품이다.
# 앱 상세 화면의 「색상 · N」 칩 줄이 이 번호로 형제를 찾는다.
# 열쇠는 size_from_ocr.color_base 를 그대로 쓴다 — 형제에게 사이즈를 물려줄 때 쓰는 것과
# 같은 잣대여야 앱이 보여 주는 형제와 우리가 치수를 물려준 형제가 어긋나지 않는다.
COLOR_GROUP: dict[str, int] = {}

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


# ── 목록 한 줄 ────────────────────────────────────────────────────────────────
# 앞판은 열쇠를 다 글자로 적고 주소·사진주소를 통째로 넣어 11만 벌이 36.3MB(gzip 5.8MB)
# 였다 — 한 벌 328바이트다. 셋을 고쳐 40바이트로 줄였다:
#
#   ① 브랜드·갈래를 번호로. 번호표는 catalog.json 에만 둔다(브랜드가 늘어도 index 는 그대로)
#   ② 사진 주소에서 **매장마다 가장 긴 공통 앞머리**를 뗀다. 115,934장이 100% 접힌다
#      (아낀 글자 4.9MB). 슬러그로는 못 만든다 — 도메인에 슬러그가 든 건 58%뿐이고
#      83곳은 공용 CDN(cafe24img.poxo.com·ecimg.cafe24img.com)이며, 경로가
#      /web/product/ 로 시작하는 것도 62%뿐이다(나머지는 /pushbuttonspace/web/ 처럼
#      매장 계정명이 낀다). 그래서 앞머리를 매장마다 catalog.json 에 적는다.
#   ③ 상세에서만 쓰는 것(주소·갤러리·태그·치수·설명)은 빼고 브랜드 조각으로 미뤘다
#
# 자리 배열로 바꾸면 4% 더 줄지만(gzip 37B/벌) 읽기 어려워지는 값이 더 크다 —
# gzip 이 반복되는 열쇠를 이미 먹는다. 그래서 **짧은 열쇠 객체**로 둔다(양쪽 합의 2026-09-20).
# ── 스펙표에 무엇을 그리나 (사람 결정 2026-09-20) ─────────────────────────────
# 안 그릴 것을 11만 벌치 나르지 않는다.
#   neckline  25%  하이넥·터틀넥 — 사진이 말한다
#   hardware  57%  지퍼·단추·YKK — 옷을 고를 때 안 본다
#   function  28%  신축성·보온성·경량
#
# `finish_wash` 는 **빼지 않고 가른다.** 처음엔 빈도(31%)만 보고 빼자고 했는데, 앱 쪽이
# 짚었다 — 청바지의 「디스트로이드」는 그 옷이 무릎 찢긴 청바지라고 말해 주는 **유일한
# 줄**이다. 잣대는 빈도가 아니라 붙었을 때 할 말이 있느냐다.
#
# 그래서 눈에 보이는 값은 design_element 로 옮기고 공정 이름은 걷는다. 규칙을 양쪽이
# 따로 들면 한쪽이 낡으므로 **여기 한 군데**에만 둔다(이 저장소가 그걸로 세 번 데였다).
# 창고 어휘 35값을 전부 가른다 — 열둘만 보고 고르면 나머지가 조용히 샌다.
DROP_AXES = {"neckline", "hardware", "function"}

# 여러 공정이 같은 결을 낸다 — 사람 눈에는 「워싱」 하나다
WASH_AS_ONE = {"가먼트워싱", "바이오워싱", "노말워싱", "핸드메이드워싱", "피그먼트워싱",
               "솔트워싱", "더티워싱", "폴딩워싱", "샌드워시"}
# 보이는 것 — 디자인 줄로 옮긴다
FINISH_VISIBLE = {"워싱", "블리치", "빈티지가공", "에이징", "데미지", "페이딩", "디스트로이드",
                  "페인팅", "코팅", "크링클", "엠보싱", "주름가공", "기모", "피치기모", "브러시드"}
# 공정 이름 — 옷을 보고 알 수 없고, 알아도 살지 말지가 안 바뀐다.
# 방수·발수·방풍은 기능이라 function 과 같이 뺀다(그쪽을 살리기로 하면 여기서도 살린다).
FINISH_PROCESS = {"후가공", "흡습가공", "텐타가공", "덤블가공", "실켓가공", "수축방지",
                  "링클프리", "가먼트다잉", "방수", "발수", "방풍"}


def fold_finish(t: dict) -> dict:
    """가공 축을 디자인 줄로 접는다 — 보이는 것만, 워싱은 하나로."""
    fin = t.get("finish_wash") or []
    if not fin:
        return {k: v for k, v in t.items() if k not in DROP_AXES}
    moved = []
    for x in fin:
        x = "워싱" if x in WASH_AS_ONE else x
        if x in FINISH_PROCESS:
            continue
        # 모르는 값은 통과시킨다 — 어휘가 자라는 축이라 버리면 새 말이 조용히 사라진다
        if x in FINISH_VISIBLE or x not in FINISH_PROCESS:
            if x not in moved:
                moved.append(x)
    out = {k: v for k, v in t.items() if k not in DROP_AXES and k != "finish_wash"}
    if moved:
        des = list(out.get("design_element") or [])
        out["design_element"] = des + [x for x in moved if x not in des]
    return out


# ── 무드 ────────────────────────────────────────────────────────────────────
#
# 앱 쪽 지적(2026-09-20): 무드를 아는 매장이 195곳 중 49곳뿐이라 「스트릿」을 눌러도
# 카탈로그의 4분의 1만 보인다. 옛 앱 씨앗 64곳을 되재 보니 **한 브랜드 안에서 무드가
# 늘 하나뿐**이었다 — 상품 무드는 처음부터 브랜드 무드였다. 그러니 상품에 붙이지 않고
# 브랜드 줄에 싣는다.
#
# 값은 이미 `brands_seed.csv` 의 `mood_tags` 에 279곳 전부 있다(사람이 손으로 정했다).
# 앱 씨앗 49곳과 견주면 47곳이 그대로 같다 — 저기서 온 값이 맞다.
#
# 다만 창고 어휘가 14낱말이고 앱 어휘가 9낱말이다. 남는 다섯을 그냥 버리면 10곳이
# 무드 없는 채로 남아 **고치려던 구멍이 작아질 뿐 그대로다.** 그래서 접는다.
# 접는 자리는 브랜드가 아니라 **낱말**이다 — 매장 이름을 코드에 적지 않는다.
MOOD_APP = ["스트릿", "미니멀", "빈티지", "캐주얼", "걸리시", "시크", "클래식", "워크웨어", "Y2K"]
# 창고에만 있는 말 → 앱 말. 페미닌·로맨틱은 앱의 아홉 중 여성스러움을 말하는 유일한
# 낱말인 걸리시로 간다. 이 접기가 닿는 곳은 상품표에 있는 195곳 중 **10곳**이다.
MOOD_FOLD = {"페미닌": "걸리시", "로맨틱": "걸리시", "아방가르드": "시크",
             "스포티": "캐주얼", "고프코어": "캐주얼"}


def fold_mood(raw: str) -> list[str]:
    """브랜드 무드를 앱 어휘로 옮긴다 — 모르는 말은 버린다(틀린 값보다 없는 값이 낫다)."""
    out: list[str] = []
    for x in (raw or "").replace("|", ",").split(","):
        x = MOOD_FOLD.get(x.strip(), x.strip())
        if x in MOOD_APP and x not in out:
            out.append(x)
    return out


GENDER_CODE = {"WOMENSWEAR": "W", "MENSWEAR": "M", "UNISEX": "U"}


def img_prefix(urls: list[str]) -> str:
    """그 매장 사진 주소들의 가장 긴 공통 앞머리 — `/` 까지만 자른다."""
    urls = [u for u in urls if u]
    if not urls:
        return ""
    lo, hi = min(urls), max(urls)
    i = 0
    while i < len(lo) and i < len(hi) and lo[i] == hi[i]:
        i += 1
    head = lo[:i]
    return head[:head.rfind("/") + 1] if "/" in head else head


def thin_row(r: dict, tags: dict, bi: dict, ci: dict, pref: dict,
             upref: dict | None = None) -> dict:
    """목록 한 줄. 열쇠 뜻은 catalog.json 의 "fields" 에 적어 둔다."""
    t = (tags.get(r["source_url"]) or {}).get("tags") or {}
    u = r.get("image_url") or ""
    pre = pref.get(r["brand_slug"], "")
    upre = (upref or {}).get(r["brand_slug"], "")
    row = {
        "i": f'{r["brand_slug"]}-{r["product_no"]}',
        "b": bi[r["brand_slug"]],
        "n": r["name"],
        "p": int(r["price"] or 0),
        "m": u[len(pre):] if pre and u.startswith(pre) else u,
        "c": ci[r.get("category_code") or "other"],
        "t": r.get("subtype") or "",
        "g": GENDER_CODE.get(r.get("gender_target"), "U"),
        "s": 1 if r.get("status") == "ON_SALE" else 0,
    }
    # 상품 주소 — 「공식몰 보기」가 이것 없이는 안 걸린다(앱 쪽 요청 2026-09-20).
    # 카페24 표준 주소(?product_no=N)로 퉁치려 했으나 표본 8곳 중 2곳이 404 였다.
    # 깨진 링크는 없는 링크보다 나쁘므로 **진짜 주소**를 싣되, 사진과 똑같이 매장마다
    # 공통 앞머리를 떼서 catalog.brands[].up 에 한 번만 적는다.
    # 실측: 꼬리 평균 35자 · 목록에 평문 +4.28MB · gzip +1.08MB.
    su = r.get("source_url") or ""
    if su:
        row["u"] = su[len(upre):] if upre and su.startswith(upre) else su
    # 빈 값은 아예 안 적는다 — 11만 번 반복되면 그것만으로 수백 KB다
    for k, v in (("co", (t.get("color") or [""])[0]),
                 ("ma", (t.get("material") or [""])[0]),
                 ("se", r.get("season") or ""),
                 ("cg", COLOR_GROUP.get(r["source_url"], 0))):
        if v:
            row[k] = v
    return row


def full(r: dict, tags: dict, sizes: dict, crawl: dict) -> dict:
    """상세 한 벌 — 얇은 목록에 없는 것 전부."""
    d = crawl.get(r["source_url"]) or {}
    out = {"id": f'{r["brand_slug"]}-{r["product_no"]}'}
    out.update({
        "tags": fold_finish((tags.get(r["source_url"]) or {}).get("tags") or {}),
        "size": sizes.get(r["source_url"]),
        "gallery": d.get("gallery") or [],
        # 설명문을 내보낸다(2026-09-20 사람 결정: 「지금 당장 메울 곳은 메워, 배송이나
        # 세탁/후기 문의 같은 찌꺼기들은 싹 지우고」). 2026-09-07 에 안 내보내기로 한 까닭이
        # 사라졌다 — 그때는 씻는 자리가 없어 남의 말이 상품 설명 자리에 떴다:
        #   siyazu 783벌  손님 후기 통째로 —「커서 스몰사이즈로 교환했는데도 크네요」
        #   vunque·kirsh·blayer·andersson-bell  후기 신고 안내문
        #   divein   「Q & A Write View all 상품명 … 판매가 109,000원」
        #   dnsr     「RELATED ITEMS 원턱 버뮤다 데님 팬츠 블루 KRW 74,000 …」
        #   badblood 「Delivery / Returns * Estimated delivery dates …」
        # 이제 product_desc 가 그것들을 걷어내고, 매장 글이 아무 말도 안 하면 그림에서
        # 읽어 둔 글로 메운다. --with-desc 는 씻기 전 원문까지 보고 싶을 때만 쓴다.
        # 설명은 브랜드 조각에 안 넣는다 — descs/ 로 따로 나간다(아래 write_descs).
        # 상세 화면은 브랜드 조각만 받고, 설명을 펼칠 때 그 조각을 받는다(사람 결정
        # 2026-09-20). 설명을 같이 넣으면 브랜드 조각이 평균 1.2MB · 최대 5.9MB 가 된다.
        **({"desc_raw": (d.get("description") or "")[:4000]} if WITH_DESC else {}),
        "options": r.get("options") or "",
        "color_name": r.get("representative_color") or "",
        "price_log": d.get("price_log") or [],
        "stock_log": d.get("stock_log") or [],
        "price_seen_at": d.get("price_seen_at") or "",
    })
    return out


# 미리 압축하지 않는다 — **평문 JSON 으로 올린다.**
#
# 처음엔 `.json.gz` 로 올리고 Content-Encoding: gzip 을 붙이려 했다. 슈퍼베이스가 올린
# 그대로 내보낸다고 들어서다. 실제로 올려 되받아 보니 둘 다 틀렸다:
#
#   · 슈퍼베이스는 그 머리말을 **안 붙여 준다** (content-encoding 이 아예 없다)
#   · 그런데 앞에 **클라우드플레어**가 있고, 그쪽이 알아서 압축해 준다
#         --compressed 로 부르면 content-encoding: br 로 온다
#
# 그래서 이미 gzip 인 것을 brotli 로 한 번 더 감싸는 꼴이 됐다 — 728,949 → 728,959 바이트,
# 열 바이트가 **늘었다**. 두 번 압축하면 아무것도 안 줄어든다.
#
# 평문으로 올리면 클라우드플레어가 brotli 로 줄이는데 그게 gzip 보다 낫고, 머리말 문제도
# 사라지고, 앱은 그냥 `.json` 을 받으면 된다.
#
# 여기서 세는 크기는 **평문 크기**다. 실제로 오가는 값은 그보다 작다(brotli 가 줄인다).
GZ = False


def write(path: Path, obj, dry: bool) -> int:
    """쓴 바이트 수를 돌려준다 — **압축한 뒤**의 크기다(그게 실제로 오가는 값이다)."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    b = gzip.compress(raw, 6) if GZ else raw
    if not dry:
        out = path.with_suffix(path.suffix + ".gz") if GZ else path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b)
    return len(b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry", action="store_true", help="쓰지 않고 크기만 잰다")
    ap.add_argument("--with-desc", action="store_true",
                    help="매장 설명문도 내보낸다(31MB 늘어난다. full() 의 주석을 먼저 읽어라)")
    args = ap.parse_args()
    global WITH_DESC
    WITH_DESC = args.with_desc
    out = Path(args.out)

    rows = list(csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig")))
    tags = json.loads((DATA / "product_tags_full.json").read_text(encoding="utf-8"))
    sizes = json.loads((DATA / "product_sizes.json").read_text(encoding="utf-8"))
    crawl = {}
    for p in sorted((DATA / "crawl").glob("*.jsonl")):
        if p.name.startswith("_"):
            continue
        for ln in p.open(encoding="utf-8"):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if d.get("source_url"):
                crawl[d["source_url"]] = d

    for p in sorted((DATA / "crawl" / "detail").glob("*.jsonl")):
        for ln in p.open(encoding="utf-8"):
            try:
                m = json.loads(ln)
            except Exception:
                continue
            MINED[(m.get("brand_slug") or p.stem, str(m.get("product_no")))] = m
    print(f"그림에서 읽어 둔 상품 글 {len(MINED):,}벌")

    # 색만 다른 형제 묶기 — 둘 이상 모인 묶음에만 번호를 준다(단독은 0)
    fam: dict[tuple, list[str]] = defaultdict(list)
    for r in rows:
        base = size_from_ocr.color_base(r["name"])
        if base:
            fam[(r["brand_slug"], base)].append(r["source_url"])
    gid = 0
    for k, urls in sorted(fam.items()):
        if len(urls) < 2:
            continue
        gid += 1
        for u in urls:
            COLOR_GROUP[u] = gid
    print(f"색만 다른 형제 묶음 {gid}개 · 묶인 상품 {len(COLOR_GROUP)}벌")

    # 매장마다 사진 주소 앞머리 — 줄에서 떼어 내고 catalog.json 에 한 번만 적는다
    urls_of: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.get("image_url"):
            urls_of[r["brand_slug"]].append(r["image_url"])
    slugs = sorted({r["brand_slug"] for r in rows})
    bi = {s: i for i, s in enumerate(slugs)}
    pref = {s: img_prefix(urls_of.get(s, [])) for s in slugs}
    codes = sorted({(r.get("category_code") or "other") for r in rows})
    ci = {c: i for i, c in enumerate(codes)}
    # 갈래 코드 → 화면 이름. 창고 줄에서 그대로 모은다(따로 적어 두면 낡는다).
    cat_label, grp_label = {}, {}
    for r in rows:
        c = r.get("category_code") or "other"
        cat_label.setdefault(c, r.get("category") or "")
        grp_label.setdefault(c, r.get("group") or "")
    brand_name, brand_mood = {}, {}
    with (DATA / "brands_seed.csv").open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            brand_name[r["slug"]] = r.get("name") or r["slug"]
            brand_mood[r["slug"]] = fold_mood(r.get("mood_tags") or "")

    # 상품 주소도 매장마다 앞머리가 같다 — 사진과 같은 방식으로 접는다.
    # `by` 는 아래에서 만들어지므로 여기서는 rows 에서 바로 모은다(안 그러면 NameError).
    _u_of: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        _u_of[r["brand_slug"]].append(r.get("source_url") or "")
    upref = {slug: img_prefix(us) for slug, us in _u_of.items()}

    shard: dict[str, list] = defaultdict(list)
    for r in rows:
        shard[r.get("category_code") or "other"].append(
            thin_row(r, tags, bi, ci, pref, upref))
    files: dict[str, dict] = {}
    idx_gz = 0
    for code, items in sorted(shard.items()):
        n = write(out / "index" / f"{code}.json", items, args.dry)
        # 열쇠 이름은 **압축 전** 크기라는 뜻이다. 앞서 `gzip` 이라 적고 평문 값을 넣어
        # 앱이 다섯 배 잘못 판단할 뻔했다(앱 쪽 지적 2026-09-20: tops 가 gzip 11.08MB 로
        # 적혀 있는데 실제 전송량은 2.23MB). 오가는 값은 클라우드플레어가 정하므로 우리가
        # 알 수 없다 — 아는 것(평문)만 그 이름으로 적는다.
        files[f"index/{code}.json"] = {"n": len(items), "bytes": n}
        idx_gz += n
    # ── 검색 조각 — 11만 벌 **이름 전부**를 한 파일에 ──────────────────────────────
    # 갈래로 쪼개 두면 상의만 열어 본 사람이 검색을 누를 때 나머지를 그 자리에서 받아야
    # 한다(4.7MB). 이름·번호만 담으면 훨씬 작다 — 사진 주소가 목록 무게의 절반이다.
    # 자리로 적는다. 여기서는 사람이 읽을 일이 없고, 11만 번 반복되는 열쇠가 그대로 무게다.
    #   [브랜드번호, 상품번호, 이름, 갈래번호, 성별(0 여 · 1 남 · 2 공용), 판매중(1/0)]
    search = [[t["b"], int(t["i"].rsplit("-", 1)[1]), t["n"], t["c"],
               "WMU".index(t["g"]), t["s"]]
              for items in shard.values() for t in items]
    sn = write(out / "search.json", search, args.dry)
    files["search.json"] = {"n": len(search), "bytes": sn}
    print(f"검색 조각 {len(search):,}줄 · 평문 {sn/1048576:.2f} MB (이름만)")

    print(f"목록 {len(rows):,}벌 · 갈래 {len(shard)}개 · 평문 {idx_gz/1048576:.1f} MB "
          f"(오갈 때는 brotli 로 줄어든다) — "
          + " · ".join(f"{k} {len(v):,}" for k, v in sorted(shard.items(), key=lambda x: -len(x[1]))[:4]))

    by = defaultdict(list)
    for r in rows:
        by[r["brand_slug"]].append(r)
    # 상세 화면은 **한 벌**을 보여 주는데 조각이 매장 통째였다 — 제일 큰 곳이 3,548KB 다.
    # 설명을 뺀 뒤에도 무거우므로 같은 잣대로 쪼갠다(2026-09-20). 앱은 `product_no % bp`
    # 로 어느 조각인지 바로 안다(bp 는 brands.json 에 적는다. 1 이면 안 쪼갠 것이다).
    SHARD_BYTES = 500_000
    tot = 0
    big = []
    shards_of: dict[str, int] = {}
    for slug, items in sorted(by.items()):
        made = [full(r, tags, sizes, crawl) for r in items]
        raw = len(json.dumps(made, ensure_ascii=False, separators=(",", ":")).encode())
        bp = max(1, -(-raw // SHARD_BYTES))
        shards_of[slug] = bp
        if bp == 1:
            size = write(out / "brands" / f"{slug}.json", made, args.dry)
        else:
            buckets: list[list] = [[] for _ in range(bp)]
            for r, m in zip(items, made):
                buckets[int(r["product_no"]) % bp].append(m)
            size = sum(write(out / "brands" / f"{slug}.{i}.json", b, args.dry)
                       for i, b in enumerate(buckets))
        tot += size
        big.append((size // bp, slug, len(items), bp))
    big.sort(reverse=True)
    cut = [s for s, n in shards_of.items() if n > 1]
    print(f"브랜드 조각 {len(by)}곳 · 합계 {tot/1048576:.1f} MB · "
          f"쪼갠 매장 {len(cut)}곳 · 조각 하나 평균 {tot/sum(shards_of.values())/1024:.0f} KB")
    for s, slug, k, bp in big[:3]:
        print(f"   제일 큰 조각: {slug} {k}벌 ×{bp} → 조각당 {s/1024:.0f} KB")

    # ── 설명 — 브랜드 조각과 따로, 펼칠 때만 받는다 ─────────────────────────────
    # 브랜드 하나를 한 덩이로 두면 설명 하나 펼치자고 그 매장 설명 전부가 따라온다.
    # 전수로 재니 매장당 중앙 158KB 인데 꼬리가 길다 — years-ago 3,296KB ·
    # facade-pattern 1,984 · dunst 1,770 · 1MB 넘는 곳이 열 곳이다(2026-09-20).
    # 그래서 큰 곳은 쪼갠다. 앱은 `product_no % dp` 로 어느 조각인지 바로 안다
    # (dp 는 brands.json 에 적는다. 1 이면 안 쪼갠 것이다).
    DESC_PART_BYTES = 500_000
    desc_of: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        d = crawl.get(r["source_url"]) or {}
        txt, src = product_desc.best(d.get("description") or "",
                                     MINED.get((r["brand_slug"], str(r["product_no"]))))
        if txt:
            desc_of[r["brand_slug"]][f'{r["brand_slug"]}-{r["product_no"]}'] = {"t": txt, "s": src}
    parts_of: dict[str, int] = {}
    desc_bytes = 0
    for slug, m in sorted(desc_of.items()):
        raw = len(json.dumps(m, ensure_ascii=False, separators=(",", ":")).encode())
        dp = max(1, -(-raw // DESC_PART_BYTES))
        parts_of[slug] = dp
        if dp == 1:
            desc_bytes += write(out / "descs" / f"{slug}.json", m, args.dry)
            continue
        buckets: list[dict] = [{} for _ in range(dp)]
        for k, v in m.items():
            buckets[int(k.rsplit("-", 1)[1]) % dp][k] = v
        for i, b in enumerate(buckets):
            desc_bytes += write(out / "descs" / f"{slug}.{i}.json", b, args.dry)
    split = [s for s, n in parts_of.items() if n > 1]
    print(f"설명 조각 {len(desc_of)}곳 · 합계 {desc_bytes/1048576:.1f} MB · "
          f"쪼갠 매장 {len(split)}곳 ({', '.join(f'{s}×{parts_of[s]}' for s in sorted(split))})")

    # ── catalog.json — 앱이 제일 먼저 읽는 도장 ────────────────────────────────
    # 하루 한 번 데이터가 바뀌는데 앱이 「새 게 나왔는지」를 알 길이 없었다. 받아 둔 걸
    # 계속 쓰거나 매번 4MB 를 다시 받거나 둘 중 하나가 된다. `v` 한 줄이 그걸 푼다 —
    # 앱은 이것만 보고 바뀐 조각만 다시 받는다(앱 쪽 요청 2026-09-20).
    #
    # 번호표(브랜드·갈래)는 **여기에만** 둔다. index 파일마다 적으면 브랜드가 늘 때마다
    # 모든 index 를 다시 써야 한다.
    #
    # 사진 주소는 imgBase 한 줄로 못 만든다 — 실측: 도메인에 매장 슬러그가 든 것 58%,
    # 83곳이 공용 CDN(cafe24img.poxo.com·ecimg.cafe24img.com), 경로가 /web/product/ 로
    # 시작하는 것 62%(나머지는 /pushbuttonspace/web/ 처럼 매장 계정명이 낀다).
    # 그래서 **매장마다** 앞머리(p)를 적는다. 그러면 115,934장이 100% 접힌다.
    #   사진 주소 = brands[b].p + 줄의 m
    for slug, bp in shards_of.items():
        files[f"brands/{slug}.json" if bp == 1 else f"brands/{slug}.<0..{bp-1}>.json"] = {
            "n": len(by[slug]), "parts": bp}
    for slug, dp in parts_of.items():
        files[f"descs/{slug}.json" if dp == 1 else f"descs/{slug}.<0..{dp-1}>.json"] = {
            "n": len(desc_of[slug]), "parts": dp}
    catalog = {
        "v": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "count": len(rows),
        # 줄의 열쇠가 무슨 뜻인가 — 앱이 여기서 읽게 해 두면 열쇠가 늘어도 안 어긋난다
        "fields": {
            "i": "상품 id (<slug>-<product_no>)", "b": "brands 번호", "n": "이름",
            "p": "값(원)", "m": "사진 — brands[b].p 를 앞에 붙인다",
            "c": "cats 번호", "t": "품목(subtype)", "g": "W 여성 · M 남성 · U 남녀공용",
            "s": "1 판매중 · 0 품절", "co": "대표색", "ma": "대표소재", "se": "시즌",
            "cg": "색만 다른 형제 묶음(없으면 안 적힘)",
            "u": "상품 주소 — brands[b].up 을 앞에 붙인다",
        },
        # 무드는 **브랜드에만** 있다 — 상품마다 붙일 것이 아니다(아래 fold_mood 참고)
        "brand_fields": {"mo": "무드 — moods 안의 말만 쓴다",
                         "p": "사진 주소 앞머리", "up": "상품 주소 앞머리"},
        "brands": [{"i": bi[s], "s": s, "n": brand_name.get(s, s), "p": pref.get(s, ""),
                    "c": len(by.get(s, [])), "bp": shards_of.get(s, 1), "dp": parts_of.get(s, 0),
                    "im": next((r.get("image_url") for r in by.get(s, []) if r.get("image_url")), ""),
                    **({"up": upref[s]} if upref.get(s) else {}),
                    **({"mo": brand_mood[s]} if brand_mood.get(s) else {})}
                   for s in slugs],
        "cats": [{"i": ci[c], "c": c, "n": cat_label.get(c, ""), "g": grp_label.get(c, "")}
                 for c in codes],
        "moods": MOOD_APP,
        "files": files,
    }
    b = write(out / "catalog.json", catalog, args.dry)
    print(f"도장 catalog.json — 매장 {len(slugs)}곳 · 갈래 {len(codes)}개 · "
          f"파일 {len(files)}개 · {b/1024:.0f} KB · v={catalog['v']}")
    if args.dry:
        print("(--dry 라 아무것도 쓰지 않았다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
