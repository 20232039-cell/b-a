"""무늬(pt) · 대표색(co) 회귀 검사 — 2026-10-27 사진 QA(qa27) 에서 나온 꼴. 데이터에는 쓰지 않는다."""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import crawl_cafe24 as c
import export_app_data as app
import photo_attrs as pa
import tag_items as t

PAT = pa.PAT_NAMES
FAMS = pa.FAMS


def pat(**probs):
    """무늬 확률(0~1) → photo_attrs 한 줄의 p(×10000)."""
    p = [0.0] * len(PAT)
    for k, v in probs.items():
        p[PAT.index(k)] = v
    return {"p": [int(round(x * 10000)) for x in p]}


def col(**probs):
    c_ = [0.0] * len(FAMS)
    for k, v in probs.items():
        c_[FAMS.index(k)] = v
    return {"c": [int(round(x * 1000)) for x in c_]}


class EmbeddingScoreTest(unittest.TestCase):
    def test_crop_overrides_full_and_missing_crop_falls_back(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, urls, vectors in (("siglip", ["a", "b", "excluded"], [[1, 0], [1, 0], [0, 1]]),
                                        ("siglip_crop", ["a"], [[0, 1]])):
                dest = root / "emb" / name
                dest.mkdir(parents=True)
                np.savez(dest / "part_0000.npz", E=np.array(vectors, dtype=np.float16),
                         meta=json.dumps([{"u": u} for u in urls]))
            tp = np.zeros((len(PAT), 2), np.float32)
            tp[0, 0] = tp[1, 1] = 1
            tc = np.zeros((len(pa.COL), 2), np.float32)
            tc[pa.COL.index("red"), 0] = tc[pa.COL.index("blue"), 1] = 1
            with patch.object(pa, "DATA", root):
                got = pa.score_embeddings({"a", "b"}, tp, tc, 20)
            self.assertEqual(set(got), {"a", "b"})
            self.assertEqual(pa.pattern_photo(got["a"]), ("pattern", "stripe"))
            self.assertEqual(pa.pattern_photo(got["b"]), ("plain", "plain"))
            self.assertEqual(pa.color_family(got["a"])[0], "blue")
            self.assertEqual(pa.color_family(got["b"])[0], "red")


class PatternNoiseTest(unittest.TestCase):
    def setUp(self):
        self.tagger = t.Tagger({ax: ({} if ax != "pattern" else {"체크": ["check", "checked", "깅엄", "gingham"],
                                                                     "스트라이프": ["stripe", "striped"],
                                                                     "페이즐리": ["paisley"]})
                                for ax in t.AXES})

    def tag(self, name, body):
        return self.tagger.tag("Skirts", name, body, "", "ok").get("pattern")

    def test_check_as_verb_is_not_a_pattern(self):
        # lartisan 세탁 안내 — 의류 518벌이 이 한 줄로 체크무늬였다
        body = "washing notice 손세탁 시 중성 세제를 푼 차가운 물에 담가두지 말고 바로 세탁해 주세요.\n(중성세제 뒷면을 체크하여 중성세제 전용인지 확인해 주세요.)"
        self.assertEqual(self.tag("L6 POCKET SLIT SKIRT(BROWN)", body), ["단색"])
        self.assertEqual(self.tag("SKIRT", "check up 착용시기 봄 여름 · please check the size"), ["단색"])

    def test_lining_and_trim_patterns_are_not_the_shell(self):
        self.assertEqual(self.tag("WORK JACKET", "안감에 얇은 플란넬 체크 원단 적용 - 전면에 손 넣기 편한 포켓"), ["단색"])
        self.assertEqual(self.tag("HOODIE", "후드끈 스트라이프 배색 포인트"), ["단색"])
        self.assertEqual(self.tag("PANTS", "주머니 안쪽 페이즐리 안감 배색 디테일"), ["단색"])

    def test_real_patterns_survive(self):
        self.assertEqual(self.tag("체크 플리츠 스커트", "울 혼방 체크 원단"), ["체크"])
        self.assertEqual(self.tag("STRIPED SHIRT", "cotton, lining: polyester"), ["스트라이프"])


class PatternFlagTest(unittest.TestCase):
    def test_name_words(self):
        self.assertEqual(pa.name_patterns("Wkndrs Camo Cargo Pants"), {"카모"})
        self.assertEqual(pa.name_patterns("레오파드 미디 스커트"), {"애니멀프린트"})
        self.assertEqual(pa.name_patterns("Micro Gingham Shirt"), {"체크"})
        # 로고 이름 · 동사 · 단추는 무늬가 아니다(photoB 이름 낱말 36벌 중 12벌)
        for n in ("FLOWERMARDI LOGO TEE", "DOTCOM HOODIE", "Checkmate Charm", "도트 단추 셔츠", "사이즈 체크하기"):
            self.assertEqual(pa.name_patterns(n), set(), n)

    def test_photo_thresholds(self):
        self.assertEqual(pa.pattern_photo(pat(plain=0.95, stripe=0.05))[0], "plain")
        self.assertEqual(pa.pattern_photo(pat(plain=0.001, stripe=0.999))[0], "pattern")
        self.assertEqual(pa.pattern_photo(pat(plain=0.005, stripe=0.995))[0], "")       # 줄무늬는 0.998 부터
        self.assertEqual(pa.pattern_photo(pat(plain=0.01, graphic=0.99))[0], "pattern")  # 큰 그래픽은 0.985 부터
        self.assertEqual(pa.pattern_photo(pat(plain=0.03, logo=0.97))[0], "")
        # 선별기가 검증한 것은 한 무늬 확률 > 0.7 — 합쳐서 문턱을 넘기지 않는다.
        self.assertEqual(pa.pattern_photo(pat(plain=0.01, graphic=0.50, logo=0.49))[0], "")
        self.assertEqual(pa.pattern_photo(pat(plain=0.002, check=0.998))[0], "pattern")
        self.assertEqual(pa.pattern_photo(pat(plain=0.015, graphic=0.985))[0], "pattern")

    def test_keys_priority(self):
        plain = pat(plain=0.97, stripe=0.03)
        # 이름이 사진을 이긴다(마이크로 깅엄 · 톤온톤 스트라이프는 사진에서 안 보인다)
        self.assertEqual(pa.pattern_keys("Tonal Stripe Jacquard Knit", {}, photo=plain), {"스트라이프", "자카드"})
        # 본문 태그만인 무늬는 사진이 무지면 버린다
        self.assertEqual(pa.pattern_keys("POCKET SKIRT", {"pattern": ["체크"]}, photo=plain), set())
        # 사진이 큰 무늬라 하면 이름에 없어도 무늬
        self.assertEqual(pa.pattern_keys("BASIC TEE", {"pattern": ["단색"]}, photo=pat(plain=0.0005, camo=0.9995)), {"카모"})
        # 작은 로고: 그래픽 태그 + photo_pred 만으로는 사진이 있을 때 무늬가 아니다(cayl)
        mid = pat(plain=0.6, logo=0.4)
        t_ = {"pattern": ["단색"], "design_element": ["그래픽"]}
        self.assertEqual(pa.pattern_keys("hiking pants", t_, photo=mid, graphic=0.9), set())
        split = pat(plain=0.05, graphic=0.60, logo=0.35)
        self.assertEqual(pa.pattern_keys("PRINT TEE", t_, photo=split, graphic=0.9), {"그래픽"})
        self.assertEqual(pa.pattern_keys("BASIC TEE", {}, photo=split, graphic=0), set())
        # 슈러그 밑에 입은 티셔츠 프린트를 상품의 무늬·색으로 옮기지 않는다.
        self.assertEqual(pa.pattern_keys("ALPACA TURTLE SHRUG", {}, photo=pat(plain=0.001, graphic=0.999)), set())
        self.assertFalse(pa.photo_safe("ALPACA TURTLE SHRUG"))
        # 사진이 없으면 예전 규칙 그대로
        self.assertEqual(pa.pattern_keys("hiking pants", t_, photo=None, graphic=0.9), {"그래픽"})
        # 케이블 · 멜란지는 짜임 · 실 색
        self.assertEqual(pa.pattern_keys("CABLE KNIT", {"pattern": ["케이블", "멜란지"]}), set())
        # 잡화는 이름만
        self.assertEqual(pa.pattern_keys("카모 볼캡", {}, garment=False), {"카모"})
        self.assertEqual(pa.pattern_keys("볼캡", {"pattern": ["체크"]}, garment=False), set())

    def test_export_pt_without_similar_neighbors(self):
        # similar_axes 에 이웃이 없어도(AX_FLAGS 비어 있음) 무늬가 나간다 — 예전엔 f & 7 일 때만 pt 를 옮겼다
        row = {"brand_slug": "x", "product_no": "1", "name": "CAMO CARGO PANTS", "source_url": "u1",
               "category_code": "bottoms", "category": "Pants", "price": "1", "status": "SOLD_OUT"}
        tags = {"u1": {"tags": {"pattern": ["카모"]}}}
        self.assertNotIn("x-1", app.AX_FLAGS)
        self.assertEqual(app.thin_row(row, tags, {"x": 0}, {"bottoms": 0}, {}).get("pt"), 1)


class PatternFixTest(unittest.TestCase):
    def test_manual_pattern_fix_wins(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "pattern_fix.csv"
            p.write_text("id,pt,why\nx-1,0,함께 입은 옷 무늬\nx-2,1,톤온톤 스트라이프\nx-3,,빈 값\n", encoding="utf-8")
            fix = pa.load_pattern_fix(p)
        self.assertEqual(fix, {"x-1": 0, "x-2": 1})
        self.assertEqual(pa.apply_pattern_fix("x-1", {"체크"}, fix), set())
        self.assertEqual(pa.apply_pattern_fix("x-2", set(), fix), {"무늬"})
        self.assertEqual(pa.apply_pattern_fix("x-9", {"카모"}, fix), {"카모"})

    def test_fine_stripe_store_keeps_text(self):
        plain = pat(plain=0.97, stripe=0.03)
        self.assertEqual(pa.pattern_keys("H-LINE MAXI SKIRT", {"pattern": ["스트라이프"]}, photo=plain, brand="dunst"), {"스트라이프"})
        self.assertEqual(pa.pattern_keys("H-LINE MAXI SKIRT", {"pattern": ["스트라이프"]}, photo=plain, brand="other"), set())

    def test_weak_name_needs_photo(self):
        floral = pat(plain=0.002, floral=0.99)   # 0.998 — 문턱(0.997) 위라 사진만으로도 무늬
        near = pat(plain=0.004, floral=0.996)    # 문턱 아래
        self.assertEqual(pa.pattern_keys("FLOWER CARDIGAN IVORY", {}, photo=near), {"플로럴"})
        self.assertEqual(pa.pattern_keys("FLOWERMARDI LOGO TEE", {}, photo=pat(plain=0.95, floral=0.05)), set())
        self.assertEqual(pa.pattern_keys("BASIC CARDIGAN", {}, photo=near), set())
        self.assertEqual(pa.pattern_keys("ROSE TEE", {}, photo=pat(plain=0.004, stripe=0.996)), set())
        self.assertEqual(pa.pattern_keys("BASIC TEE", {}, photo=floral), {"플로럴"})


class ColorTest(unittest.TestCase):
    def test_two_tone_option_takes_body(self):
        self.assertEqual(c.pick_color("SLIM JOGGER PANTS M.R.D. LOGO_RED IVORY", ""), "레드")
        self.assertEqual(c.pick_color("[ACTIF] SWIMHAT JARDIN_PINK BLACK", "", options=["FREE"]), "핑크")
        self.assertEqual(c.pick_color("BIG BUTTERFLY SLEEVE TOP_BLACK SILVER", ""), "블랙")
        self.assertEqual(c.pick_color("BUTTERFLY SIGNATURE HOODIE_MELANGE BLACK", ""), "그레이")
        # 원단 이름 · 금속 장식은 예전대로
        self.assertEqual(c.pick_color("TEE_WHITE MELANGE", ""), "그레이")
        self.assertEqual(c.pick_color("플럼프 스퀘어 카우 레더 벨트 [SILVER BLACK]", ""), "블랙")
        # 앞 색은 패널·뒷면일 수도 있다 — 기존 compound 예외를 건너뛰지 않는다.
        self.assertEqual(c.pick_color("PANEL REVERSIBLE TEE [CREAM BLACK]", ""), "블랙")
        self.assertEqual(c.pick_color("PANEL TEE_CREAM BLACK", ""), "블랙")
        self.assertEqual(c.pick_color("RINGER TEE (WH/BK)", ""), "화이트")
        self.assertEqual(c.pick_color("RINGER TEE 블랙아이보리", ""), "블랙")
        self.assertEqual(c.pick_color("TEE PINK WHITE", ""), "핑크")
        self.assertEqual(c.pick_color("TEE (PINK WHITE)", "", options=["PINK", "WHITE"]), "화이트")
        self.assertEqual(c.pick_color("TEE_SAND BEIGE", ""), "베이지")
        # 원단 이름 · 한 색 이름은 배색이 아니다(2026-10-28 전수 대조 — beslow · o-oi 「DENIM BLACK」 검정 데님)
        self.assertEqual(c.pick_color("UNAFFECTED TRACK DENIM BLACK", ""), "블랙")
        self.assertEqual(c.pick_color("빈티지 데님스웻 티셔츠 [DENIM BLACK]", ""), "블랙")
        self.assertEqual(c.pick_color("DOUBLE WAIST JEANS apa831w(BLUE BLACK)", ""), "블랙")
        self.assertEqual(c.pick_color("COMFY CARPENTER SWEAT PANTS MOCHA GREY", ""), "그레이")
        self.assertEqual(c.pick_color("식스 포켓 와이드 슬랙스 (그레이스트라이프)", ""), "그레이")

    def test_color_slot_beats_longer_line_name(self):
        self.assertEqual(c.pick_color("Caramel Box Shoulder Bag_Beige", "", options=["N"]), "베이지")
        self.assertEqual(c.pick_color("블랙 오닉스 시슬 롱 네크리스_실버", "", options=["ONE", "SILVER"]), "실버")
        self.assertEqual(c.pick_color("VELVET BAG ROSE_RED", ""), "레드")

    def test_words_inside_other_words(self):
        self.assertEqual(c.pick_color("스틸 스터드 벨트", "", options=["O/S"]), "")
        self.assertEqual(c.pick_color("그레이팅 엠블럼 백슬릿 지퍼 트레이닝 팬츠 블랙", ""), "블랙")
        self.assertEqual(c.pick_color("콰이엇 젬스톤 네클레이스 블루", ""), "블루")
        self.assertEqual(c.pick_color("빈티지 워싱 워크 셔츠 (크림슨)", ""), "레드")
        self.assertEqual(c.pick_color("스트로베리 플라워 머플러 블랙", ""), "블랙")
        self.assertEqual(c.pick_color("rose lace french pants", ""), "")
        self.assertEqual(c.pick_color("Light multi pocket jacket / dark moss", ""), "")
        # doucan 품번 꼬리 BL 은 5벌 중 4벌이 정말 파랑이었다 — 규칙으로 안 바꾸고 한 벌(861)만 color_fix 제안
        self.assertEqual(c.pick_color("Puff Pintuck Dress (UWG-3512 CO BL)", ""), "블루")
        # 진짜 색은 그대로
        self.assertEqual(c.pick_color("Rosewood Check Shirt_Rose Dust", ""), "로즈")
        self.assertEqual(c.pick_color("SKIRT_Mocha gray", ""), "그레이")
        self.assertEqual(c.pick_color("KNIT (MOCHA GREY)", ""), "그레이")
        self.assertEqual(c.pick_color("KNIT (MOCHA)", ""), "모카")
        self.assertEqual(c.pick_color("MINT CHOCO", ""), "초코")
        self.assertEqual(c.pick_color("TRIMMING PULLOVER BK+IV", ""), "블랙")

    def test_photo_color(self):
        red = col(red=0.95, light=0.01)
        self.assertEqual(pa.color_photo("아이보리", red, alt="레드"), "레드")
        self.assertEqual(pa.color_photo("화이트", col(green=0.9, light=0.01), alt="민트"), "민트")
        self.assertEqual(pa.color_photo("블루", col(gray=0.9, blue=0.01)), "그레이")
        # light · dark 는 0.95 이상 + 이름이 그 색을 적었을 때만
        dark = col(dark=0.97, light=0.01)
        self.assertEqual(pa.color_photo("아이보리", dark, alt="네이비"), "네이비")
        self.assertEqual(pa.color_photo("아이보리", dark), "")
        self.assertEqual(pa.color_photo("아이보리", col(dark=0.9, light=0.01), alt="네이비"), "")
        # 절대 안 고치는 방향
        self.assertEqual(pa.color_photo("옐로우", col(light=0.99, yellow=0.001)), "")
        self.assertEqual(pa.color_photo("브라운", col(dark=0.99, brown=0.001)), "")
        # 지금 색 계열도 3% 넘게 보이면 그대로
        self.assertEqual(pa.color_photo("아이보리", col(red=0.9, light=0.05), alt="레드"), "")
        # 빈 색: 유채 계열만 채운다
        self.assertEqual(pa.color_photo("", col(pink=0.9)), "핑크")
        self.assertEqual(pa.color_photo("", col(dark=0.99)), "")
        self.assertEqual(pa.color_photo("", col(pink=0.7, light=0.3)), "")

    def test_color_among_selling_colors(self):
        self.assertEqual(pa.color_among(["블랙", "그레이"], col(gray=0.9)), "그레이")
        self.assertEqual(pa.color_among(["블랙", "그레이"], col(dark=0.97)), "")
        self.assertEqual(pa.color_among(["화이트", "그레이"], col(light=0.99)), "")
        self.assertEqual(pa.color_among(["베이지", "그레이"], col(beige=0.99)), "")
        self.assertEqual(pa.color_among(["블랙", "차콜"], col(dark=0.99)), "")      # 같은 계열 둘 — 못 고른다
        self.assertEqual(pa.color_among(["블랙", "그레이"], col(dark=0.9)), "")      # 무채는 0.95 부터
        self.assertEqual(pa.color_among(["블랙", "그레이"], col(pink=0.95)), "")     # 판매색에 없는 계열

    def test_export_color(self):
        row = {"brand_slug": "m", "product_no": "1", "name": "SLIM JOGGER PANTS LOGO_NAVY IVORY", "source_url": "u2",
               "category_code": "bottoms", "representative_color": "아이보리"}
        app.PHOTO_AT["u2"] = col(dark=0.98, light=0.005)
        try:
            self.assertEqual(app.index_color(row, {}), "네이비")
            row2 = dict(row, name="SWEAT PANTS", representative_color="", source_url="u3")
            app.PHOTO_AT["u3"] = col(green=0.92)
            self.assertEqual(app.index_color(row2, {}), "그린")
            self.assertEqual(app.index_color(dict(row2, name="SWEAT PANTS [3COLOR]"), {}), "")
            self.assertEqual(app.index_color(dict(row2, category_code="bags"), {}), "")
        finally:
            app.PHOTO_AT.pop("u2", None)
            app.PHOTO_AT.pop("u3", None)

    def test_manual_color_fix_beats_photo(self):
        row = {"brand_slug": "m", "product_no": "9", "name": "TOP", "source_url": "fixed",
               "category_code": "tops", "representative_color": "핑크"}
        app.COLOR_FIXED[("m", "9")] = "핑크"
        app.PHOTO_AT["fixed"] = col(blue=0.99)
        try:
            self.assertEqual(app.index_color(row, {}), "핑크")
            # 상품표가 아직 옛 색(블루)이어도 color_fix 가 이긴다 — 상품표 재생성 전에 더한 줄
            self.assertEqual(app.index_color(dict(row, representative_color="블루·그레이"), {}), "핑크")
        finally:
            app.COLOR_FIXED.pop(("m", "9"), None)
            app.PHOTO_AT.pop("fixed", None)


if __name__ == "__main__":
    unittest.main()
