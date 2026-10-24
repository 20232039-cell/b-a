"""문맥별 오탐과 독립된 긍정 근거를 함께 검사한다. data/에는 쓰지 않는다."""
import unittest

import tag_items as t


class TagContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vocab = {ax: {} for ax in t.AXES}
        vocab.update({
            "silhouette": {"슬림핏": ["슬림", "slim fit"], "타이트": ["tight"], "구조적": ["structured"]},
            "length": {"미디": ["midi"], "크롭": ["crop"], "쇼츠": ["shorts"]},
            "neckline": {"카라": ["collar"], "헨리넥": ["헨리 넥"], "라운드넥": ["round neck"]},
            "pants_type": {"스웨트": ["스웨트 팬츠"], "진": ["데님", "denim"]},
            "material": {"니트": ["knit", "knitted", "니트웨어"], "데님": ["denim"],
                         "코튼": ["cotton"], "폴리에스터": ["polyester"], "캔버스": ["canvas"],
                         "다운": ["goose down"],
                         "인조가죽": ["synthetic leather"], "스판덱스": ["polyurethane", "spandex"]},
            "finish_wash": {"데미지": ["damaged"], "페이딩": ["물빠짐", "faded"],
                            "피그먼트워싱": ["피그먼트", "pigment"], "코팅": ["coating"],
                            "가먼트워싱": ["가먼트 워싱"]},
            "design_element": {"패치": ["patch"], "드레이프": ["drape"], "배색": []},
            "construction": {"포켓": ["pocket", "주머니"], "안감": ["lining"], "시보리": ["리브"]},
            "pattern": {"체크": ["check"], "스트라이프": ["stripe"]},
            "hardware": {"아일렛": ["eyelet"], "벨트": ["belt", "belted", "벨티드"],
                         "메탈": ["금속", "metal"],
                         "어드저스트": ["조절"], "단추": ["button"], "스냅": ["snap"], "스트랩": []},
            "function": {"항균": ["항균성"], "신축성": ["stretch"]},
        })
        cls.tagger = t.Tagger(vocab)

    def tag(self, body="", name="제품", category="Tops"):
        return self.tagger.tag(category, name, body, "", "ok")

    def test_medium_size_keeps_midi_length(self):
        for medium in ("미디움 사이즈", "M(미디움)", "미디엄 사이즈"):
            with self.subTest(medium=medium):
                self.assertNotIn("미디", self.tag(medium).get("length", []))
                self.assertIn("미디", self.tag(medium, "미디 스커트", "Skirts")["length"])

    def test_part_and_measurement_are_not_whole_fit(self):
        for body in ("Tight 34cm", "Structured Collar Detail", "슬림하게 잡아주는 소매와 밑단의 립 조직",
                     "측면 스트랩을 활용하여 슬림한 핏으로 연출 가능"):
            with self.subTest(body=body):
                self.assertFalse(self.tag(body).get("silhouette"))
        self.assertIn("구조적", self.tag("Structured silhouette")["silhouette"])
        self.assertIn("타이트", self.tag("Tight fit")["silhouette"])
        self.assertIn("슬림핏", self.tag("슬림한 핏")["silhouette"])

    def test_length_scale_keeps_explicit_crop(self):
        self.assertNotIn("크롭", self.tag("제품 가이드 기장 크롭 롱 원단 두께").get("length", []))
        self.assertIn("크롭", self.tag("기장 크롭 롱", "크롭 자켓")["length"])

    def test_setup_jacket_is_not_pants_length(self):
        body = "summer crop jacket 제품과 동일 소재로 셋업 연출이 가능합니다."
        self.assertNotIn("크롭", self.tag(body, "Pants", "Pants").get("length", []))
        self.assertIn("크롭", self.tag(body, "크롭 팬츠", "Pants")["length"])
        self.assertIn("크롭", self.tag(body, "Jacket", "Outerwear")["length"])

    def test_relative_midi_shorts_keep_other_length(self):
        tags = self.tag("적당한 미디 기장감", "Denim shorts", "Pants")
        self.assertNotIn("미디", tags.get("length", []))
        self.assertIn("쇼츠", tags["length"])
        self.assertIn("미디", self.tag("미디 기장감", "스커트", "Skirts")["length"])

    def test_rib_collar_keeps_ribbing_and_real_collar(self):
        tags = self.tag("Ribbed collar")
        self.assertNotIn("카라", tags.get("neckline", []))
        self.assertIn("시보리", tags["construction"])
        self.assertIn("카라", self.tag("Ribbed collar", "카라 셔츠")["neckline"])

    def test_review_table_keeps_product_text_after_pagination(self):
        review = ("리뷰 전체보기 4 카라니트에서 헨리넥도 삽니다 [1] 키: 170 몸무게: 75 "
                  "드라이 카라티는 2 사이즈 구매했어요. 리뷰 0 이**** 2026-08-17 조회 0 "
                  "첫 페이지 이전 페이지 1 다음 페이지 마지막 페이지 ")
        tags = self.tag(review + "헨리 넥 디테일, Cotton 100%", "헨리 넥 니트")
        self.assertNotIn("카라", tags.get("neckline", []))
        self.assertIn("헨리넥", tags["neckline"])
        self.assertIn("코튼", tags["material"])
        self.assertIn("카라", self.tag(review + "카라 디테일")["neckline"])

    def test_sweatshirt_is_not_sweat_pants(self):
        self.assertNotIn("스웨트", self.tag("스웨트셔츠와 매치", category="Pants").get("pants_type", []))
        self.assertIn("스웨트", self.tag("스웨트셔츠와 매치", "스웨트 팬츠", "Pants")["pants_type"])

    def test_denim_care_is_not_pants_type(self):
        for body in ("데님 세탁 시에는 찬물에 손세탁해 주세요.", "데님 소재의 특성상 잦은 세탁은 원단에 데미지를 줍니다."):
            with self.subTest(body=body):
                self.assertNotIn("진", self.tag(body, category="Pants").get("pants_type", []))
                self.assertIn("진", self.tag(body, "데님 팬츠", "Pants")["pants_type"])

    def test_hand_work_and_size_guide_are_not_knit(self):
        self.assertNotIn("니트", self.tag(name="HAND KNITTED DAMAGED DENIM SHORTS", category="Pants")["material"])
        self.assertNotIn("니트", self.tag("니트나 늘어짐이 있는 제품은 오차가 있습니다.").get("material", []))
        self.assertIn("니트", self.tag(name="Hand knitted sweater")["material"])
        self.assertIn("니트", self.tag("니트와 매치", name="니트 가디건")["material"])

    def test_trucker_shape_is_not_denim(self):
        self.assertNotIn("데님", self.tag("데님 자켓의 디테일을 재해석. Cotton 100%",
                                        "Heavy canvas trucker jacket", "Outerwear")["material"])
        self.assertIn("데님", self.tag("Cotton 100%", "Denim trucker jacket", "Outerwear")["material"])

    def test_coating_pu_keeps_real_spandex(self):
        tags = self.tag("Shell: Polyester 55% Polyurethane 45%", "Synthetic leather blouson")
        self.assertNotIn("스판덱스", tags["material"])
        self.assertIn("인조가죽", tags["material"])
        self.assertIn("스판덱스", self.tag("Polyester 95% Polyurethane 5%", "Pants", "Pants")["material"])
        self.assertIn("스판덱스", self.tag("Polyester 55% Spandex 45%", "Synthetic leather pants", "Pants")["material"])

    def test_damage_notice_keeps_separate_design(self):
        for body in ("미세한 데미지가 있을 수 있습니다.",
                     "데미지 워싱 공정이 진행되는 제품 같은 경우 편차가 있습니다."):
            with self.subTest(body=body):
                self.assertNotIn("데미지", self.tag(body).get("finish_wash", []))
                self.assertIn("데미지", self.tag(body, "데미지 진")["finish_wash"])
        self.assertNotIn("페이딩", self.tag("물빠짐 및 이염 현상이 있을 수 있어 단독 세탁 부탁드립니다.").get("finish_wash", []))
        self.assertIn("페이딩", self.tag("물빠짐 및 이염이 있을 수 있음", "Faded jeans")["finish_wash"])

    def test_pigment_coating_is_not_dyeing(self):
        for body in ("피그먼트 코팅 린넨", "Pigment coating linen"):
            with self.subTest(body=body):
                self.assertNotIn("피그먼트워싱", self.tag(body).get("finish_wash", []))
        self.assertIn("피그먼트워싱", self.tag("피그먼트 다잉")["finish_wash"])

    def test_patch_pocket_keeps_pocket_and_independent_patch(self):
        for body in ("패치포켓", "패치 포켓", "Patch pockets"):
            with self.subTest(body=body):
                tags = self.tag(body)
                self.assertNotIn("패치", tags.get("design_element", []))
                self.assertIn("포켓", tags["construction"])
                self.assertIn("패치", self.tag(body + " / 로고 patch")["design_element"])

    def test_pocket_lining_keeps_body_lining(self):
        for body in ("주머니 안감: Cotton 100%", "Pocket lining: Polyester 100%"):
            with self.subTest(body=body):
                tags = self.tag(body)
                self.assertNotIn("안감", tags.get("construction", []))
                self.assertIn("포켓", tags["construction"])
                self.assertTrue(tags["material"])
                self.assertIn("안감", self.tag(body + " / Body lining: Cotton 100%")["construction"])

    def test_fabric_properties_are_not_design(self):
        self.assertNotIn("드레이프", self.tag("드레이프성을 높인 원단").get("design_element", []))
        self.assertIn("드레이프", self.tag("드레이프 디테일")["design_element"])
        self.assertNotIn("배색", self.tag("배색감 Cotton 60%, Tencel 40%").get("design_element", []))
        self.assertIn("배색", self.tag("배색 소재 사용")["design_element"])

    def test_explicit_extra_product_keeps_own_product_after_price(self):
        body = "추가구성상품 WOMAN 벨티드 슬랙스 79,000 본품 카라 티셔츠"
        self.assertNotIn("벨트", self.tag(body).get("hardware", []))
        self.assertIn("카라", self.tag(body)["neckline"])
        self.assertIn("벨트", self.tag(body + " / 본품 벨트 포함")["hardware"])
        many = "추가구성상품 보스턴백 69,000 벨티드 투턱 슬랙스 79,000 본품 셔츠 52,000"
        self.assertNotIn("벨트", self.tag(many).get("hardware", []))

    def test_check_point_keeps_real_check(self):
        self.assertNotIn("체크", self.tag("CHECK POINT 두께감 얇음").get("pattern", []))
        self.assertIn("체크", self.tag("CHECK POINT 두께감 얇음", "Check shirt")["pattern"])

    def test_eyelet_fabric_keeps_metal_eyelet(self):
        self.assertNotIn("아일렛", self.tag("플라워 아일렛 소재").get("hardware", []))
        self.assertIn("아일렛", self.tag("플라워 아일렛 소재 / 금속 아일렛 장식")["hardware"])

    def test_care_and_display_notices_are_not_hardware(self):
        for body in ("모니터의 밝기를 조절해 주세요.",
                     "단추와 스냅 버튼은 모두 체결되지 않은 상태에서 케어해주십시오.",
                     "스냅 버튼과 단추를 체결한 후 세탁하는 경우 이탈할 수 있습니다.",
                     "Belt Loops"):
            with self.subTest(body=body):
                self.assertFalse(self.tag(body).get("hardware"))
        self.assertNotIn("어드저스트", self.tag("허리 조절 스트링").get("hardware", []))
        self.assertIn("단추", self.tag("메탈 button 여밈")["hardware"])
        self.assertIn("벨트", self.tag("Belt Loops", "Belted pants", "Pants")["hardware"])
        tags = self.tag("기본 구성품 분실시 불가합니다. (금속장식, 스트랩, 에코백 등)")
        self.assertNotIn("스트랩", tags.get("hardware", []))
        self.assertNotIn("메탈", tags.get("hardware", []))


class E3RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        cls.tagger = t.Tagger(json.loads(t.VOCAB.read_text(encoding="utf-8")))

    def tag(self, body="", name="제품", category="Tops"):
        return self.tagger.tag(category, name, body, "", "ok")

    def test_adjustment_requires_hardware_noun(self):
        for body in ("허리 스트링으로 핏 조절 가능", "소매 단추 디테일로 폭 조절 가능",
                     "기장 조절 원할 시 주문 전 연락 바랍니다.", "안감은 체온조절 기능"):
            self.assertNotIn("어드저스트", self.tag(body).get("hardware", []))
        for body in ("허리 어드저스트를 적용", "허리 사이즈 조절이 가능한 사이드 비조 디테일",
                     "back cinch", "metal adjuster", "버클로 허리 둘레 조절",
                     "허리 양쪽의 어져스터로 사이즈 조절이 가능", "NIFCO 스토퍼. 밑단 스트링으로 조절",
                     "뒤쪽 디링 스트랩으로 원하는 핏 조절 가능"):
            self.assertIn("어드저스트", self.tag(body)["hardware"])
        self.assertIn("스트링", self.tag("허리 스트링으로 조절")["hardware"])
        self.assertIn("단추", self.tag("소매 단추로 조절")["hardware"])

    def test_hardware_fiber_packaging_and_notices(self):
        for body in ("골드 메탈사가 직조된 트위드", "Metal yarn", "지퍼폴리백 특성상 파우더 가루",
                     "프린팅/금속 장식/자수의 경우 사람이 손으로 작업하는 특성상 0.5cm의 오차"):
            self.assertFalse(self.tag(body).get("hardware"))
            self.assertIn("메탈", self.tag(body + "\n메탈 장식 단추")["hardware"])
            self.assertIn("지퍼", self.tag(body + "\n히든지퍼 여밈")["hardware"])

    def test_measurement_closure_notice_is_limited_to_tees(self):
        body = "사이즈는 단추 및 지퍼를 모두 채우고 제품을 평평한 바닥에 놓고 측정한 사이즈입니다."
        self.assertFalse(self.tag(body, "LINE DETAIL WOOL LONG SLEEVE").get("hardware"))
        self.assertIn("단추", self.tag(body, "STRIPE CARDIGAN")["hardware"])
        self.assertIn("단추", self.tag(body, "STRIPE COLLAR T-SHIRT")["hardware"])
        self.assertIn("단추", self.tag(body, "LOGO SWEATSHIRT")["hardware"])
        self.assertIn("지퍼", self.tag(body + "\n옆선 지퍼 여밈", "T-shirt")["hardware"])
        self.assertIn("지퍼", self.tag("투웨이 지퍼 여밈. 지퍼폴리백 파우더 안내")["hardware"])

    def test_damage_warning_keeps_independent_design(self):
        for body in ("고온 건조기 사용은 소재에 데미지를 입히기 때문에 손상이 빠릅니다.",
                     "더 에이징 되는 것을 원치 않는 고객은 자연건조를 권장합니다.",
                     "Signs of wear, use, or damage.", "Please be careful as ironing can damage the mohair."):
            self.assertFalse(set(self.tag(body).get("finish_wash", [])) & {"데미지", "에이징"})
            self.assertIn("데미지", self.tag(body + "\n데미지 디테일을 적용한 팬츠")["finish_wash"])
        self.assertIn("에이징", self.tag(name="Aged denim")["finish_wash"])

    def test_pigment_finish_keeps_real_dyeing(self):
        self.assertNotIn("피그먼트워싱", self.tag("Calfskin-Chrome tanned pigment finish1mm").get("finish_wash", []))
        self.assertIn("피그먼트워싱", self.tag("pigment finish\nPigment dyed cotton")["finish_wash"])

    def test_selected_and_unreadable_attribute_tables(self):
        for body in ("신축성 있음 약간 있음 없음", "신축성 -0--6ㅎ--@__\n신축성있음 약간있음 신축성없음",
                     "신축성 ows ㅁ 약간 있음 mcs", "신축성 @ 이음 © 약간 O 많음",
                     "STRETCH Stretchy Light Stretch Non-Stretch"):
            self.assertNotIn("신축성", self.tag(body).get("function", []))
            self.assertIn("신축성", self.tag(body + "\n신축성이 뛰어난 원단")["function"])
        self.assertNotIn("신축성", self.tag("신축성 □ 있음 □ 약간 있음 ■ 없음").get("function", []))
        self.assertIn("신축성", self.tag("신축성 ■ 있음 □ 약간 있음 □ 없음")["function"])
        self.assertIn("신축성", self.tag("신축성 ■ 있음")["function"])
        self.assertNotIn("신축성", self.tag("신축성 ■ 있음 ■ 없음").get("function", []))
        self.assertIn("단추", self.tag("신축성 □ 있음 ■ 없음, 메탈 단추 디자인")["hardware"])
        self.assertIn("신축성", self.tag("신축성을 더해 안정적인 착용감 제공 - 플랩 포켓 포인트")["function"])
        self.assertIn("포켓", self.tag("신축성을 더해 안정적인 착용감 제공 - 플랩 포켓 포인트")["construction"])
        self.assertIn("로고플레이", self.tag("Fitted shorts made from stretch waffle fabric, featuring an all-over CRANK logo and star print.")["design_element"])
        self.assertIn("메탈", self.tag("Wool see-through fabric U-neck line Small metal logo ACRYLIC 75% NYLON 10% WOOL 15%")["hardware"])
        self.assertIn("멜란지", self.tag("신축성이 좋아 착용하기 편함 - 단독, 이너로 입기 좋음 - 멜란지 5컬러")["pattern"])
        self.assertIn("신축성", self.tag("신축성 ( ) 없음 (_) 약간 @us")["function"])
        self.assertNotIn("신축성", self.tag("신축성 @ 없음 (_) 약간 O 많음").get("function", []))
        self.assertNotIn("신축성", self.tag("신축성 @ 이음 © 약간 O 많음").get("function", []))
        self.assertIn("밴딩", self.tag("비침 있음 -카라 레이스 포인트 -소매, 허리 밴딩으로 체형에 맞는 착용 가능 -밝은 컬러는 약간의 오염")["construction"])

    def test_transparency_scale_is_not_sheer_material(self):
        for body in ("SEE-THROUGH None Slightly Sheer See-Through", "Sheerness Slightly Sheer"):
            self.assertNotIn("시어", self.tag(body).get("material", []))
            self.assertIn("시어", self.tag(body, "Sheer blouse")["material"])
        self.assertNotIn("시어", self.tag("비침 □ 있음 ■ 없음").get("material", []))
        self.assertIn("시어", self.tag("비침 ■ 있음 □ 없음")["material"])

    def test_non_stretch_is_not_positive_even_with_spandex(self):
        for body, name in (("Non-stretch denim skirt", "Skirt"), ("Cotton 97% Spandex 3%\n신축성 없음", "Skirt"),
                           ("", "Non-stretch skirt")):
            self.assertNotIn("신축성", self.tag(body, name, "Skirts").get("function", []))
        self.assertIn("신축성", self.tag("Non-stretch label\nStretchy fabric")["function"])
        self.assertIn("신축성", self.tag("Cotton 95% Spandex 5%")["function"])

    def test_no_collar_does_not_imply_collar(self):
        tags = self.tag("노카라 디자인, 탈부착 후드 머플러 구성")
        self.assertEqual(tags["neckline"], ["노카라", "후드"])
        self.assertIn("카라", self.tag("노카라 디자인, 별도의 탈착 숄카라 구성")["neckline"])

    def test_imitation_denim_and_velvet(self):
        tags = self.tag("데님 TROMPE L`OEIL 프린트의 콤비 카고 팬츠", "FAUX-DENIM CARGO PANTS (DENIM/BLACK)", "Denim")
        self.assertNotIn("진", tags.get("pants_type", []))
        self.assertIn("카고", tags["pants_type"])
        self.assertNotIn("진", self.tag("특히 피그먼트 소재 및 데님의 경우 소재 특성상 이염과 물 빠짐이 있습니다.", "VELVET PANTS", "Pants").get("pants_type", []))
        self.assertNotIn("진", self.tag("", "VELVET PANTS", "Denim").get("pants_type", []))
        self.assertIn("진", self.tag("", "Velvet trim denim pants", "Denim")["pants_type"])
        self.assertIn("진", self.tag("", "Flock velvet wide-leg jeans", "Pants")["pants_type"])
        self.assertIn("진", self.tag("", "JEANS", "Denim")["pants_type"])

    def test_body_rib_keeps_actual_rib_trim(self):
        for body in ("탄탄한 리브드 원단의 슬리브리스", "리브드 저지 원단", "Rib fabric"):
            self.assertNotIn("시보리", self.tag(body).get("construction", []))
            self.assertIn("시보리", self.tag(body + "\n소매와 밑단의 립 조직")["construction"])
        self.assertIn("시보리", self.tag("Ribbed cuffs and hem")["construction"])
        self.assertIn("시보리", self.tag("소매와 네크 부분의 리브드 레이어링 패턴")["construction"])
        self.assertIn("시보리", self.tag("넓은 리브드 직조 네크라인")["construction"])
        self.assertIn("시보리", self.tag("리브드 져지원단, 넥 배색 립지 디테일")["construction"])
        self.assertIn("시보리", self.tag("후드 베스트 리브드 헴")["construction"])
        self.assertIn("시보리", self.tag("리브드 가디건. 소맷단과 밑단을 잡아주는 립 피니시")["construction"])
        self.assertNotIn("랩", self.tag(name="티셔츠 (올리브드랩)").get("construction", []))

    def test_raw_edge_is_not_opening(self):
        body = "셔츠 끝단에 적용된 컷 아웃 디테일\nRaw edge at cuffs"
        self.assertNotIn("컷아웃", self.tag(body).get("construction", []))
        self.assertIn("컷아웃", self.tag(body, "Cut-Out Strap Knit Top")["construction"])

    def test_partner_product_keeps_current_product_evidence(self):
        for body in ("와이드 프렌치 스웻 팬츠와 SET-UP으로 착용 가능합니다.",
                     "크롭한 기장감으로 와이드 팬츠와 함께 매치해 연출",
                     "Black 색상은 [SIST9051 벨티드 롱 스커트] 제품과 세트 착용이 가능",
                     "신치백 데님 등 이얼즈어고의 다양한 팬츠 제품들과의 상성을 고려"):
            tags=self.tag(body)
            self.assertNotIn("와이드", tags.get("silhouette", []))
            self.assertNotIn("벨트", tags.get("hardware", []))
            self.assertNotIn("어드저스트", tags.get("hardware", []))
        self.assertIn("와이드", self.tag("와이드 팬츠와 세트", "와이드 팬츠", "Pants")["silhouette"])
        self.assertIn("벨트", self.tag("[벨티드 스커트] 제품과 세트. 본품 벨트 포함")["hardware"])
        self.assertIn("슬림핏", self.tag("슬림핏 실루엣으로 데님과 매치 용이")["silhouette"])

    def test_styling_sleeves_are_not_own_sleeves(self):
        body = "민소매 티셔츠, 반팔 티셔츠 제품과 함께 레이어드 연출도 추천해드립니다."
        self.assertNotIn("반팔", self.tag(body).get("sleeve_length", []))
        self.assertIn("반팔", self.tag(body, "반팔 티셔츠")["sleeve_length"])
        self.assertNotIn("반팔", self.tag(name="CARPENTER SHORTS INSERTED COAT").get("sleeve_length", []))

    def test_loose_skirt_and_model_hair(self):
        self.assertNotIn("와이드", self.tag("여유로운 실루엣", "Skirt", "Skirts").get("silhouette", []))
        self.assertIn("와이드", self.tag("여유로운 실루엣", "Pants", "Pants")["silhouette"])
        self.assertNotIn("스트레이트", self.tag("Model: Dasha (Yellow straight hair)").get("silhouette", []))
        self.assertIn("스트레이트", self.tag("Yellow straight hair", "Straight pants", "Pants")["silhouette"])
        self.assertNotIn("구조적", self.tag("구조적인 완성도를 높임").get("silhouette", []))
        self.assertIn("구조적", self.tag("구조적인 실루엣")["silhouette"])

    def test_accessory_pattern_and_print(self):
        self.assertNotIn("헤링본", self.tag("단추 밑 부분에 헤링본 테이프를 추가").get("pattern", []))
        self.assertIn("헤링본", self.tag("헤링본 테이프 사용", "Herringbone coat")["pattern"])
        self.assertNotIn("체크", self.tag("정확한 사이즈 체크는 실측을 비교").get("pattern", []))
        self.assertNotIn("단색", self.tag(name="풀 프린트 메쉬 티셔츠").get("pattern", []))
        self.assertIn("멜란지", self.tag(name="Melange knit")["pattern"])

    def test_yarn_certification_and_pocket_layer(self):
        for body,value in (("하드 트위스트 코튼 원사를 사용", "트위스트"),
                           ("울마크 로고가 표시된 제품은 엄격한 테스트를 거친 제품", "로고플레이"),
                           ("Double-layered pocketing for added reinforcement", "레이어드"),
                           ("짙은 컬러 및 배색이 들어간 제품은 이염이 발생할 수 있으므로 단독 세탁 권장", "배색")):
            self.assertNotIn(value, self.tag(body).get("design_element", []))
            self.assertIn(value, self.tag(body + "\n" + value + " 디자인")["design_element"])

    def test_review_block_keeps_product_after_boundary(self):
        body = "Review 6 색도 고급스럽고 같이 산 니트랑 잘 어울립니다 한혜수 | 25.09.20 5 만족 김도은 | 25.02.08 write all 1 2\n본품 Cotton 100%, 카라 디자인"
        tags = self.tag(body)
        self.assertNotIn("니트", tags.get("material", []))
        self.assertIn("코튼", tags["material"])
        self.assertIn("카라", tags["neckline"])

    def test_unbracketed_review_does_not_drop_independent_true_knit(self):
        body = "Review 1 가을 겨울에 단품으로 좋은 니트에요. 김지영 | 26.01.18 write all 1"
        self.assertIn("니트", self.tag(body)["material"])

    def test_general_wool_care_keeps_actual_antibacterial_claim(self):
        body = "대다수의 울 소재 섬유는 항균성을 띠고 있어 스스로 세척됩니다."
        self.assertNotIn("항균", self.tag(body).get("function", []))
        self.assertIn("항균", self.tag(body + " 이 제품은 항균 가공 원단입니다.")["function"])

    def test_unreadable_attribute_choices_keep_single_claim(self):
        body = "신축성 Meas [약간있음 Oss 아주좋음\n안 감 O없음 O기모 O누빔"
        self.assertNotIn("신축성", self.tag(body).get("function", []))
        self.assertNotIn("안감", self.tag(body).get("construction", []))
        self.assertIn("신축성", self.tag(body + "\n신축성 있음")["function"])
        self.assertIn("안감", self.tag(body + "\n안감 있음")["construction"])

    def test_app_download_is_not_down_insulation(self):
        body = "정품인증 APP을 다운받는다. 다운로드 후 실행한다."
        self.assertNotIn("보온성", self.tag(body).get("function", []))
        self.assertIn("다운", self.tag(body, "구스 다운 자켓")["material"])
        self.assertIn("보온성", self.tag(body, "구스 다운 자켓")["function"])

    def test_brand_story_keeps_product_before_and_after(self):
        story = ("The 2655 sample collection describes a dream.\nLayered details.\n"
                 "Sample is a premium designer brand.\nOur graphic artwork.\n"
                 "Each piece in our collection embodies elegance and innovation.")
        self.assertNotIn("graphic", t.strip_brand_story(story).lower())
        self.assertIn("Cotton 100%", t.strip_brand_story("Cotton 100%\n" + story + "\n카라 디테일"))
        self.assertIn("카라 디테일", t.strip_brand_story("Cotton 100%\n" + story + "\n카라 디테일"))
        self.assertEqual(t.strip_brand_story(story.replace("Layered details", "Cotton 100%")),
                         story.replace("Layered details", "Cotton 100%"))


if __name__ == "__main__":
    unittest.main()
