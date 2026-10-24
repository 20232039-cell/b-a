"""사진 대조 QA(2026-10-27) 회귀 — 품목 · 데님 워싱 · 성별 · 착용컷."""
import unittest
from unittest.mock import patch

import crawl_cafe24 as C
import export_app_data as app
import tag_items


class NameKeywordNotTheItemTest(unittest.TestCase):
    def test_garment_head(self):
        cases = {
            "NO FATIGUE (BLUE)(Fabric : A)": "",                      # 피곤 없음 — 퍼티그가 아니다
            "Drawstring wide fatigue pants, Black": "파티그팬츠",
            "ORIGINAL N-1.ZIP (OFF WHITE)(Fabric : A)": "집업",
            "GARMENT DYEING ORIGINAL N-1 ZIP (LAVENDER)": "집업",
            "N-1 Deck Jacket_Navy": "재킷",
            "ESSENTIAL STRAIGHT CORDUROY JEANS DEEP BROWN": "팬츠",
            "소크드 레귤러 코듀로이 진 네이비": "팬츠",
            "Corduroy Repair Regular Fit Denim Pants": "데님",
            "Cardigan Set-Up Top _ Yellow": "가디건",
            "LACE V-NECK KNIT CARDIGAN TOP_BLACK": "가디건",
            "BASIC CARDIGAN T/BLUE": "가디건",
            "LOGO T": "티셔츠",
            "UNISEX INTARISA ZIP-UP CREWNECK SWEATER": "니트집업",
            "HALF ZIP-UP HOOD KNIT LILAC": "하프집업니트",
            "컷앤소 원피스 스윔웨어 [화이트]": "수영복",
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(C.garment_head(name), want)

    def test_accessory_words(self):
        cases = {
            "딥 헨리넥 나그랑 롱슬리브 - 백메란지": ("tops", ""),
            "수아레 유니섹스 365 반팔티 보스턴카키": ("tops", ""),
            "(우먼)코튼 100 크롭 후드 니트 보스턴 카키": ("tops", ""),
            "UNISEX 보스턴백 [BLACK]": ("bags", "보스턴백"),
            "풀리오 브리프 케이스_cognac dust": ("bags", "가방"),
            "Barbed Wire Acrylic Wallet Chain - Clear": ("accessories", "키링"),
            "월렛 체인 미니백 - 카멜": ("bags", "미니백"),
            "[AW26] DUAL YARN KNIT CAP / ANTHRACITE": ("headwear", "비니"),
            "MONK STRAP SHOES / Black": ("shoes", "더비"),
            "Cashmere Bandeau Snood, Charcoal": ("accessories", "목도리"),
            "Lumbar Pack (CAYL x QMC) / B-grid Black": ("bags", "웨이스트백"),
            "삭 리오레_블랙": ("bags", "가방"),
            "ONE TUCK STRAP WOOL PT (BLACK)": ("bottoms", ""),
            "Sheer layered scarf long sleeves IVORY": ("tops", ""),
            "Draped string blouse scarf set_brown": ("tops", ""),
            "[SET] Essential V Neck Knit & Shawl-Blue": ("tops", ""),
            "NYLON HOOD VEST +GYM SACK SET - BLACK": ("tops", ""),
            "Knit Beanie & Muffler Set": ("accessories", "스카프"),
            "logo beanie short": ("headwear", "비니"),
        }
        for name, (code, acc) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(C.classify_category(name, []), code)
                self.assertEqual(C.acc_of(name), acc)

    def test_shearling_bag_follows_store_menu(self):
        name = "SOFT BOWLING MINI_beige shearling+chestnut nubuck"
        self.assertEqual(C.classify_category(name, ["SHOP", "NEW IN", "BAGS", "SOFT BOWLING"]), "bags")
        self.assertEqual(C.classify_category("DV.LOT 692 Type B-6 Sheep Shearling", ["cate_1", "Leather Jackets"]), "outer")

    def test_fit_item_in_store_menu(self):
        cats = ["ADERERRROR Main Collection", "커브드 핏 진"]
        self.assertEqual(C.cate_item(cats), "데님")
        self.assertEqual(C.classify_category("Product. 66", cats), "bottoms")
        self.assertEqual(C.cate_item(["ADERERRROR Main Collection", "세미 오버사이즈 핏 후디"]), "후드")
        self.assertEqual(C.cate_item(["Shop", "Top"]), "")

    def test_skirt_pants_pattern(self):
        for n in ("LAYERED SKIRT PANTS_BLACK", "CANCAN RUFFLE PANTSKIRT BLACK", "랩 레이어드 스커트 팬츠 [블랙]"):
            self.assertTrue(C._SKIRT_PANTS.search(n), n)
        self.assertFalse(C._SKIRT_PANTS.search("Sleek Mini Skorts-Beige"))

    def test_synthetic_body_is_not_denim(self):
        self.assertTrue(C._synthetic_body({"mat": [{"p": "", "v": [["나일론", 100]]}]}))
        self.assertFalse(C._synthetic_body({"mat": [{"p": "겉감", "v": [["코튼", 98], ["스판덱스", 2]]}]}))
        self.assertFalse(C._synthetic_body({}))


class GenderTest(unittest.TestCase):
    def test_split_skirt_and_dress_items_are_women(self):
        for item in ("롱스커트", "미니스커트", "미디스커트", "미니원피스", "롱원피스", "슬립원피스"):
            with self.subTest(item=item):
                self.assertEqual(C.classify_gender([], "UNISEX", "H-LINE LONG SKIRT LIGHT BEIGE", item), "WOMENSWEAR")
        self.assertEqual(C.classify_gender([], "UNISEX", "Denim Overall Jumpsuit", "점프수트"), "UNISEX")

    def test_underwear_waist_is_unreliable(self):
        g = C.classify_gender([], "UNISEX", "ALLCOTTONSERVICE MODAL BOXER BRIEF (3-PACK) / Multi", "언더웨어",
                              None, None, "bottoms", (30.0, 34.0))
        self.assertEqual(g, "UNISEX")


class DenimWashTest(unittest.TestCase):
    def test_grey_is_black_wash_unless_blue(self):
        self.assertEqual(app.denim_wash("클래식 스트레이트 데님 팬츠 (그레이)", "그레이", ""), "흑청")
        self.assertEqual(app.denim_wash("SIJN6083 기모 가먼트 다잉 슬로우 데님_Deep charcoal", "차콜", ""), "흑청")
        self.assertEqual(app.denim_wash("스트레치 슬림 크롭 진 그레이 블루", "그레이", ""), "")
        self.assertEqual(app.denim_wash("벨티드 라운드 데님_blue grey", "그레이", ""), "")

    def test_colour_words_need_word_boundary(self):
        self.assertEqual(app.denim_wash("Men's colored straight fit denim, Blue", "블루", ""), "")
        self.assertEqual(app.denim_wash("UNISEX LAYERED WIDE-LEG JEANS (BLUE)", "블루", ""), "")
        self.assertEqual(app.denim_wash("내츄럴 트윈 워시드 맥시 데님 팬츠 [인디고 레드]", "인디고", ""), "")
        self.assertEqual(app.denim_wash("Python Layered Denim Pants - Brown", "브라운", ""), "컬러")
        self.assertEqual(app.denim_wash("SNAKE BONE WASHED DENIM PANTS (BLUE)", "블루", ""), "")
        self.assertEqual(app.denim_wash("Rigid Denim One Wash", "블루", ""), "생지")

    def test_multi_colour_rep_does_not_decide(self):
        self.assertEqual(app.denim_wash("[4차]_마일드 데님", "화이트·블랙·그레이", "S | M | L"), "")
        self.assertEqual(app.denim_wash("워시드 데님 롱 스커트_2Color", "블루·블랙", "BLUE-S | BLACK-S"), "")
        self.assertEqual(app.denim_wash("Loose Wide Denim Pants - Black", "블랙", ""), "블랙")

    def test_store_colourway_name(self):
        self.assertEqual(app.denim_wash("Side One Tuck Denim Balloon Pants - Deep Blue", "블루", "", "not4nerd"), "")
        self.assertEqual(app.denim_wash("Side One Tuck Denim Balloon Pants - Deep Blue", "블루", ""), "진청")
        self.assertEqual(app.denim_wash("Card Wallet Denim Jacket - Light Blue", "블루", "", "not4nerd"), "연청")


class WornTest(unittest.TestCase):
    def worn(self, category, fr, z=None, stale=False, zimg=None):
        zs = {} if z is None else {"u": z}
        images = {} if zimg is None else {"u": zimg}
        with patch.object(app, "_WORN", {"u": ("old" if stale else "img", fr)}), patch.object(app, "_WORN_ZS", zs), \
                patch.object(app, "_WORN_ZS_IMAGES", images):
            return app.worn({"source_url": "u", "image_url": "img", "category": category})

    def test_segmentation_only_when_no_score(self):
        self.assertEqual(self.worn("Tops", {"4": .2, "14": .02}), 1)
        self.assertEqual(self.worn("Tops", {"7": .4052, "11": .0001}), 0)
        self.assertEqual(self.worn("Tops", {"14": .02}, stale=True), 0)

    def test_product_shot_veto(self):
        # 흰 배경 모자 — 분할 모델이 윗옷 · 머리카락으로 칠했다(the-coldest-moment-1359)
        self.assertEqual(self.worn("Headwear", {"2": .0605, "4": .4845, "7": .0312}, z=.01), 0)

    def test_partial_crops(self):
        # 허리 아래 바지 컷 — 다리는 바지에 덮여 칸이 비고 신발만 보인다(begin202-213)
        pants = {"4": .0118, "6": .4151, "9": .009, "10": .0086, "11": .0082}
        self.assertEqual(self.worn("Pants", pants, z=.92), 1)
        self.assertEqual(self.worn("Pants", pants, z=.6), 1)
        self.assertEqual(self.worn("Pants", {"4": .055, "6": .444}, z=.55), 0)    # 납작하게 놓은 바지 — 몸이 안 칠해졌다
        self.assertEqual(self.worn("Pants", pants, z=.4), 0)

    def test_unmeasured_photo_uses_score(self):
        self.assertEqual(self.worn("Tops", None, z=.45), 1)
        self.assertEqual(self.worn("Tops", None, z=.2), 0)

    def test_changed_photo_needs_matching_score_image(self):
        self.assertEqual(self.worn("Tops", {"4": .2}, z=.9, stale=True), 0)
        self.assertEqual(self.worn("Tops", {"4": .2}, z=.9, stale=True, zimg="img"), 1)
        self.assertEqual(self.worn("Tops", None, z=.9, zimg="old"), 0)
        self.assertEqual(self.worn("Tops", {"14": .02}, z=.01, zimg="old"), 1)


class BottomDetailsTest(unittest.TestCase):
    def test_negated_fatigue_is_not_a_detail(self):
        self.assertEqual(app.bottom_details("NO FATIGUE (BLUE)", "스웨트팬츠"), [])
        self.assertEqual(app.bottom_details("ANTI-FATIGUE PANTS", "팬츠"), [])
        self.assertEqual(app.bottom_details("Drawstring fatigue pants", "퍼티그팬츠"), ["퍼티그"])


class DenimColourwayMaterialTest(unittest.TestCase):
    def test_nylon_denim_blue_is_not_denim(self):
        body = "소재 : 나일론 100%"
        mats = tag_items.order_materials({"데님", "나일론"}, body, "EQ Wide Pants / Denim Blue", "Pants", ["데님"])
        self.assertNotIn("데님", mats)
        mats = tag_items.order_materials({"데님", "코튼"}, "코튼 100%", "Mil Pants Denim Blue", "Pants", ["데님"])
        self.assertIn("데님", mats)


if __name__ == "__main__":
    unittest.main()
