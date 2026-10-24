"""data/llm 판정으로 빈칸 채우기 — 모양 태그(빈 축만) · 데님 워싱(denim_wash_photo.json) · 안감 · 무게. conf high 이고 대조 정밀도 0.90 넘은 값만."""
from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
OK = {
    "silhouette": {"슬림핏", "세미와이드", "H라인"},
    "length": {"크롭", "쇼츠", "미니", "버뮤다", "맥시", "카프리"},
    "neckline": {"후드", "브이넥", "카라", "헨리넥", "하이넥", "터틀넥", "오프숄더", "홀터넥", "U넥", "모크넥"},
    "sleeve_length": {"롱슬리브", "반팔", "슬리브리스"},
    "pants_type": {"진", "카고", "스웨트", "슬랙스", "치노", "밴딩", "조거", "퍼티그", "벌룬", "워크", "레깅스"},
}
DW_OK = {"연청", "블랙", "화이트", "컬러"}
FEEL_OK = {"안감": {"있음", "없음"}, "무게": {"가벼움", "보통", "무거움"}}


def rows(name: str):
    p = DATA / "llm" / f"{name}.jsonl"
    if p.exists():
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                yield json.loads(l)


def main() -> None:
    tp = DATA / "product_tags_full.json"
    tags = json.loads(tp.read_text(encoding="utf-8"))
    n = {ax: 0 for ax in OK}
    for o in rows("shape"):
        e = tags.get(o["u"])
        out = o.get("out") or {}
        if not e:
            continue
        conf = out.get("conf") or {}
        tg = e.setdefault("tags", {})
        for ax, ok in OK.items():
            if conf.get(ax) != "high" or tg.get(ax):
                continue
            v = out.get(ax)
            vals = [x for x in (v if isinstance(v, list) else [v]) if x in ok]
            if vals:
                tg[ax] = vals
                src = e.setdefault("text_sources", [])
                if "llm" not in src:
                    src.append("llm")
                n[ax] += 1
    nf = 0
    for o in rows("feel"):
        e = tags.get(o["u"])
        fl = (o.get("out") or {}).get("feel") or {}
        if not e or not fl:
            continue
        cur = e.setdefault("feel", {})
        for k, ok in FEEL_OK.items():
            if k not in cur and fl.get(k) in ok:
                cur[k] = fl[k]
                nf += 1
    n["feel"] = nf
    tp.write_text(json.dumps(tags, ensure_ascii=False), encoding="utf-8")
    dp = DATA / "denim_wash_photo.json"
    d = json.loads(dp.read_text(encoding="utf-8")) if dp.exists() else {}
    items = {}
    for o in rows("denim"):
        out = o.get("out") or {}
        if out.get("conf") == "high" and out.get("w") in DW_OK:
            items[o["u"]] = {"w": out["w"], "p": 1.0}
    if items:
        d["items"], d["model"] = items, "claude-sonnet-5-5"
        dp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print("llm_fill:", n, "· 데님", len(items))


if __name__ == "__main__":
    main()
