"""크롤링 원본(products_app_seed.json) → data/products_seed.csv + data/layer_mvp.db.

CSV가 정본이다. AI Fashion 앱이 이 파일을 읽는다.
SQLite는 같은 행을 schema/layer_db_mvp.md 구조에 넣어보는 검증용이라, 반드시 CSV와
같은 데이터로 만든다(따로 만들면 둘이 어긋난다).

할인가는 수집하지 않는다 — 타임세일·쿠폰으로 수시로 바뀌는데 우리는 주 단위로 수집한다.
틀린 판매가를 띄우는 건 표시 문제라, 빈 컬럼조차 두지 않는다(누가 채우고 싶어진다).
crawled_at은 "언제 기준 가격인지"를 화면에 표기하고, 오래된 것부터 다시 긁기 위해 남긴다.
"""
import csv
import json
import os
import sqlite3

SRC = r"C:\Users\user\OneDrive\Documents\카카오톡 받은 파일\products_app_seed (1).json"
OUT_CSV = "data/products_seed.csv"
DB = "data/layer_mvp.db"

CATEGORY_MAP = {"TOP": "tops", "BOTTOM": "bottoms", "OUTER": "outer"}
GENDER_MAP = {"woman": "WOMENSWEAR", "man": "MENSWEAR"}

# 상품명에서 뽑는 닫힌 어휘 (CLAUDE.md §3 원칙: 새 값은 임의 생성 금지).
# 크롤링이 색상·품목을 따로 안 주는 동안의 임시 출처다. 상세페이지 수집이
# 붙으면 그쪽 값이 우선이고 이 파서는 폴백으로 내린다.
COLOR_VOCAB = {
    # 뭉뚱그리지 않는다. mint를 "그린"으로 묶었더니 하늘색 상품에 그린이 붙었다.
    # 상세에서 사실 칩으로 쓰는 값이라, 눈으로 본 색과 다르면 그 자리에서 틀린 게 된다.
    "블랙": ["black", "블랙"],
    "차콜": ["charcoal", "차콜"],
    "화이트": ["off white", "white", "화이트"],
    "아이보리": ["ivory", "아이보리"],
    "크림": ["cream", "크림"],
    "그레이": ["melange grey", "grey", "gray", "그레이", "멜란지"],
    "네이비": ["navy", "네이비"],
    "블루": ["light blue", "blue", "블루"],
    "민트": ["mint", "민트"],
    "그린": ["green", "그린"],
    "올리브": ["olive", "올리브"],
    "베이지": ["beige", "베이지"],
    "샌드": ["sand", "샌드"],
    "카키": ["khaki", "카키"],
    "카멜": ["camel", "카멜"],
    "브라운": ["brown", "브라운"],
    "초코": ["choco", "초코"],
    "옐로우": ["yellow", "옐로우", "lemon"],
    "핑크": ["pink", "핑크"],
    "레드": ["red", "레드"],
    "버건디": ["burgundy", "버건디", "와인"],
    "퍼플": ["purple", "퍼플"],
    "라벤더": ["lavender", "라벤더"],
    # 900건으로 늘리며 실제 상품명에서 확인된 값들
    "라떼": ["latte", "라떼"],
    "피치": ["peach", "피치"],
    "버터": ["butter", "버터"],
    "내추럴": ["natural", "내추럴"],
    "차콜그레이": ["charcoal grey", "charcoal gray"],
    "스카이블루": ["sky blue", "스카이블루"],
    "실버": ["silver", "실버"],
    "골드": ["gold", "골드"],
    "코발트": ["cobalt", "코발트"],
    "머스타드": ["mustard", "머스타드"],
    "터콰이즈": ["turquoise", "터콰이즈"],
    "오트밀": ["oatmeal", "오트밀"],
    "잉크": ["ink blue", "잉크"],
    # 체크·스트라이프는 색이 아니라 패턴이다. 색상 칩에 넣으면 "색상: 체크"가 된다.
    # 패턴 축이 필요해지면 별도 컬럼으로 뽑을 것.
}

ITEM_TYPE_VOCAB = {
    "후드": ["hoodie", "후드"],
    "집업": ["zip-up", "zipup", "zip up", "집업", "half zip", "하프집업"],
    "맨투맨": ["sweatshirt", "맨투맨", "crewneck"],
    "티셔츠": ["t-shirt", "tshirt", "tee", "티셔츠"],
    "셔츠": ["shirt", "blouse", "셔츠", "블라우스"],
    "니트": ["knit", "sweater", "니트", "스웨터", "cardigan", "카디건"],
    "재킷": ["jacket", "자켓", "재킷", "blouson", "블루종"],
    "코트": ["coat", "코트"],
    "패딩": ["padding", "puffer", "패딩", "다운"],
    "데님": ["jeans", "denim", "데님", "청바지"],
    "팬츠": ["pants", "trousers", "팬츠", "슬랙스", "slacks"],
    "스커트": ["skirt", "스커트"],
    "원피스": ["dress", "원피스", "드레스"],
    "베스트": ["vest", "베스트"],
    "바람막이": ["windbreak", "windbreaker", "바람막이", "아노락", "anorak"],
    "숏팬츠": ["shorts", "숏팬츠", "반바지"],
    "점프수트": ["jumpsuit", "점프수트", "overall", "오버올"],
    # 900건으로 늘리며 실제 상품명에서 확인된 값들
    "가디건": ["가디건"],
    "블레이저": ["blazer", "블레이저"],
    "트렌치": ["trench", "트렌치"],
    "점퍼": ["jumper", "점퍼"],
    "탑": ["top", "탑", "sleeveless", "슬리브리스", "민소매"],
    "롱슬리브": ["long sleeve", "롱슬리브", "긴팔"],
    "쇼츠": ["shorts", "쇼츠"],
    "스웨트팬츠": ["sweatpants", "스웨트팬츠"],
    "카고팬츠": ["cargo pants", "카고팬츠"],
}


def _match_vocab(name: str, vocab: dict) -> str:
    """가장 긴 키워드부터 본다 — 'zip up'이 'up'보다, 'sweatshirt'가 'shirt'보다 먼저."""
    low = name.lower()
    best, best_len = "", 0
    for label, keys in vocab.items():
        for k in keys:
            if k in low and len(k) > best_len:
                best, best_len = label, len(k)
    return best

FIELDS = [
    "brand_slug", "category_code", "item_type", "name", "gender_target", "price",
    "representative_color", "season", "status",
    "image_url", "source_url", "crawled_at",
]

with open("data/brands_seed.csv", encoding="utf-8-sig") as f:
    brand_rows = list(csv.DictReader(f))
slug_by_name = {r["name"].strip(): r["slug"] for r in brand_rows}

with open("data/categories_seed.csv", encoding="utf-8-sig") as f:
    cat_rows = list(csv.DictReader(f))

data = json.load(open(SRC, encoding="utf-8"))

rows, unmatched = [], []
seen_images, dropped_junk, dropped_dupe = set(), 0, 0
for d in data:
    slug = slug_by_name.get(d["brand_name"].strip())
    cat = CATEGORY_MAP.get(d["category"])
    if not slug or not cat:
        unmatched.append(d["brand_name"])
        continue

    # 상품이 아닌 행: 룩북·캠페인·사이트 제목이 가격 0~1원으로 딸려온다
    # ("2026 여름 룩북", "인세인개러지 | INSANE GARAGE").
    if int(d["price"]) <= 1000:
        dropped_junk += 1
        continue

    # 같은 사진을 여러 행이 물고 있으면 화면에 같은 옷이 반복된다.
    # 크롤러가 대표컷을 못 찾아 브랜드 공용 이미지를 넣은 경우라, 첫 행만 남긴다.
    img = d["product_image_url"]
    if img in seen_images:
        dropped_dupe += 1
        continue
    seen_images.add(img)
    rows.append({
        "brand_slug": slug,
        "category_code": cat,
        "name": d["product_name"],
        "gender_target": GENDER_MAP.get(d["gender"], "UNISEX"),
        "price": int(d["price"]),
        "item_type": _match_vocab(d["product_name"], ITEM_TYPE_VOCAB),
        "representative_color": _match_vocab(d["product_name"], COLOR_VOCAB),
        "season": "",
        "status": "ON_SALE",
        "image_url": d["product_image_url"],
        "source_url": d["product_url"],
        # 초 단위까지만 — 마이크로초는 쓸 데가 없고 diff만 지저분해진다
        "crawled_at": (d.get("crawled_at") or "")[:19],
    })

with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(rows)
print(f"{len(rows)}/{len(data)}건 → {OUT_CSV}"
      f"  (상품 아님 {dropped_junk} · 사진 중복 {dropped_dupe} 제외)")
if unmatched:
    print("브랜드 매칭 실패:", set(unmatched))

# ---- 같은 행으로 SQLite 검증 적재 ----
if os.path.exists(DB):
    os.remove(DB)
con = sqlite3.connect(DB)
con.executescript("""
CREATE TABLE brands (
  brand_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  slug          TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL,
  name_en       TEXT,
  country       TEXT NOT NULL DEFAULT 'KR',
  gender        TEXT,
  price_tier    TEXT,
  description   TEXT,
  official_site TEXT,
  source        TEXT,
  review_status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW'
);
CREATE TABLE categories (
  category_id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id   INTEGER REFERENCES categories(category_id),
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  depth       INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE products (
  product_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  brand_id       INTEGER NOT NULL REFERENCES brands(brand_id),
  category_id    INTEGER NOT NULL REFERENCES categories(category_id),
  name           TEXT NOT NULL,
  gender_target  TEXT NOT NULL DEFAULT 'UNISEX',
  item_type      TEXT,
  representative_color TEXT,
  price          INTEGER NOT NULL,
  status         TEXT NOT NULL DEFAULT 'ON_SALE',
  image_url      TEXT,
  source_url     TEXT,
  crawled_at     TEXT
);
""")

for r in brand_rows:
    con.execute(
        "INSERT INTO brands(slug,name,name_en,country,gender,price_tier,description,"
        "official_site,source,review_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            r["slug"], r["name"], r["name_en"] or None, r["country"] or "KR",
            r["gender"].upper() or None,
            f"T{r['positioning_tier']}" if r["positioning_tier"] else None,
            r["description"] or None, r["official_url"] or None, r["source"] or None,
            "NEEDS_REVIEW" if r["needs_review"] == "TRUE" else "VERIFIED",
        ),
    )
brand_id = {r["slug"]: i for i, r in enumerate(brand_rows, start=1)}

cat_id = {}
for depth in ("1", "2"):
    for r in cat_rows:
        if r["depth"] != depth:
            continue
        cur = con.execute(
            "INSERT INTO categories(parent_id,code,name,depth) VALUES (?,?,?,?)",
            (cat_id.get(r["parent_code"]), r["code"], r["name"], int(depth)),
        )
        cat_id[r["code"]] = cur.lastrowid

for r in rows:
    con.execute(
        "INSERT INTO products(brand_id,category_id,name,gender_target,item_type,"
        "representative_color,price,status,image_url,source_url,crawled_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            brand_id[r["brand_slug"]], cat_id[r["category_code"]], r["name"],
            r["gender_target"], r["item_type"] or None,
            r["representative_color"] or None,
            r["price"], r["status"],
            r["image_url"], r["source_url"], r["crawled_at"],
        ),
    )
con.commit()

cur = con.cursor()
print(f"SQLite 적재: 브랜드 {cur.execute('SELECT COUNT(*) FROM brands').fetchone()[0]}, "
      f"카테고리 {cur.execute('SELECT COUNT(*) FROM categories').fetchone()[0]}, "
      f"상품 {cur.execute('SELECT COUNT(*) FROM products').fetchone()[0]}")
cols = {r[1] for r in cur.execute("PRAGMA table_info(products)")}
assert "discount_price" not in cols, "할인가 컬럼은 두지 않는다 (신선도를 감당 못 함)"
assert cur.execute("SELECT COUNT(*) FROM products WHERE crawled_at IS NULL OR crawled_at=''").fetchone()[0] == 0, \
    "crawled_at이 비면 신선도 표기를 못 한다"
for label, col in (("색상", "representative_color"), ("품목", "item_type")):
    n = cur.execute(f"SELECT COUNT(*) FROM products WHERE {col} IS NOT NULL").fetchone()[0]
    total = cur.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    print(f"{label} 추출: {n}/{total} ({n/total*100:.0f}%)")
print("수집 시각 범위:",
      cur.execute("SELECT MIN(crawled_at), MAX(crawled_at) FROM products").fetchone())
con.close()
print(f"DB → {DB}")
