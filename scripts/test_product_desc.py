"""표시용 OCR 잡음 회귀 검사. 네트워크와 data/에는 쓰지 않는다."""
import copy
import unittest
from unittest.mock import patch

import product_desc as desc
import size_from_ocr as size


class OcrDescriptionTest(unittest.TestCase):
    def test_short_noise_fragments(self):
        for text in (r"0\5\#'", "@@##", "___ ^ __", "YSolo|", "Mele]", "OAS}",
                     "q z v | i | e x ¢", "\\ # ^ |"):
            with self.subTest(text=text):
                self.assertEqual(desc.ocr_display_text(text), "")

    def test_dnsr_3320_keeps_sentences_and_removes_only_fragments(self):
        before = (
            "현대적인 감성과 트렌디한 디테일을 결합한 0\\5\\#'의 첫번째 협업 콜라보 입니다.\n"
            "전체 2겹의 원단으로 봉제되어 보다 자연스러운 레이어드 YSolo| 특징입니다.")
        expected = (
            "현대적인 감성과 트렌디한 디테일을 결합한 첫번째 협업 콜라보 입니다.\n"
            "전체 2겹의 원단으로 봉제되어 보다 자연스러운 레이어드 특징입니다.")
        self.assertEqual(desc.ocr_display_text(before), expected)

    def test_normal_ocr_sentences_blends_care_and_measurements_are_unchanged(self):
        examples = [
            "부드러운 터치감의 코튼 소재로 제작한 오버핏 셔츠입니다.",
            "겉감 COTTON 63% RAYON 33% POLYURETHANE 4%",
            "면 87% 모 13%", "COTTON 100 %", "100% Cotton, 70% Cotton 27% Silk 3% Spandex",
            "30℃ 이하에서 중성세제로 단독 손세탁해 주세요.",
            "HAND WASH / DO NOT BLEACH / DRY CLEAN ONLY",
            "S : 어깨 37.5 / 가슴 48 / 소매 57 / 총장 62 (±1~2cm)",
            "M 49 55 58 60", "SHOULDER | CHEST | SLEEVE | LENGTH (cm)",
            "S M L XL XXL", "A B C D E F |", "a b c d |", "S / M / L",
            "YKK 투웨이 지퍼와 RIRI 스냅을 사용했습니다.",
            "LYCRA 소재와 COOLMAX 원사, GORE-TEX와 eVent 원단을 사용했습니다.",
            "헤비투일 나일론 소재의 MA-1 자켓입니다.",
            "30D / 20수 / 24SS / 2XL / SWAWS920 / 99%IS- / #123",
            "낯선 SuperFine Merino 브랜드 원사를 사용했습니다.",
            "고밀도 [Super Fine Wool] 소재와 [SWAWS920] 스커트입니다.",
            "COTTON] 소재, YKK| 지퍼, CM] 단위의 실측입니다.",
            "DETAIL FABRIC COLOR SIZE", "%", "％", "±", "℃",
        ]
        for text in examples:
            with self.subTest(text=text):
                self.assertEqual(desc.ocr_display_text(text), text)

    def test_ambiguous_english_is_preserved(self):
        for text in ("ASUS 소재를 사용했습니다.", "새로운 AeWoe 소재입니다.",
                     "7HetTl 시원한 터치감의 원단입니다.", "UNKNOWNBRAND", "ASO]"):
            with self.subTest(text=text):
                self.assertEqual(desc.ocr_display_text(text), text)

    def test_ocr_marker_keeps_shop_prefix(self):
        shop = "매장 YSolo| 디테일과 0\\5\\#'의 소재입니다.\n"
        picture = "전체 원단으로 봉제한 YSolo| 레이어드 특징입니다."
        self.assertEqual(desc._description_text(shop + "[그림 글자]\n" + picture),
                         shop + "[그림 글자]\n전체 원단으로 봉제한 레이어드 특징입니다.")

    def test_html_shop_text_does_not_call_ocr_filter(self):
        with patch.object(desc, "ocr_display_text", side_effect=AssertionError("OCR 전용")):
            self.assertEqual(desc._description_text("<p>YKK &amp; YSolo| 원단입니다.</p>"),
                             "\nYKK & YSolo| 원단입니다.\n")
            self.assertTrue(desc.clean("부드러운 코튼 소재로 제작된 셔츠입니다."))

    def test_mined_record_and_materials_are_not_mutated(self):
        rec = {"material": {"겉감": "COTTON 100%"}, "description": [
            "전체 원단으로 봉제되어 자연스러운 레이어드 YSolo| 특징입니다."]}
        original = copy.deepcopy(rec)
        result = desc.from_mined(rec)
        self.assertEqual(result, "COTTON 100%\n전체 원단으로 봉제되어 자연스러운 레이어드 특징입니다.")
        self.assertEqual(rec, original)
        self.assertEqual(desc.best("", rec), (result, "ocr"))

    def test_extraction_does_not_use_display_filter(self):
        raw = ("FABRIC COTTON 63% RAYON 33% POLYURETHANE 4%\n"
               "SHOULDER CHEST SLEEVE LENGTH\nM 49 55 58 60\nL 51 57 60 62\n"
               "전체 원단으로 봉제한 YSolo| 레이어드 특징입니다.")
        # 혼용률 사전도 작은 가짜 값으로 두어 실제 data/에 의존하지 않는다.
        with patch.dict(desc._FIBER_CANON, {"cotton": "코튼", "rayon": "레이온",
                                          "polyurethane": "스판덱스"}, clear=True):
            before = desc.blend(raw), desc.mix(raw, ocr=True), size.from_ocr(raw)
            with patch.object(desc, "ocr_display_text", side_effect=AssertionError("표시 전용")):
                self.assertEqual((desc.blend(raw), desc.mix(raw, ocr=True), size.from_ocr(raw)), before)
        self.assertTrue(before[0])
        self.assertTrue(before[1][0])
        self.assertTrue(before[2][1])

    def test_line_boundaries_and_idempotence(self):
        before = "코튼 소재입니다.\nYSolo|\n@@##\n\n레이어드 YSolo| 특징입니다."
        expected = "코튼 소재입니다.\n\n레이어드 특징입니다."
        result = desc.ocr_display_text(before)
        self.assertEqual(result, expected)
        self.assertEqual(desc.ocr_display_text(result), result)
        self.assertEqual(desc.ocr_display_text(""), "")
        self.assertEqual(desc.from_mined(None), "")



class DescriptionCleanupTest(unittest.TestCase):
    def test_empty_brackets_and_only_empty_tail_labels(self):
        for before, after in (
            ("실루엣 조절 가능 () [", "실루엣 조절 가능"),
            ("후면 포켓 디테일 FABRIC SHELL", "후면 포켓 디테일"),
            ("COTTON 100% COLOR", "COTTON 100%"),
            ("+SHOES detail", ""),
            ("Sleeve", ""),
            ("\u2800\u2800", ""),
            ("[SHELL] COTTON 100%", "[SHELL] COTTON 100%"),
            ("[23FW] [AT22F19]", "[23FW] [AT22F19]"),
            ("Color : Black / Ivory", "Color : Black / Ivory"),
            ("Cotton 100% (from.japan)", "Cotton 100% (from.japan)"),
        ):
            with self.subTest(before=before):
                self.assertEqual(desc.tidy_line(before), after)

    def test_css_and_widget_do_not_remove_feature(self):
        raw = "{ opacity: 1; transform: translateY(0); }\n총 평점 0.0 전체 리뷰 0\n부드러운 코튼 원단의 셔츠입니다."
        self.assertEqual(desc.clean(raw), "부드러운 코튼 원단의 셔츠입니다.")
        self.assertIn("{special}", desc._display_blocks("{special} 디테일"))

    def test_review_panel_boundaries(self):
        for review in ("따뜻해요 네이버 페이 구매자 2022-01-08", "섹쿠시한 브레이슬릿 [1] 한**** 조회 0 2026-06-09"):
            raw = "부드러운 코튼 셔츠입니다.\n19세 미만의 미성년자 " + review + " 다음 페이지\n소뿔 단추 디테일"
            result = desc.clean(raw, acc=True)
            self.assertNotIn("따뜻해요", result)
            self.assertNotIn("섹쿠시", result)
            self.assertIn("부드러운 코튼 셔츠입니다.", result)
            self.assertIn("소뿔 단추 디테일", result)
        text = "19세 미만의 미성년자는 이용할 수 없습니다. 다음 페이지 코튼 원단"
        self.assertIn("19세", desc._description_text(text))

    def test_sizing_cells_and_headers(self):
        for table in (
            "M Size LENGTH : 106 / WAIST : 37.5 / BOTTOM HEM : 23.5",
            "SHOULDER / CHEST / SLEEVE / LENGTH 36.5 / 42 / 17 / 48.5",
            "총장 (LENGTH) 68CM 어깨너비 (SHOULDER) 43CM 가슴단면 (CHEST) 50CM",
            "가로 28cm 세로 44cm 폭 12cm 스트랩 47-98cm",
            "HEM 23\nHEM 24",
        ):
            with self.subTest(table=table):
                self.assertEqual(desc.clean("전면 포켓 디테일\n" + table), "전면 포켓 디테일")

    def test_numeric_features_are_not_size_tables(self):
        for text in ("COTTON 47% TENCEL 49% SPANDEX 4%", "7게이지 8합 니트",
                     "99.9% UV차단 원단", "총 7개 포켓", "3 interior pockets",
                     "3 different length", "소매 길이를 조절하는 디테일", "YKK 2-WAY 지퍼", "MA-1 자켓"):
            with self.subTest(text=text):
                self.assertEqual(desc._display_blocks(text), text)

    def test_model_measurements_preserve_adjacent_fit(self):
        raw = "전면 포켓 디테일\n모델 (키 175cm) 피팅 사이즈 / 원사이즈\nHeight : 185cm Dress : 100 Bust : 87cm"
        self.assertEqual(desc.clean(raw), "전면 포켓 디테일")
        fit = "모델이 착용한 사이즈는 S이며 정 55 사이즈의 여유로운 핏입니다."
        self.assertEqual(desc._display_blocks(fit), fit)

    def test_complete_sentence_is_never_cut_after_feature_words(self):
        text = "과하지 않으면서도 시선을 끄는 볼륨감이 발등을 부드럽게 감싸며 미니멀한 구조 안에서 섬세한 포인트를 완성합니다."
        self.assertEqual(desc.split_fn(text), ([text], []))
        for text in ("오프숄더 연출 가능한 네크라인", "캐주얼한 무드로 연출 가능하며 가벼운 착용감", "소매 비죠\n및 밑단 스트링 조절 가능"):
            self.assertEqual(len(desc.split_fn(text)[0]), 1)

    def test_care_is_separated_before_classification(self):
        text = "가슴 셔링 디테일-실측 사이즈는 측정 위치에 따라 다를 수 있습니다."
        f, n = desc.split_fn(text)
        self.assertEqual(f, ["가슴 셔링 디테일"])
        self.assertEqual(len(n), 1)
        for text in ("부분 세척 권장.", "고열과 스팀은 소재 손상의 원인이 될 수 있습니다.", "원단특성상 물과 습기 마찰에 약하며, 강하게 닦으면 결이 망가질 수 있음"):
            self.assertEqual(desc.split_fn(text)[0], [])
        for text in ("변형 가능한 아우터", "드라이한 착용감", "워싱 가공으로 빈티지 질감", "소매 시보리 마감", "메리노 울 특유의 포근한 소재입니다."):
            self.assertEqual(desc.split_fn(text), ([text], []))

    def test_character_cap_uses_complete_boundaries(self):
        text = "부드러운 코튼 셔츠입니다. " * 80
        result = desc.clean(text)
        self.assertLessEqual(len(result), 2000)
        self.assertTrue(result.endswith("셔츠입니다."))
        raw = "포켓 디테일 " * 70 + "소뿔 단추로 완성합니다."
        self.assertIn("소뿔 단추로 완성합니다.", desc.clean(raw))
        self.assertGreater(len(list(desc._parts(raw))[0]), 400)
        self.assertEqual(desc.cap_complete("완성 문장.\n" + "a" * 2000), "완성 문장.")
        self.assertEqual(desc.cap_complete("a" * 2001), "")
        self.assertEqual(desc.cap_complete("7.5" + "a" * 2000), "")

    def test_washing_performance_is_a_feature(self):
        for text in ("완성 후 한 번 더 바이오 워싱을 거쳐 촉감이 부드럽고, 세탁 후 수축이 적습니다.",
                     "두 가닥을 합사한 원사를 사용해 복원력이 우수하며, 반복 착용과 세탁에도 안정적인 형태를 유지합니다."):
            self.assertIn(text, desc.clean(text))
            self.assertEqual(desc.split_fn(text), ([text], []))
        self.assertNotIn("세탁해", desc.clean("찬물에 단독 세탁해 주세요."))

    def test_exact_repeat_preserves_material_parts(self):
        self.assertEqual(desc.dedup_lines(["COTTON 100%", "원단 Cotton 100%", "안감 COTTON 100%"]),
                         ["COTTON 100%", "안감 COTTON 100%"])
        self.assertEqual(desc.dedup_lines(["겉감1 COTTON 100%", "겉감2 COTTON 100%"]),
                         ["겉감1 COTTON 100%", "겉감2 COTTON 100%"])

    def test_frequency_alone_does_not_delete_normal_features(self):
        text = "YKK 지퍼\n오버사이즈 핏\n안감 없음\n개체 차이가 있을 수 있습니다."
        self.assertEqual(desc.store_repeats([text] * 20, 15), {"개체 차이가 있을 수 있습니다."})

    def test_only_known_bilingual_equivalents_are_removed(self):
        text = ["OVERSIZED FIT", "오버사이즈 핏", "English unique pocket detail", "소매 포켓 디테일"]
        self.assertEqual(desc.dedup_bilingual(text), text[1:])
        self.assertEqual(desc.dedup_bilingual(["OVERSIZED FIT"]), ["OVERSIZED FIT"])
        self.assertEqual(desc.dedup_bilingual(["Oversized fit with pockets", "오버사이즈 핏"]),
                         ["Oversized fit with pockets", "오버사이즈 핏"])

    def test_material_only_candidate_can_use_brief_or_ocr(self):
        self.assertEqual(desc.best("COTTON 100%", spec={"상품간략설명": "전면 포켓 디테일의 셔츠"}),
                         ("COTTON 100%\n전면 포켓 디테일의 셔츠", "shop"))
        rec = {"material": {}, "description": ["자연스러운 곡선의 포켓 디테일이 특징입니다."]}
        text, source = desc.best("안감 POLYESTER 100%", rec)
        self.assertEqual(source, "ocr")
        self.assertIn("안감 POLYESTER 100%", text)
        self.assertIn("포켓 디테일", text)
        self.assertEqual(desc.best("COTTON 100%"), ("COTTON 100%", "shop"))
        good = "탄탄하고 부드러운 터치감의 소가죽으로 제작."
        self.assertTrue(desc.has_product_feature(good))
        self.assertEqual(desc.best(good, rec, acc=True), (good, "shop"))


class DescriptionConservationTest(unittest.TestCase):
    def test_metadata_does_not_eat_following_feature(self):
        raw = "Cotton 100% 제조사 : (주)에어리어인터내셔널 제조년월 : 2024.03 Made in KOREA 프리 사이즈로 한 사이즈만 출시하였으며, 여유롭게 제작되었습니다."
        self.assertIn("프리 사이즈로 한 사이즈만 출시하였으며, 여유롭게 제작되었습니다.", desc.clean(raw))
        self.assertNotIn("제조사", desc.clean(raw))

    def test_new_dimension_variants_do_not_leave_rows(self):
        for table in ("S : 허리단면 34.5 힙단면 47", "소매장 : 58cm 총장 : 52.5cm", "어깨단면 54.5 팔기장 59", "소매통 18 총장 83", "Sleeve 63 Armholes 34"):
            with self.subTest(table=table):
                self.assertEqual(desc.clean("전면 포켓 디테일\n" + table), "전면 포켓 디테일")

    def test_comma_continuation_without_clothing_keyword(self):
        raw = "포켓 디테일과 부드러운 코튼 원단 " * 20 + ", worn-in appearance Made in South Korea"
        self.assertIn("worn-in appearance", desc.clean(raw))

    def test_from_country_and_outshell_are_not_split(self):
        raw = "Outshell : Cotton 100% (From. Korea)"
        self.assertEqual(list(desc._parts(raw)), [raw])
        self.assertIn("Outshell", desc._display_blocks(raw))
        self.assertIn("(From. Korea)", desc.clean(raw))

    def test_long_whitespace_does_not_change_content(self):
        text = "전면 포켓 디테일" + " " * 20000 + "여유로운 실루엣"
        self.assertEqual(desc.clean(text), "전면 포켓 디테일\n여유로운 실루엣")

    def test_orphan_labels_in_english_and_material_parts(self):
        self.assertEqual(desc.tidy_line("Stretchy cotton blend fabric MATERIAL Shell"), "Stretchy cotton blend fabric")
        self.assertEqual(desc.tidy_line("Oversized fit 제품설명"), "Oversized fit")
        self.assertEqual(desc.tidy_line("& Care COTTON 100%"), "COTTON 100%")
        self.assertEqual(desc.tidy_line("white fabric"), "white fabric")
        self.assertEqual(desc.split_fn("* 전면 포켓 디테일"), (["* 전면 포켓 디테일"], []))

    def test_removing_heading_preserves_english_jewelry(self):
        raw = "Instruction DETAIL Black crystal accents Surgical steel chain Irregular surgical steel details Surgical steel clasp and chain"
        text = desc.clean(raw, acc=True)
        self.assertIn("Surgical steel chain", text)
        self.assertNotIn("Instruction DETAIL", text)

    def test_detail_heading_is_removed_after_feature_selection(self):
        text = "몸에 자연스럽게 감기며 넉넉한 수납력을 갖춘 플랩 백입니다."
        self.assertIn(text, desc.clean("Product Info DETAIL " + text, acc=True))
        self.assertIn("Irregular outline and sculptural form", desc.clean("Instruction DETAIL Irregular outline and sculptural form", acc=True))

    def test_material_part_label_belongs_to_next_line(self):
        text = "RAYON 80% POLYESTER 20% LINING\nRAYON 80% POLYESTER 20% COLORING 1\nPOLYESTER 100%"
        for sep in ("\n", " - ", " | ", " / ", ", "):
            out = desc.clean(text.replace("\n", sep))
            self.assertIn("LINING RAYON 80% POLYESTER 20% COLORING 1", out)

    def test_navigation_details_is_not_a_product_feature(self):
        for text in ("Details Shopping Guide", "Barcode RR261TP007BKF Details and composition"):
            self.assertEqual(desc.clean(text), "")

    def test_brand_admin_notices_do_not_return_as_features(self):
        notices = ["정품 인증을 위한 HIDDEN TAG가 부착되어 있습니다.", "지퍼폴리백 특성상 미량의 파우더 가루가 묻어 나올 수 있습니다.", "상품, 라벨, TAG 등이 분실"]
        text = "전면 브랜드 로고 자수 디테일\n" + "\n".join(notices)
        self.assertEqual(desc.store_repeats([text] * 20, 15), set(notices))

    def test_reverse_percent_material_survives_section_cleanup(self):
        text = "[ 소재 ] 바디1 90% 폴리에스터, 10% 스판덱스 [ 디테일 ]\n플라워 패턴 레깅스 쇼츠"
        result = desc.clean(text)
        self.assertIn("바디1 90% 폴리에스터, 10% 스판덱스", result)
        self.assertIn("플라워 패턴 레깅스 쇼츠", result)

    def test_order_collection_does_not_hide_garment_details(self):
        text = "오간자 원단과 벨벳 프린트가 특징인 점퍼 [Made to Order Collection] 본 제품은 주문제작상품입니다."
        f, n = desc.split_fn(desc.clean(text))
        self.assertIn("오간자 원단과 벨벳 프린트가 특징인 점퍼", f)
        self.assertNotIn("주문제작", " ".join(f))

    def test_generic_fiber_care_does_not_precede_product_features(self):
        care = "Cotton 컬러를 선명하게 유지하기 위해 겨드랑이, 네크라인, 소매 같은 부분의 얼룩을 본 세탁법에 따라 처리하십시오.\nWool 울 소재는 다른 섬유만큼 자주 세탁할 필요가 없습니다.\n대다수의 울 소재 섬유는 항균성을 띠고 있어 어느 정도는 스스로 세척됩니다.\n이를 제대로 따르지 않으면 제품에 손상이 가거나 뻣뻣해지고 수축 현상이 나타날 수 있습니다.\nLinen\n부드러운 가죽은 긁힐 수 있습니다."
        feature = "리버스 심 니트는 스코틀랜드산 울 원사로 제작된 제품입니다."
        result = desc.clean(care + "\nProduct Info DETAIL " + feature)
        self.assertIn(feature, result)
        self.assertNotIn("겨드랑이", result)
        self.assertNotIn("스스로 세척", result)
        self.assertNotIn("Linen", result)
        self.assertEqual(desc.clean("Product Info DETAIL " + care), "")
        self.assertEqual(desc.clean("Linen"), "Linen")
        self.assertNotIn("Linen", desc.clean(care + "\nLinen 세탁 방법은 안내를 확인하십시오."))
        self.assertIn("항균", desc.clean("항균 가공을 적용한 코튼 원단의 셔츠입니다."))

    def test_measurement_notice_keeps_feature_and_material_prefix(self):
        feature = "내추럴한 데일리룩에 잘 어울리는 디자인"
        text = feature + " MATERIAL 겉감 : 나일론+소가죽 안감 : 코튼 SIZE(CM) 39(W) X 27(H) X 10.5(D) 측정 위치에 따라 1~2CM 정도의 오차가 있을 수 있습니다."
        result = desc.clean(text, acc=True)
        self.assertIn(feature, result)
        self.assertIn("안감 : 코튼", result)
        self.assertNotIn("측정 위치", result)

    def test_partial_size_removal_does_not_leave_unknown_dimension_cells(self):
        for table in ("암홀 19 총길이 48", "소매통 24.5 소매부리 18.7 총기장 56", "소매부리 10.5C 목넓이 20.5CM", "M : 허리 40.5/44 / 밑위 32 / 총길이 100", "소매 길이 60.5cm 암홀 26cm", "소매넓이 16.5 총길이 46", "SLEEVE LENGTH 19 / BOTTOM WIDTH 55 / LENGTH 68", "Crotch depth 32.5 / Hem 24"):
            with self.subTest(table=table):
                self.assertEqual(desc.clean("전면 포켓 디테일\n" + table), "전면 포켓 디테일")
        self.assertEqual(desc._display_blocks("SLEEVE 3/4"), "SLEEVE 3/4")
        self.assertIn("SLEEVE 3/4", desc.clean("SLEEVE 3/4"))
        self.assertIn("SLEEVE LENGTH 3/4", desc.clean("SLEEVE LENGTH 3/4"))

    def test_inline_ui_and_guide_do_not_erase_real_features(self):
        feature = "은은한 스트라이프 원단과 YKK 지퍼로 다양한 스타일링 연출"
        self.assertIn(feature, desc.clean("상품명 SHIRT 판매가 189,000원 상품간략설명 " + feature + " 사이즈 가이드 배송"))
        self.assertIn("배색 고리 디테일", desc.clean("가죽 스트랩과 배색 고리 디테일DO NOT WASH, 오염시 닦아서 착용, 이염주의", acc=True))
        self.assertIn("링클 프리 가공", desc.clean("쿠폰 20% 상품상세 상세설명 링클 프리 가공의 셔츠 사이즈 S (90) 어깨 48 가슴 56 소매 57 배송"))
        self.assertIn("아크릴", desc.clean("DETAIL fabric 아크릴 color 그레이, 블랙 size(cm) 총장 44 발목 16 model size 167cm"))
        self.assertIn("POLYESTER 80%", desc.clean("REVIEW Text 1,000 KRW Payment information POLYESTER 80% SPAN 20% Delivery information SIZE GUIDE"))

    def test_washing_and_down_construction_are_product_traits(self):
        washing = "바이오 워싱과 덤블 워싱을 거쳐 부드러운 터치감을 더했으며, 세탁 후에도 변형이 적어 오랜 시간 안정적인 형태를 유지합니다."
        down = "고밀도 원단의 다운백 공정이 적용된 2중 구조를 통해 덕 다운의 털 빠짐을 최소화"
        for text in (washing, down):
            with self.subTest(text=text):
                self.assertEqual(desc.split_fn(desc.clean(text)), ([text], []))
        self.assertEqual(desc.split_fn("소재 특성상 털 빠짐이 있을 수 있습니다."), ([], ["소재 특성상 털 빠짐이 있을 수 있습니다."]))
        text = "Material Shell Nylon 100% relation-color1 2482,#000000 relation-color2 2483,#848484 CLOSE 경량의 원단과 내부 메쉬라이닝으로 쾌적한 착용감을 주는 햇입니다."
        self.assertIn("경량의 원단과 내부 메쉬라이닝으로 쾌적한 착용감을 주는 햇입니다.", desc.clean(text, acc=True))
        text = "크리에이터와 협업하여 제작된 후드 티셔츠 면, 폴리 혼방 소재로 세탁 케어에 용이하게 제작 크롭한 기장감으로 단품, 이너 모두 활용 가능"
        self.assertIn("협업하여 제작된 후드 티셔츠", desc.clean(text))
        self.assertIn("이너 모두 활용 가능", desc.clean(text))
        text = "쿠폰 20% 상품상세 상세설명 [DESIGNER COMMENT]캐주얼한 무드의 보더 티셔츠루즈 핏으로 편안한 착용감남, 여 모두 착용 가능한 보트넥 디자인[FABRIC]면 100%cotton 100% 사이즈 S (~90) 어깨 48 가슴 54.5 소매 58.5"
        self.assertIn("보트넥 디자인", desc.clean(text))

    def test_name_with_composition_is_more_than_name_only(self):
        self.assertFalse(desc.is_name_only("LOGO PRINTED JACKET FABRIC COTTON 100% ORIGIN KOREA", "LOGO PRINTED JACKET"))
        self.assertTrue(desc.is_name_only("LOGO PRINTED JACKET", "LOGO PRINTED JACKET"))

    def test_brand_repeats_keep_fit_but_drop_maintenance(self):
        text = "프리 사이즈로 한 사이즈만 출시하였으며, 여유롭게 제작되었습니다.\n장시간 수분에 노출 시 변형 가능성이 있습니다."
        self.assertEqual(desc.store_repeats([text] * 20, 15), {"장시간 수분에 노출 시 변형 가능성이 있습니다."})

if __name__ == "__main__":
    unittest.main()
