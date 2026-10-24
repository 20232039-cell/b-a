"""상품 이름 → 품목 회귀 검사."""
import unittest

import crawl_cafe24 as C


class CapeNameTest(unittest.TestCase):
    def test_cape_only_when_head_word(self):
        cases = {
            "CUT-OUT KNIT CAPE": "케이프/판초",
            "AE packable poncho": "케이프/판초",
            "Cape Alpaca Coat": "코트",
            "Cape-effect knitted dress": "원피스",
            "Flare Knit Skirt & Cape": "스커트",
            "Volume note two-piece": "투피스",
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                self.assertEqual(C.garment_head(name), want)

    def test_scarf_cape_stays_accessory(self):
        for name in ("3 Way Button Hole Cape Stole", "루넬 니트 숄 케이프", "Double-faced wool-blend muffler poncho"):
            with self.subTest(name=name):
                self.assertEqual(C.classify_category(name, []), "accessories")
                self.assertEqual(C.match_acc(name), "스카프")


if __name__ == "__main__":
    unittest.main()
