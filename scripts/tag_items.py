"""텍스트 → 12축 아이템 태그. data/vocab_aliases.json 하나만 읽는 규칙 기반 매칭.

무엇을 읽나: data/products_full.csv(상품 우주 — 크롤러가 걸러 낸 27k건)에 crawl/<slug>.jsonl 의
description·spec 과 crawl/ocr/<slug>.jsonl 의 ocr_text 를 붙인다. 이미지밖에 없던 매장은 OCR 글이
곧 설명이다. 결정적(LLM 없음)이라 OCR 이 더 들어오면 그냥 다시 돌린다.

결과: data/product_tags_full.json — source_url → {brand_slug, category, source_quality, tags, text_sources}.
layer-web scripts/build-discover-products.mjs 가 읽는 product_tags_seed.json 과 같은 모양.

규칙(vocab_aliases.json _context_rules 를 코드로 옮긴 것):
  · 긴 표현 우선(longest-first) + 매칭한 구간 마스킹 — '카고포켓'이 '포켓'을, '세미크롭'이 '크롭'을 먹지 않게.
  · 한글 1~2글자 별칭('면','울','마','진','리브','다운')은 왼쪽이 한글이 아닐 때만(1글자는 오른쪽도).
    '측면'·'서울'·'올리브'·'버튼다운'이 소재로 잡히는 것을 막는다. 영문은 단어 경계(\b).
  · _text_blocklist(시어링, 레이어드 스타일링…)는 본문 매칭 전에 마스킹.
  · color 는 본문이 아니라 name + representative_color + spec 의 색상 값에서만. _color_blocklist 먼저 마스킹.
  · n부: '소매'가 따르면 sleeve_length.칠부소매, 팬츠면 length.버뮤다(버뮤다·카프리 명시어가 있으면 그 값).
  · '여유로운 실루엣': 상의 루즈핏, 하의 루즈핏+와이드.
  · 혼방: 소재가 하나만 잡혔는데 '혼방'이 있으면 폴리에스터를 더한다.
  · 단색: 매칭 아닌 추론 — source_quality ok 이고 pattern 이 비면 단색.
  · _bottoms_only(세미와이드·로우라이즈·하이웨이스트)는 하의·미분류에만.
  · Skirts 는 length 축 없음. pants_type 은 Pants·Denim(·미분류)만. 가방·신발·액세서리는 옷 축(넥라인·소매·핏·기장) 없음.

사용:
    py scripts/tag_items.py                 # 전부
    py scripts/tag_items.py --brands kirsh dunst --report
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CRAWL = DATA / "crawl"
OCR = CRAWL / "ocr"
BROWSER = CRAWL / "browser"
VOCAB = DATA / "vocab_aliases.json"
OUT = DATA / "product_tags_full.json"

AXES = ["neckline", "sleeve_length", "silhouette", "length", "pants_type", "material",
        "finish_wash", "design_element", "construction", "pattern", "hardware", "function", "color"]
GARMENT_AXES = {"neckline", "sleeve_length", "silhouette", "length", "pants_type"}
NON_GARMENT = {"Accessories", "Bags", "Shoes"}
PANTS = {"Pants", "Denim", ""}
BOTTOMS = {"Pants", "Denim", "Skirts"}
SHORT_TEXT = 80
# spec 표에서 태깅에 쓸 만한 키만 — 사이즈 실측(chest/hem)·배송 표(ems/ups)는 뺀다
SPEC_KEY = re.compile(r"소재|material|fabric|composition|혼용|원단|색상|color|colour|세탁|care|간략설명|디테일|detail|핏|fit|상품명|설명", re.I)
COLOR_KEY = re.compile(r"색상|colou?r", re.I)
HANGUL = "가-힣"
CSS_RX = re.compile(r"\{[^{}]{0,80}:[^{}]{0,80}\}|border-(?:bottom|top|left|right)|\d+px solid")
NBU_RX = re.compile(r"([5-8])\s*부\s*(소매|기장|팬츠|바지)?")
LOOSE_RX = re.compile(r"여유\s?(?:로운|있는|로이)\s?(?:실루엣|핏|fit)", re.I)


def _is_hangul(s: str) -> bool:
    return bool(s) and all("가" <= c <= "힣" for c in s)


def compile_alias(alias: str) -> re.Pattern:
    a = re.escape(alias.lower())
    if _is_hangul(alias) and len(alias) == 1:
        return re.compile(rf"(?<![{HANGUL}]){a}(?![{HANGUL}])")
    if _is_hangul(alias) and len(alias) == 2:
        return re.compile(rf"(?<![{HANGUL}]){a}")
    if re.fullmatch(r"[a-z0-9 /\-]+", alias.lower()):
        # 복수형 s 를 받는다 — 「Archive Long Sleeves」가 「long sleeve」에 안 걸려
        # 상품명에 sleeve 723 · long 616 이 미등록으로 남아 있었다(2026-09-05).
        return re.compile(rf"(?<![a-z0-9]){a}s?(?![a-z0-9])")
    return re.compile(a)




# 상세 설명 뒤(또는 사이)에 매장이 「함께 보는 상품」을 이름+가격으로 붙인다. 그 이름이
# 이 옷의 태그가 됐다 — badblood MA-1 자켓이 옆의 「솔리드 복서 - 블랙/스트라이프」 때문에
# 단색이면서 스트라이프가 되고, dunst 블레이저가 옆의 「H-LINE MAXI SKIRT」 때문에 맥시가
# 됐다. 상품 12,493벌(33%)이 이런 글을 달고 있다(2026-09-05).
#
# 「가격이 처음 나오는 데서 자른다」는 못 쓴다 — 자기 가격 블록이 글 한가운데(중앙값 39%
# 지점)에 있어 진짜 설명 25,740건이 함께 잘린다. 그래서 자르지 않고 **가격과 그 앞의
# 이름만 도려낸다**. 앞으로 예순 자까지 되짚되 문장 경계(마침표·「다 」·「요 」·줄바꿈·
# 가운뎃점)에서 멈추므로 진짜 문장은 남는다(네 매장 표본에서 한 글자도 안 잘렸다).
_PRICE_RUN = re.compile(
    r"(?:(?:krw|won)\s*\d{1,3},\d{3}|\d{1,3},\d{3}\s*(?:krw|won|원))"
    r"(?:\s*(?:\(\s*\d+%\s*\))?\s*(?:krw|won)?\s*\d{1,3},\d{3}\s*(?:krw|won|원)?)*", re.I)
_SENT_END = re.compile(r"[.。!?\n|·•▪]|다\s|요\s")


def strip_other_products(text: str, back: int = 60) -> str:
    if not text or not _PRICE_RUN.search(text):
        return text
    out, last = [], 0
    for m in _PRICE_RUN.finditer(text):
        win_from = max(last, m.start() - back)
        win = text[win_from:m.start()]
        k = 0
        for mm in _SENT_END.finditer(win):
            k = mm.end()
        cut = win_from + k
        if cut >= last:
            out.append(text[last:cut])
        last = m.end()
    out.append(text[last:])
    return "".join(out)


class Tagger:
    def __init__(self, vocab: dict):
        self.vocab = vocab
        self.mine_only = set(vocab.get("_mine_alias_only", []))
        self.blocklist = [b.lower() for b in vocab.get("_color_blocklist", [])]
        self.text_blocklist = [b.lower() for b in vocab.get("_text_blocklist", [])]
        self.bottoms_only = [tuple(x.split(".", 1)) for x in vocab.get("_bottoms_only", [])]
        # 축 전체를 한 목록으로 — 길이 내림차순으로 매칭하고 매칭 구간을 마스킹한다
        text_rules, color_rules = [], []
        # 색은 수집기 어휘 하나만 쓴다. 예전에는 여기 25색을 따로 적어 두었는데, 그것은
        # 사실 「거친 이름」이라 차콜 2,037벌이 그레이로, 크림 753벌이 아이보리로 뭉개졌다
        # (색상 칸이 찬 36,964벌 중 6,101벌, 2026-09-05). 이제 값은 수집기의 63색을 쓰고,
        # 예전 25색은 _color_rollup(세밀→거친)으로 남겨 앱이 두 층을 다 쓸 수 있게 한다.
        vocab = dict(vocab)
        try:
            import crawl_cafe24 as _cc
            vocab["color"] = {k: list(vs) for k, vs in _cc.COLOR_VOCAB.items()}
        except Exception:
            pass
        for ax in AXES:
            for value, aliases in vocab[ax].items():
                names = list(aliases) if value in self.mine_only else [value] + list(aliases)
                for a in names:
                    (color_rules if ax == "color" else text_rules).append((len(a), ax, value, compile_alias(a)))
        self.text_rules = sorted(text_rules, key=lambda r: -r[0])
        self.color_rules = sorted(color_rules, key=lambda r: -r[0])

    @staticmethod
    def _scan(rules, text: str) -> dict[str, set]:
        """긴 낱말이 짧은 낱말을 먹는다 — 단, 같은 축 안에서만.

        예전에는 축을 가리지 않고 먹었다. 그래서 「카고 팬츠」에 pants_type 어휘를 더하자
        construction 의 「카고포켓」 409벌이 사라졌다(2026-09-05). 한 낱말이 두 가지를
        동시에 말하는 일은 흔하다 — 카고는 바지 종류이면서 주머니고, 밴딩은 바지 종류이면서
        허리 만듦새다(사람 지적). 축마다 따로 가려야 둘 다 남는다.
        「가먼트 워싱」이 「워싱」을 먹는 것은 같은 축 안이라 그대로 지켜진다.
        """
        text = text.lower()
        hits: dict[str, set] = defaultdict(set)
        by_ax: dict[str, list] = defaultdict(list)
        for r in rules:
            by_ax[r[1]].append(r)
        for ax, rs in by_ax.items():
            masked = list(text)
            cur = text
            for _, _, value, rx in rs:
                found = False
                for m in rx.finditer(cur):
                    found = True
                    for i in range(m.start(), m.end()):
                        masked[i] = "\x00"
                if found:
                    hits[ax].add(value)
                    cur = "".join(masked)
        return hits

    def tag(self, category: str, name: str, body: str, color_text: str, quality: str) -> dict[str, list]:
        text = f"{name}\n{strip_other_products(body)}".lower()
        for b in self.text_blocklist:  # '시어링'→시어, '레이어드 스타일링'→레이어드 같은 오탐을 먼저 지운다
            text = text.replace(b, " " * len(b))
        hits = self._scan(self.text_rules, text)

        # n부 — 소매면 칠부소매, 팬츠면 버뮤다(명시어 우선)
        for m in NBU_RX.finditer(body):
            tail = body[m.end():m.end() + 6]
            if m.group(2) == "소매" or tail.startswith("소매"):
                hits["sleeve_length"].add("칠부소매")
            elif category in PANTS:
                window = body[max(0, m.start() - 40):m.end() + 40]
                if "카프리" in window or "capri" in window.lower():
                    hits["length"].add("카프리")
                else:
                    hits["length"].add("버뮤다")
        if LOOSE_RX.search(body):
            hits["silhouette"].add("루즈핏")
            if category in BOTTOMS:
                hits["silhouette"].add("와이드")
        # 「혼방」이라는 낱말만 보고 폴리에스터를 넣던 규칙을 뺀다(2026-09-05).
        # 「부드러운 리오셀 혼방 소재」(badblood)에 폴리에스터가 붙었다 — 무엇과 섞였는지
        # 페이지는 말하지 않는데 특정 섬유를 단정한 것이다. 324벌이 그 꼴이었다.
        # 소재는 사람이 옷을 고르는 근거라, 빠진 것보다 틀린 것이 나쁘다.

        # color — 본문이 아니라 이름·대표색·스펙 색상에서만
        ct = color_text.lower()
        for b in self.blocklist:
            ct = ct.replace(b, " " * len(b))
        for ax, vals in self._scan(self.color_rules, ct).items():
            hits[ax] |= vals

        # 축 게이트
        if category == "Skirts":
            hits.pop("length", None)
        if category not in PANTS:
            hits.pop("pants_type", None)
        if category in NON_GARMENT:
            for ax in GARMENT_AXES:
                hits.pop(ax, None)
        if category not in BOTTOMS | {""}:
            for ax, val in self.bottoms_only:  # 하의 전용 값이 코디 문장으로 상의에 붙는 것 방지
                hits.get(ax, set()).discard(val)
        if quality == "ok" and not hits.get("pattern"):
            hits["pattern"].add("단색")
        return {ax: sorted(hits[ax]) for ax in AXES if hits.get(ax)}


def load_latest(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        latest[str(d["product_no"])] = d
    return latest


def spec_texts(spec) -> tuple[str, str]:
    """(태깅용 본문, 색상 텍스트)"""
    if not isinstance(spec, dict):
        return "", ""
    body, color = [], []
    for k, v in spec.items():
        if not isinstance(v, str) or len(v) > 400:
            continue
        if COLOR_KEY.search(k):
            color.append(v)
        if SPEC_KEY.search(k):
            body.append(f"{k}: {v}")
    return "\n".join(body), " ".join(color)


def quality_of(body: str) -> str:
    letters = re.sub(rf"[^{HANGUL}A-Za-z]", "", body)
    if CSS_RX.search(body):
        return "css_fragment"
    if len(letters) < SHORT_TEXT:
        return "too_short"
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="*")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    tagger = Tagger(json.loads(VOCAB.read_text(encoding="utf-8")))
    rows = list(csv.DictReader(open(DATA / "products_full.csv", encoding="utf-8-sig")))
    if args.brands:
        rows = [r for r in rows if r["brand_slug"] in set(args.brands)]
    by_brand: dict[str, list] = defaultdict(list)
    for r in rows:
        by_brand[r["brand_slug"]].append(r)

    out: dict[str, dict] = {}
    q_count, src_count = Counter(), Counter()
    ax_count, cat_count = Counter(), Counter()
    for slug, items in sorted(by_brand.items()):
        crawl = load_latest(CRAWL / f"{slug}.jsonl")
        ocr = load_latest(OCR / f"{slug}.jsonl")
        # 브라우저로 거둔 설명글 — 탭 안에 있어 requests 로는 빈 껍데기만 오는 매장이 있다.
        # perenn 은 설명글이 8자였는데 탭을 누르고 내려서 받으니 40벌이 치수·소재까지 들어
        # 있었다(2026-09-04). 주소로 맞춘다 — product_no 는 브라우저 기록에 없을 수 있다.
        brw: dict[str, str] = {}
        bp = BROWSER / f"{slug}.jsonl"
        if bp.exists():
            for l in bp.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    b = json.loads(l)
                    t = (b.get("description") or "").strip()
                    if b.get("source_url") and len(t) > len(brw.get(b["source_url"], "")):
                        brw[b["source_url"]] = t
        for r in items:
            d = crawl.get(str(r["product_no"]), {})
            o = ocr.get(str(r["product_no"]), {})
            desc = d.get("description") or ""
            dt = d.get("detail_text") or ""
            if dt and dt[:200] != desc[:200]:
                desc = desc + "\n" + dt   # JSON-LD 요약과 본문 글이 다르면 둘 다 읽는다(2026-09-03)
            sbody, scolor = spec_texts(d.get("spec"))
            otext = o.get("ocr_text") or ""
            btext = brw.get(r["source_url"], "")
            if btext and btext[:200] != desc[:200]:
                pass          # 브라우저 글은 따로 붙인다 — 원래 글과 겹치면 아래에서 무시된다
            else:
                btext = ""
            body = "\n".join(t for t in (desc, sbody, otext, btext) if t)
            sources = [s for s, t in (("json-ld" if d.get("description_source") == "json-ld" else "html", desc), ("spec", sbody), ("ocr", otext), ("browser", btext)) if t]
            quality = quality_of(body)
            # 상품 이름은 색을 고르는 데 쓰지 않는다. 수집기의 pick_color 가 이미 이름을
            # 읽되 「끝 괄호 → 이름 → 설명 → 스펙」 순서를 지켜 representative_color 를
            # 정해 두었다. 이름을 다시 통째로 훑으면 그 순서가 무너진다 — 수집기 어휘로
            # 갈아탄 뒤 kirsh 「CHERRY」 상품이 전부 레드가 되어 75 → 602 로 뛰었다
            # (체리는 이 브랜드의 마스코트지 옷 색이 아니다, 2026-09-05).
            color_text = " ".join(t for t in (r.get("representative_color", ""), scolor) if t)
            tags = tagger.tag(r["category"], r["name"], body, color_text, quality)
            out[r["source_url"]] = {
                "brand_slug": slug, "category": r["category"], "source_quality": quality,
                "text_sources": sources, "tags": tags,
            }
            q_count[quality] += 1
            for s in sources:
                src_count[s] += 1
            cat_count[r["category"] or "(없음)"] += 1
            for ax in tags:
                ax_count[ax] += 1

    # --brands 로 몇 곳만 돌렸으면 나머지 브랜드의 태그를 지우면 안 된다. 예전엔 통째로
    # 덮어써서 「--brands kirsh」 한 번에 파일이 38,341벌 → 1,796벌이 됐다. 그 순간에
    # Actions 가 커밋했으면 36,545벌의 태그가 사라졌을 것이다(2026-09-05에 실제로 밟았다).
    if args.brands:
        keep = set(args.brands)
        prev = {}
        if Path(args.out).exists():
            try:
                prev = json.loads(Path(args.out).read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                prev = {}
        merged = {u: e for u, e in prev.items() if (e or {}).get("brand_slug") not in keep}
        merged.update(out)
        print(f"  --brands 로 {len(keep)}곳만 돌렸다 — 나머지 {len(merged) - len(out)}벌은 그대로 둔다")
        out = merged
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
    n = len(out)
    print(f"상품 {n} → {args.out}")
    print("품질:", dict(q_count))
    print("본문 출처:", dict(src_count))
    print("축 커버리지:")
    for ax in AXES:
        print(f"  {ax:15s} {ax_count[ax]:6d} ({ax_count[ax] / n:5.1%})")
    if args.report:
        val = {ax: Counter() for ax in AXES}
        for e in out.values():
            for ax, vs in e["tags"].items():
                val[ax].update(vs)
        for ax in AXES:
            print(f"\n[{ax}]", ", ".join(f"{v} {c}" for v, c in val[ax].most_common(12)))


if __name__ == "__main__":
    main()
