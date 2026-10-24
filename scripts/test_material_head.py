"""설명 맨 앞 혼용률 줄 · 매장 견본 혼용률 · 대표소재(ma) 회귀 검사(qa27 2026-10-27). 네트워크·원본 data에는 쓰지 않는다."""
import io
import unittest
from pathlib import Path
from unittest.mock import patch

import export_app_data as exp
import product_desc as desc
import tag_items as tag


def group(part, *values):
    return {"p": part, "v": [list(v) for v in values]}


class HeadLineTest(unittest.TestCase):
    def first(self, text, mat):
        return desc.clean(text, mat=mat).split("\n")[0]

    def test_label_from_anywhere_in_line_is_not_used(self):
        # matin-kim 9165: 「안감 없음」의 안감이 혼용률 부위로 붙었다
        text = "면 100% -비침 보통 -신축성 보통 -안감 없음 -두께감 보통 빈티지한 워싱 소재의 니트 베스트 포켓 디테일"
        self.assertTrue(desc.material_of(text).startswith("안감"))           # 글만 읽는 옛 길은 그대로(mat 없음)
        self.assertEqual(self.first(text, [group("", ("코튼", 100))]), "코튼 100%")

    def test_agreeing_head_keeps_store_words(self):
        text = "WOOL 88% NYLON 12%\n셔츠형 카라 디자인의 자켓입니다."
        self.assertEqual(self.first(text, [group("", ("울", 88), ("나일론", 12))]), "WOOL 88% NYLON 12%")

    def test_truncated_head_is_rewritten_from_mat(self):
        # rest-recreation 2085: 「POLYURETHANE(SPANDEX) 3%」를 못 읽어 「COTTON 97%」만 남았다
        text = "코튼 스판 소재의 슬림 핏 슬리브리스\nCOTTON 97% POLYURETHANE(SPANDEX) 3%"
        mat = [group("", ("코튼", 97), ("스판덱스", 3))]
        self.assertEqual(self.first(text, mat), "코튼 97% 스판덱스 3%")

    def test_leather_bag_head_is_leather_not_lining(self):
        # margesherwood: 가죽은 섬유 낱말이 아니라 안감만 머리줄이 됐다
        text = ("이태리 풀업 가죽을 사용한 오버사이즈 백입니다.\nVegetable tanned pull-up cow leather 100%\n"
                "Lining: Cotton 100%")
        mat = [group("", ("소가죽", 100)), group("안감", ("코튼", 100))]
        self.assertEqual(self.first(text, mat), "겉감 소가죽 100% / 안감 코튼 100%")

    def test_empty_mat_drops_generated_head(self):
        text = "안감 처리로 보온성 우수한 자켓입니다. WOOL 88% NYLON 12%"
        self.assertFalse(desc.clean(text, mat=[]).startswith("안감 WOOL"))

    def test_restated_mix_in_body_is_not_repeated(self):
        text = "VOLUME BLOUSE CHIEF MATERIAL : POLYESTER 99% SPAN 1%\n촘촘한 조직감의 폴리에스터 원단으로 제작되었습니다."
        out = desc.clean(text, mat=[group("", ("폴리에스터", 99), ("스판덱스", 1))]).split("\n")
        self.assertEqual(sum("99%" in ln for ln in out), 1)

    def test_parts_agree_needs_same_part(self):
        self.assertFalse(desc.parts_agree({"안감": "COTTON 100%"}, [group("", ("코튼", 100))]))
        self.assertTrue(desc.parts_agree({"겉감": "COTTON 100%"}, [group("", ("코튼", 100)), group("안감", ("폴리에스터", 100))]))
        self.assertTrue(desc.parts_agree({"원단": "POLY 60% PU 40%"}, [group("겉감", ("폴리에스터", 60), ("폴리우레탄", 40))]))
        # 표준 이름이 없는 섬유(PVC)가 섞여도 넘어지지 않는다(전수 첫 판에서 정렬하다 넘어졌다)
        self.assertFalse(desc.parts_agree({"겉감": "PVC 50% COTTON 50%"}, [group("", ("코튼", 50), ("PVC", 50))]))

    def test_mined_picture_head_follows_mat(self):
        rec = {"material": {"안감": "WOOL 50% NYLON 50%"},
               "description": ["부드러운 울 혼방 원단으로 제작한 니트 가디건입니다 따뜻합니다"]}
        self.assertTrue(desc.from_mined(rec, [group("", ("울", 50), ("나일론", 50))]).startswith("울 50% 나일론 50%"))
        self.assertTrue(desc.from_mined(rec).startswith("안감 WOOL"))


class StoreTemplateTest(unittest.TestCase):
    TCM = ("SHELL : COTTON 46% , RAYON 46% , POLYURETHANE 8% CONTRAST : POLYESTER 60% , COTTON 35% , "
           "POLYURETHANE {n}% Made In China {name}")

    def test_tcm_numbers_vary_but_block_is_one_template(self):
        items, names = [], []
        for i in range(8):
            names.append(f"TCM item {i} (black)")
            items.append((self.TCM.format(n=500 + i, name=names[-1]), [group("", ("코튼", 100))]))
        keys = desc.store_templates(items)
        self.assertEqual(len(keys), 1)
        self.assertEqual(desc.drop_templates(items[0][0], items[0][1], keys, names[0]), "")

    def test_noice_template_kept_where_it_is_the_product_own(self):
        tpl = "• fabric :MERINO WOOL 50%NYLON 30%ACRYLIC 20% •"
        cotton = [group("", ("코튼", 100))]
        knit = [group("", ("울", 50), ("나일론", 30), ("아크릴", 20))]
        items = [(f"그래픽 프린팅 스웻셔츠입니다.\n혼용률 : COTTON 100%\n{tpl}", cotton) for _ in range(6)]
        items += [(f"메리노 울 혼방 니트입니다.\n{tpl}", knit) for _ in range(2)]
        keys = desc.store_templates(items)
        self.assertTrue(keys)
        self.assertNotIn("MERINO", desc.drop_templates(items[0][0], cotton, keys))
        self.assertIn("혼용률 : COTTON 100%", desc.drop_templates(items[0][0], cotton, keys))
        self.assertIn("MERINO WOOL 50%", desc.drop_templates(items[-1][0], knit, keys))

    def test_common_true_fabric_is_not_template(self):
        items = [("COTTON 95% SPANDEX 5%\n코튼 스판 소재의 티셔츠", [group("", ("코튼", 95), ("스판덱스", 5))])] * 10
        self.assertEqual(desc.store_templates(items), set())


class BareShellNameTest(unittest.TestCase):
    def test_leather_before_slash_makes_lining_partial(self):
        # atelier-de-lumen 1541 · 1960: 가죽 겉감 / 안감 혼용률
        self.assertEqual(desc.mix("Product Info - Cow Leather / Polyester 92% Spandex 8% - 통 수납칸 1")[:2], ([], "partial"))
        self.assertEqual(desc.mix("Product Info - Cotton 60% / Polyester 40%")[0],
                         [group("", ("코튼", 60), ("폴리에스터", 40))])


class ManualMatTest(unittest.TestCase):
    def test_duplicate_fiber_is_summed_and_names_canonical(self):
        text = ('브랜드,링크,부위,섬유,퍼센트,왜\n'
                'x,dup,겉감,폴리에스터,50,Polyester\n'
                'x,dup,겉감,폴리에스터,25,Recycled polyester\n'
                'x,dup,겉감,레이온,25,\n'
                'x,odd,겉감,캐시나일론,55,\n'
                'x,odd,겉감,Polyester,40,\n'
                'x,odd,겉감,FLANT MATERIAL,5,\n')
        with patch.object(Path, 'exists', return_value=True), \
             patch.object(Path, 'open', side_effect=lambda **kwargs: io.StringIO(text)), \
             patch.object(tag, 'MANUAL_MAT_RAW', {}):
            got = tag.load_manual_mat()
        self.assertEqual(got['dup'], [group('', ('폴리에스터', 75), ('레이온', 25))])
        self.assertEqual(got['odd'], [group('', ('나일론', 55), ('폴리에스터', 40), ('기타', 5))])


class MainMaterialTest(unittest.TestCase):
    def ma(self, mats, mat, name="X", cat="Knitwear", sub="니트"):
        return exp.main_material({"category": cat, "subtype": sub, "name": name}, {"material": mats}, mat)

    def test_minor_name_fiber_loses_to_shell_majority(self):
        # unionblue 641 「CASHMERE WOOL V-NECK KNIT」 울 90 · 캐시미어 10
        self.assertEqual(self.ma(["캐시미어", "울"], [group("", ("울", 90), ("캐시미어", 10))]), "울")
        self.assertEqual(self.ma(["스판덱스", "폴리에스터"], [group("", ("폴리에스터", 92), ("스판덱스", 8))]), "폴리에스터")

    def test_fiber_absent_from_mat_is_replaced(self):
        # wednesday-oasis: 레이온인데 혼용률은 코튼 100
        self.assertEqual(self.ma(["레이온"], [group("", ("코튼", 100))], cat="Shirts", sub="셔츠"), "코튼")

    def test_lining_and_trim_never_win(self):
        mat = [group("겉감", ("울", 60), ("나일론", 40)), group("안감", ("폴리에스터", 100))]
        self.assertEqual(self.ma(["폴리에스터", "울"], mat, cat="Outerwear", sub="코트"), "울")
        self.assertEqual(self.ma(["폴리에스터"], [group("안감", ("폴리에스터", 100))], cat="Outerwear", sub="코트"), "폴리에스터")

    def test_leather_shell_wins_over_lining_fiber(self):
        mat = [group("", ("소가죽", 100)), group("안감", ("코튼", 100))]
        self.assertEqual(self.ma(["코튼", "가죽"], mat, cat="Bags", sub="가방"), "소가죽")
        self.assertEqual(self.ma(["가죽"], mat, cat="Bags", sub="가방"), "가죽")

    def test_leather_named_faux_leather_keeps_leather_but_bag_text_noise_does_not(self):
        # nick-nicole 7666 「LEATHER HALF COAT」 폴리 100 — 페이크 레더 바탕천
        self.assertEqual(self.ma(["가죽"], [group("", ("폴리에스터", 100))], name="LEATHER HALF COAT_BROWN",
                                 cat="Outerwear", sub="코트"), "가죽")
        # vunque 가방: 매장 글의 「천연가죽」이 첫 소재였지만 혼용률은 나일론 100
        self.assertEqual(self.ma(["가죽"], [group("", ("나일론", 100))], name="Hey Pocket Easy Cross S",
                                 cat="Bags", sub="가방"), "나일론")

    def test_look_materials_stay(self):
        self.assertEqual(self.ma(["니트"], [group("", ("코튼", 100))]), "니트")
        self.assertEqual(self.ma(["스웨이드"], [group("", ("폴리에스터", 100))], cat="Outerwear", sub="자켓"), "스웨이드")
        self.assertEqual(self.ma(["다운"], [group("겉감", ("나일론", 100)), group("충전재", ("다운", 80), ("깃털", 20))],
                                 cat="Outerwear", sub="패딩"), "다운")
        self.assertEqual(self.ma(["인조가죽"], [group("", ("폴리우레탄", 60), ("폴리에스터", 40))], cat="Bags", sub="가방"),
                         "인조가죽")

    def test_no_mat_keeps_tag(self):
        self.assertEqual(self.ma(["울"], []), "울")
        self.assertEqual(self.ma([], [group("", ("코튼", 100))]), "코튼")


if __name__ == "__main__":
    unittest.main()
