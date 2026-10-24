"""대표색 배색·꾸밈말·판매 옵션 및 태그 색 출처 회귀 검사. 데이터에는 쓰지 않는다."""
import unittest

import crawl_cafe24 as c
import export_app_data as app
import tag_items as t


class ColorExtractionTest(unittest.TestCase):
    def test_report_compound_names(self):
        self.assertEqual(c.pick_color('ACTIF BASIC TSHIRT ALUMNI EMBLEM_CREAM INDIGO PINK', '', options=['F']), '크림')
        self.assertEqual(c.pick_color('SLUB COTTON TSHIRT NEZ TOUT ROSE 01_WHITE INDIPINK', '', options=['F']), '화이트')
        self.assertEqual(c.match_color('WHITE PALEPINK'), '화이트')
        self.assertEqual(c.match_color('WHITE SOFT BLUE'), '화이트')
        self.assertEqual(c.match_color('OFF WHITE SKY BLUE'), '화이트')

    def test_product_line_is_not_first_color(self):
        self.assertEqual(c.pick_color('내추럴 러브 심볼 티셔츠 네이비', ''), '네이비')
        self.assertEqual(c.pick_color('CARBON BACKPACK aaa512u(BLACK)', ''), '블랙')
        self.assertEqual(c.pick_color('Cloud Boucle Sweater Soft Pink', ''), '핑크')
        self.assertEqual(c.match_color('MULTI WAY TOP IN NAVY'), '네이비')

    def test_golden_and_modifiers(self):
        self.assertEqual(c.pick_color('[Mmlg] ESSENTIAL MMLG SWEAT (GOLDEN CAMEL)', ''), '카멜')
        for modifier in ('LIGHT', 'DEEP', 'DARK', 'SOFT'):
            with self.subTest(modifier=modifier):
                self.assertEqual(c.match_color(f'Cloud Sweater {modifier} PINK'), '핑크')
        self.assertEqual(c.match_color('Sweater DUSTY PINK'), '핑크')
        for name in ('GOLDEN', 'GOLDEN NECKLACE', 'GOLD RING'):
            self.assertEqual(c.match_color(name), '골드')

    def test_multiple_options_are_preserved(self):
        self.assertEqual(c.pick_color('Shirt [2color]', '', options=['Black', 'White', 'S', 'M']), '블랙·화이트')
        self.assertEqual(c.pick_color('Shirt Black/White', ''), '블랙')
        self.assertEqual(c.pick_color('Shirt Black/White', '', options=['Black', 'White']), '블랙·화이트')
        self.assertEqual(c.pick_color('Shirt', '', options=['Black', 'White']), '블랙·화이트')
        self.assertEqual(c.match_color('Black/White'), '블랙')
        self.assertEqual(c.pick_color('멀티컬러 니트_2 COLOR', '', options=['민트', 'M', 'L']), '멀티')
        self.assertEqual(c.pick_color('MULTI STRIPE BELL KNIT MUFFLER (3color)', '', options=['IVORY', 'BLACK']), '멀티')
        options = ['Cream', 'Grey', 'Olive', 'Taupe', 'Charcoal', 'Sand Melange', 'Indigo']
        self.assertEqual(c.pick_color('WOOL MUFFLER - 7 COLOR', '', options=options),
                         '크림·그레이·올리브·토프·차콜·샌드·인디고')
        self.assertEqual(c.pick_color('MUFFLER', '', options=options),
                         '크림·그레이·올리브·토프·차콜·샌드·인디고')

    def test_blended_shades_and_melange_keep_existing_color(self):
        for name, expected in [('WHITE MELANGE', '그레이'), ('HEATHER CHARCOAL', '차콜'),
                               ('BLUE GREEN', '그린'), ('PEACH IVORY', '아이보리'),
                               ('Vintage ice blue', '블루'), ('Dusty Blue Grey', '그레이'),
                               ('Cream Yellow', '옐로우'), ('ECRU WHITE', '화이트')]:
            self.assertEqual(c.match_color(name), expected, name)
        self.assertEqual(c.pick_color('사이드 패널 믹스드 하프 티셔츠 [크림 블랙]', ''), '블랙')
        self.assertEqual(c.pick_color('Heritage Stripe Wool Muffler Cream Black', ''), '블랙')
        self.assertEqual(c.pick_color('Washed Ma-1 Reversible Flight Jacket, Khaki/Dark Navy', '', options=['M', 'L']), '카키')
        self.assertEqual(c.pick_color('DENIM & KNIT REVERSIBLE MUFFLER - IVORY/LIGHT DENIM', '', options=['F']), '아이보리')

    def test_colored_fabric_is_not_automatically_grey(self):
        for name, expected in [('GREEN MELANGE', '그린'), ('HEATHER BROWN', '브라운'),
                               ('Heather S/S Tee Brown', '브라운'),
                               ('TAUPE HEATHER CREAM', '토프'), ('MELANGE NAVY', '네이비')]:
            with self.subTest(name=name):
                self.assertEqual(c.match_color(name), expected)
        for name in ('MELANGE', 'HEATHER', 'MELANGE GREY', '3% MELANGE GRAY',
                     'WHITE MELANGE', 'WHITE HEATHER'):
            self.assertEqual(c.match_color(name), '그레이', name)
        # 별개 판매색·배색의 MELANGE는 실제 회색이다.
        self.assertEqual(c.pick_color('TEE (BLACK / MELANGE)', ''), '그레이')
        self.assertEqual(c.match_color('MELANGE&PINK'), '그레이')
        self.assertEqual(c.pick_color('TEE [2COLOR]', '', options=['Melange', 'Brown']), '그레이·브라운')
        self.assertEqual(c.pick_color('LEMON T-Shirts(레몬)_MELANGE', ''), '그레이')
        self.assertEqual(c.pick_color('NICOLE ROSE SHIRRING TOP_MELANGE', ''), '그레이')
        self.assertEqual(c.match_color('WHITE MELANGE PINK'), '그레이')
        self.assertEqual(c.pick_color('CLOUD HALF KNIT CREWNECK - OLIVE MELANGE', ''), '올리브')
        self.assertEqual(c.pick_color('SWEATSHIRT RAGLAN DDANJI_HEATHER GREEN', ''), '그레이')
        self.assertEqual(c.pick_color('SWEATSHIRT CLASSIQUE NEEDLEWORK_HEATHER PINK', ''), '그레이')
        self.assertEqual(c.pick_color('Stock Crewneck Sweater Black Melange', ''), '그레이')
        self.assertEqual(c.match_color('MELANGE BLACK'), '블랙')

    def test_processing_and_natural_color(self):
        for name, expected in [('OG Stone washed pocket tee _ navy', '네이비'),
                               ('CREST STONE WASHED CHINO PANTS / Navy', '네이비'),
                               ('STONE-WASHING TEE RED', '레드'),
                               ('Natural curved denim pant W/Navy [N6ADPT01]', '네이비'),
                               ('Natural fluid flared skirt White [N6MSK01]', '화이트')]:
            self.assertEqual(c.pick_color(name, ''), expected, name)
        for name in ('STONE', 'STONE GRAY', 'Stone Grey Merely Jogger Pants'):
            self.assertEqual(c.match_color(name), '스톤', name)
        for name in ('NATURAL', 'Natural Linen', 'TOTE NATURAL', '내추럴'):
            self.assertEqual(c.match_color(name), '내추럴', name)
        self.assertEqual(c.field_color('STONE WASHED'), '스톤')
        self.assertEqual(c.pick_color('DENIM COIN POCKET', '', options=[
            'STONE WASHED', 'WASHED BLACK', 'NON-WASHED BLACK']), '스톤·블랙')

    def test_metal_decorations_keep_body_color(self):
        for name in ('플럼프 스퀘어 카우 레더 벨트 [SILVER BLACK]',
                     'DOUBLE RIBBON BASIC FIT T SHIRT BLACK SILVER',
                     '[X MUUT] MM2 silver logo round square eyewear SHINY BLACK / BLACK'):
            self.assertEqual(c.pick_color(name, ''), '블랙', name)
        self.assertEqual(c.pick_color('Leather Belt [GOLD BROWN]', ''), '브라운')
        for name in ('SILVER', 'SILVER BELT', 'GOLD BELT', 'SILVER LOGO RING',
                     'RIBBON SHIRT SILVER', 'SNEAKERS SILVER BLACK', 'SILVER GRAY'):
            expected = '골드' if name == 'GOLD BELT' else '실버'
            self.assertEqual(c.pick_color(name, ''), expected, name)
        self.assertEqual(c.pick_color('silver logo eyewear TORTOISE / ANTI-BLUE LIGHT', ''), '실버')

    def test_verified_body_and_product_line_phrases(self):
        for name, expected in [('KNIT NAVY CREAM_UDSW6B129N2', '네이비'),
                               ('CARDIGAN NAVY CREAM_UDSW5B321N2', '네이비'),
                               ('JOGGER_PINK INDIGO PINK', '핑크'),
                               ('Mint Choco Check Shirt_Ice Mint', '민트')]:
            self.assertEqual(c.pick_color(name, ''), expected, name)
        self.assertEqual(c.pick_color('KNIT NAVY CREAM [2COLOR]', '', options=['Navy', 'Cream']), '네이비·크림')
        self.assertEqual(c.pick_color('MINT CHOCO', ''), '초코')
        self.assertEqual(c.pick_color('CHOCOLATE SHIRT', ''), '브라운')
        self.assertEqual(c.pick_color('SOFT BOSTON_white+grey two-tone cracked', ''), '화이트')

    def test_typo_is_limited_to_color_fields(self):
        self.assertEqual(c.field_color('KHAKHI'), '카키')
        self.assertEqual(c.field_color('KHAKHI-M'), '카키')
        self.assertEqual(c.pick_color('SYSTEM CARGO BERMUDA', '', options=['COATED', 'KHAKHI']), '카키')
        self.assertEqual(c.pick_color('Shirt', '', {'색상': 'KHAKHI'}), '카키')
        self.assertEqual(c.match_color('KHAKHI GRAPHIC TEE'), '')
        self.assertEqual(c.match_color('Frosted Almond'), '')
        self.assertEqual(c.field_color('COATED'), '')

    def test_verified_alias_keeps_other_selling_colors(self):
        self.assertEqual(c.field_color('Burntred'), '레드')
        self.assertEqual(c.pick_color('릴라스 가죽 벨트_2컬러', '', options=['Black', 'Burntred']), '블랙·레드')
        self.assertEqual(c.field_color('Frosted Almond'), '')
        self.assertEqual(c.field_color('multicolor'), '')

    def test_unrelated_spec_color_is_excluded(self):
        spec = {'상품연관색상정보': 'Black Sand Olive', 'colorchip_1': 'Black',
                '실제 색상 질문': 'Black', '입고 예정 있나요 올리브컬러': 'Olive',
                '색상': 'Sand', '소재': 'Cotton 100%'}
        body, color = t.spec_texts(spec)
        self.assertEqual(color, 'Sand')
        self.assertIn('Cotton 100%', body)
        self.assertEqual(t.spec_texts({'상품연관색상정보': 'Black Sand Olive'})[1], '')
        related = {'상품연관색상정보': '1837,Sand,1/1836,Olive,2/1835,Black,3'}
        self.assertEqual(t.spec_texts(related, 1837)[1], 'Sand')
        self.assertEqual(t.spec_texts(related, 183)[1], '')
        self.assertEqual(t.spec_texts({'상품연관색상정보': '3814,M.Grey/White,1/3815,Navy/White,2'}, 3814)[1], 'M.Grey/White')
        for key in ('COLOR', 'Color', 'colour', '컬러', 'color name', 'COLOR/SIZE', '색상/사이즈', 'color / 색상'):
            self.assertEqual(t.spec_texts({key: 'White'})[1], 'White', key)

    def test_hatching_room_color_reaches_app(self):
        row = {'brand_slug': 'hatching-room', 'product_no': '1837', 'name': 'Vase Neck Knit Sand',
               'source_url': 'https://hatchingroom.com/product/vase-neck-knit-sand/1837/',
               'representative_color': '샌드', 'category_code': 'tops', 'category': 'Knitwear',
               'price': '100000', 'status': 'ON_SALE'}
        spec = {'상품연관색상정보': '1837,Sand,1/1836,Olive,2/1835,Black,3'}
        color_text = ' '.join([row['representative_color'], t.spec_texts(spec, row['product_no'])[1]])
        tagger = t.Tagger({ax: {} for ax in t.AXES})
        tagged = tagger.tag('Knitwear', row['name'], '', color_text, 'ok')
        self.assertEqual(tagged['color'], ['샌드'])
        tags = {row['source_url']: {'tags': tagged, 'mat': []}}
        index = app.thin_row(row, tags, {'hatching-room': 0}, {'tops': 0}, {})
        detail = app.full(row, tags, {}, {})
        self.assertEqual(index['co'], '샌드')
        self.assertEqual(detail['color_name'], '샌드')
        self.assertEqual(detail['tags']['color'], ['샌드'])


if __name__ == '__main__':
    unittest.main()
