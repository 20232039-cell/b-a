"""잡화 실측은 옷 라벨로 바꾸지 않고 원문의 축과 행을 보존한다."""
import re


DIM_LABELS = {
    "가로": r"가로|width|너비",
    "세로": r"세로|height",
    "폭": r"폭|depth|두께|깊이",
    "높이": r"높이",
    "길이": r"길이|length",
    "스트랩길이": r"스트랩\s*길이|strap\s*length|strap",
}
HEAD_LABELS = {
    "머리둘레": r"머리\s*둘레|circumference",
    "깊이": r"깊이|depth|height",
    "챙길이": r"챙\s*길이|brim\s*length|brim|visor",
}
_NUM = r"\d{1,3}(?:\.\d{1,2})?"
_NAME = r"(?:OS|ONE\s*SIZE|FREE|XXS|XS|S|M|L|XL|XXL|\d{1,2})"
_HEAD_LIMITS = {"머리둘레": (44, 70), "깊이": (4, 40), "챙길이": (2, 15)}


def _canon(raw, labels):
    raw = re.sub(r"\(\s*cm\s*\)", "", raw, flags=re.I).strip()
    return next((k for k, rx in labels.items() if re.fullmatch(rx, raw, re.I)), None)


def parse_dimensions(text, axis="bag"):
    """명시된 머리·값 표 또는 라벨·값 두 쌍 이상. 숫자만인 곱셈 표는 순서만 보존한다."""
    labels = HEAD_LABELS if axis == "head" else DIM_LABELS
    alias = "|".join(f"(?:{v})" for v in labels.values())
    header = re.compile(rf"(?<![A-Za-z가-힣])((?:{alias})\s*(?:/\s*(?:{alias})\s*)+)(?:\(\s*cm\s*\))?", re.I)
    row_rx = re.compile(rf"\s*\(?\s*({_NAME})\s*\)?(?:\s*[:：]\s*|\s+)({_NUM}(?:\s*/\s*{_NUM})+)(?![\d.])", re.I)
    for hm in header.finditer(text or ""):
        keys = [_canon(x, labels) for x in hm.group(1).split("/")]
        if None in keys or len(set(keys)) != len(keys):
            continue
        names, rows, pos = [], [], hm.end()
        while (rm := row_rx.match(text, pos)):
            vals = [float(v) for v in re.split(r"\s*/\s*", rm.group(2))]
            if len(vals) != len(keys) or rm.group(1) in names or not all(0 < v <= 300 for v in vals):
                break
            names.append(rm.group(1).strip()); rows.append(vals); pos = rm.end()
        if rows:
            if axis == "head" and any(not _HEAD_LIMITS[k][0] <= r[i] <= _HEAD_LIMITS[k][1]
                                      for i, k in enumerate(keys) for r in rows):
                continue
            return {"axis": axis, "size_names": names,
                    "sizes": {k: [r[i] for r in rows] for i, k in enumerate(keys)}}
    # H/L/W와 H/W/D는 같은 W라도 뜻이 다르므로 묶음 전체로 해석한다.
    if axis == "bag":
        for rx, keys in [(r"H\s*:\s*({n})\s*cm\s+L\s*:\s*({n})\s*cm\s+W\s*:\s*({n})\s*cm", ["세로", "가로", "폭"]),
                         (r"H\s*:\s*({n})\s*cm\s+W\s*:\s*({n})\s*cm\s+D\s*:\s*({n})\s*cm", ["세로", "가로", "폭"])]:
            m = re.search(rx.replace("{n}", _NUM), text or "", re.I)
            if m:
                return {"axis": axis, "size_names": None, "sizes": {k: [float(v)] for k, v in zip(keys, m.groups())}}
    sizes = {}
    pair = re.compile(rf"(?<![A-Za-z가-힣])({alias})\s*[:：]?\s*({_NUM})(?![\d.])(?:\s*cm)?", re.I)
    matches = list(pair.finditer(text or ""))
    for m in matches:
        if sizes and m.start() - end > 30:
            break
        k = _canon(m.group(1), labels)
        if k in sizes:
            break
        if 0 < float(m.group(2)) <= 300:
            sizes[k] = [float(m.group(2))]
            end = m.end()
    if len(sizes) >= 2:
        if axis == "head" and any(not _HEAD_LIMITS[k][0] <= v <= _HEAD_LIMITS[k][1] for k, vs in sizes.items() for v in vs):
            return None
        return {"axis": axis, "size_names": None, "sizes": sizes}
    if axis != "head":
        m = re.search(rf"(?<![\d.])({_NUM})\s*[x×*]\s*({_NUM})\s*[x×*]\s*({_NUM})\s*\(\s*cm\s*\)", text or "", re.I)
        if m and all(0 < float(v) <= 300 for v in m.groups()):
            return {"axis": axis, "size_names": None,
                    "sizes": {f"치수 {i}": [float(v)] for i, v in enumerate(m.groups(), 1)},
                    "dimension_text": m.group(0)}
    return None


def dimensions_from_html(soup):
    """표의 칸 경계와 탭 글을 잡화 전용 경로에 남긴다."""
    for tb in soup.find_all("table"):
        if tb.find_parent(class_=re.compile(r"review|qna|relation", re.I)):
            continue
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"], recursive=False)]
                for tr in tb.find_all("tr")]
        # 전치 표도 칸 경계로 읽어 첫 사이즈만 남는 일을 막는다.
        if rows and len(rows[0]) >= 2 and re.fullmatch(r"size\s*(?:\(cm\))?|사이즈", rows[0][0], re.I):
            names = rows[0][1:]
            if all(re.fullmatch(_NAME, n, re.I) for n in names):
                sizes = {}
                for row in rows[1:]:
                    key = _canon(row[0], DIM_LABELS) if row else None
                    nums = [re.fullmatch(rf"({_NUM})\s*(?:cm)?", v, re.I) for v in row[1:]]
                    if key and len(nums) == len(names) and all(nums):
                        sizes[key] = [float(n.group(1)) for n in nums]
                if len(sizes) >= 2 and all(0 < v <= 300 for vs in sizes.values() for v in vs):
                    return {"axis": "accessory", "size_names": names, "sizes": sizes}
        for i, head in enumerate(rows):
            keys = [_canon(x, DIM_LABELS) for x in head[1:]]
            if len(keys) < 2 or None in keys or len(set(keys)) != len(keys):
                continue
            names, values = [], []
            for row in rows[i + 1:]:
                if len(row) != len(head) or not re.fullmatch(_NAME, row[0], re.I):
                    break
                nums = [re.fullmatch(rf"({_NUM})\s*(?:cm)?", x, re.I) for x in row[1:]]
                if not all(nums) or not all(0 < float(n.group(1)) <= 300 for n in nums):
                    break
                names.append(row[0]); values.append([float(n.group(1)) for n in nums])
            if values:
                return {"axis": "accessory", "size_names": names,
                        "sizes": {k: [v[j] for v in values] for j, k in enumerate(keys)}}
    for root in [*soup.select("#prdDetail, .xans-product-additional, .detailArea"), soup]:
        # 관련 상품·모델 설명에서 길이를 가져오지 않도록 명시된 치수 축 둘을 요구한다.
        text = root.get_text(" ", strip=True)
        table = parse_dimensions(text, "head") or parse_dimensions(text, "accessory")
        if table:
            return table
    return None
