import unittest

import wear_feel


class SeasonOfTest(unittest.TestCase):
    def test_store_text(self):
        self.assertEqual(wear_feel.season_of("간절기에 활용하기 좋은 간절기 아이템입니다."), ["봄", "가을"])
        self.assertEqual(wear_feel.season_of("겨울 시즌 깊이 있는 스타일을 완성해 줄 자켓"), ["겨울"])

    def test_review_is_not_store_value(self):
        # steady-everywear-1557 — 상세 글에 섞인 후기
        txt = ("리뷰 0 김**** 2026-09-20 조회 0 0 6 얇은 롱슬 [1] 키: 172 몸무게: 73 사이즈: 1 평소 사이즈:95 or 100"
               "얇고 사진보다 찐한 올리브색이고 간절기에 아주 잘어울리고단품으로 추천 리뷰 0 김****")
        self.assertEqual(wear_feel.season_of(txt), [])

    def test_range(self):
        self.assertEqual(wear_feel.season_of("봄부터 여름까지 착용 가능한 시원한 소재"), ["봄", "여름"])
        self.assertEqual(wear_feel.season_of("가을~겨울 시즌 아우터"), ["가을", "겨울"])
        self.assertEqual(wear_feel.season_of("가을부터 봄까지 입기 좋은 니트"), ["봄", "가을", "겨울"])

    def test_feel_skips_body_weight(self):
        self.assertNotIn("무게", wear_feel.feel_of("키: 175 몸무게: 68 사이즈: 2 가볍고 좋아요"))
        self.assertEqual(wear_feel.feel_of("1808의 가벼운 중량감이 느껴지며").get("무게"), "가벼움")



class FeelOfTest(unittest.TestCase):
    """2026-11-09 코덱스 검수 300벌에서 나온 틀린 꼴."""
    f = staticmethod(wear_feel.feel_of)

    def test_pre_word_must_touch_key(self):
        # 「좋은」이 몇 자 앞 다른 말을 꾸미면 신축성 값이 아니다(PRE 괄호 버그)
        self.assertEqual(self.f("활용하기 좋은 셔츠 신축성 없음 비침 중간 두께감 얇음").get("신축성"), "없음")
        self.assertEqual(self.f("신축성 중간 비침 중간 두께감 얇음").get("두께"), "얇음")
        self.assertEqual(self.f("은은한 광택감이 매력적인 원단").get("광택"), "약간")

    def test_stretch_of_a_part(self):
        self.assertNotIn("신축성", self.f("신축성 있는 이밴드가 내장되어 있는 소매 커프스 사양"))
        self.assertNotIn("신축성", self.f("피부에 편안한 원단과 신축성있는 죠거밴딩"))
        self.assertNotIn("신축성", self.f("소매와 밑단을 신축성이 뛰어난 커버 스티치 봉제 방식으로 마감"))
        self.assertEqual(self.f("신축성 좋음 / 두께 보통").get("신축성"), "좋음")

    def test_sheer_degree(self):
        self.assertEqual(self.f("약간의 비침이 있는 밀도의 소재").get("비침"), "약간")
        self.assertNotIn("비침", self.f("퓨어 화이트 덕 다운으로 비침 현상을 최소화하였으며"))
        self.assertNotIn("비침", self.f("밝은 컬러감으로 비침이 없지 않아 속바지를 권장"))

    def test_other_context(self):
        self.assertNotIn("신축성", self.f("모공이 작아 촉감이 곱고 탄성과 내구성이 뛰어납니다"))
        self.assertNotIn("무게", self.f("안감으로는 겉감보다 조금 더 무게감이 있는 소재로"))
        self.assertNotIn("광택", self.f("버튼을 사용해 은은한 광택과 고급스러운 디테일"))
        self.assertEqual(self.f("신축성 낮음 / 두께 보통").get("신축성"), "약간")


if __name__ == "__main__":
    unittest.main()
