"""값 · 할인 · 사이즈 숫자 회귀 검사(코덱스 전수 감사 2026-10-27). data/에는 쓰지 않는다."""
import io
import unittest
from unittest.mock import patch

import crawl_cafe24 as crawl
import export_app_data as export
import size_from_ocr as size


class PricelessPageTest(unittest.TestCase):
    """값이 한 번도 없었던 행 — 글은 빼고, 값만 내린 진짜 옷은 남긴다."""

    def test_year_season_is_not_short_sleeve(self):
        for n in ['2023 S/S 1ST COLLECTION', 'MATIN KIM X LIZ 2026 S/S COLLECTION', 'Mmlg 2020 S/S part 1',
                  '2017 S/S FILM', "2022S/S collection 'Marigold'", '25 S/S LAYERS II']:
            with self.subTest(n=n):
                self.assertIsNotNone(crawl.not_a_product(n))
        # 연도가 없는 S/S 는 여전히 반팔 낱말이다
        for n in ['S/S TEE BLACK', '2023 S/S TEE', '24SS 반팔 티셔츠', 'Aoiro Non-Smoking Area Cap Washed Black']:
            with self.subTest(n=n):
                self.assertIsNone(crawl.not_a_product(n))

    def test_look_number_and_curation(self):
        self.assertEqual(crawl.not_a_product('Look 11'), '룩번호')
        self.assertEqual(crawl.not_a_product('Horn Button [curation]'), '큐레이션')
        self.assertIsNone(crawl.not_a_product('Look Pocket Shirt'))

    def test_priceless_post(self):
        pp = crawl.priceless_post
        self.assertEqual(pp({'name': 'SELVEDGE DENIM EDITORIALS', 'category_names': ['Lookbooks']}, '데님', 'outstanding'), '룩북칸')
        self.assertEqual(pp({'name': '25 S/S LAYERS', 'category_names': ['MOODBOOK', 'MOODBOOK']}, '반팔', 'begin202'), '룩북칸')
        self.assertEqual(pp({'name': 'Boots Cut Fit', 'category_names': ['The Denim Guide']}, '팬츠', 'facade-pattern'), '룩북칸')
        # 칸 하나라도 상품 칸이면 묻지 않는다
        self.assertIsNone(pp({'name': 'x', 'category_names': ['PRESS', 'women']}, '자켓', 'ava-molli'))
        self.assertEqual(pp({'name': 'EWM_26SS_LIGHT V NECK KNIT (POWDER PINK)_1', 'category_names': ['View more']}, '니트', 'mmlg'),
                         '매장 연재 글')
        self.assertIsNone(pp({'name': 'EWM_26SS_LIGHT V NECK KNIT', 'category_names': ['View more']}, '니트', 'other-store'))
        self.assertEqual(pp({'name': 'NCT 도영 - 레이어드 가디건 풀오버 니트 (블랙)'}, '니트', 'noirer'), '연예인 착용 글')
        self.assertIsNone(pp({'name': '울 코트 - 블랙'}, '코트', 'x'))
        self.assertIsNone(pp({'name': 'NCT 도영 - 니트', 'options': ['S', 'M']}, '니트', 'noirer'))
        self.assertEqual(pp({'name': 'Day Out In Seoul_1'}, '', 'low-classic'), '화보 장면 번호')
        self.assertIsNone(pp({'name': 'Cutted Double Knee Pants - Tan', 'options': ['1', '2']}, '팬츠', 'aoiro'))


class SeasonAnchorTest(unittest.TestCase):
    def test_dropped_posts_still_anchor_season(self):
        rows = [{'brand_slug': 'm', 'product_no': str(i), 'season': '26FW' if i <= 10 else ''} for i in range(1, 13)]
        crawl.fill_season_gaps(rows, [{'brand_slug': 'm', 'product_no': '20', 'season': '26FW'}])
        self.assertEqual(rows[-1]['season'], '26FW')
        rows = [{'brand_slug': 'm', 'product_no': str(i), 'season': '26FW' if i <= 10 else ''} for i in range(1, 13)]
        crawl.fill_season_gaps(rows)
        self.assertEqual(rows[-1]['season'], '')


class SoldOutDiscountTest(unittest.TestCase):
    def row(self, **kw):
        r = {'brand_slug': 'b', 'product_no': '1', 'name': 'n', 'price': '89000', 'list_price': '89000',
             'sale_price': '26700', 'image_url': '', 'category_code': 'tops', 'subtype': '', 'gender_target': '',
             'status': 'SOLD_OUT', 'source_url': 'u', 'representative_color': '', 'category': 'Tops', 'options': ''}
        r.update(kw)
        return export.thin_row(r, {}, {'b': 0}, {'tops': 0}, {})

    def test_listed_sold_out_keeps_discount_delisted_drops_it(self):
        self.assertEqual(self.row()['op'], 89000)
        r = self.row(delisted='1')
        self.assertNotIn('op', r)
        self.assertEqual(r['p'], 26700)


class SizeCheckTest(unittest.TestCase):
    def test_girth_values_on_flat_labels(self):
        c = export.size_chk({'size_names': ['XS', 'S', 'M'], 'sizes': {'가슴': [115, 120, 125], '총장': [54, 56, 58]}}, [])
        self.assertEqual(c, {'가슴': [0, 1, 2]})
        self.assertEqual(export.size_chk({'size_names': ['0/1', '2/3'], 'sizes': {'허리': [74, 79]}}, []), {'허리': [0, 1]})
        self.assertEqual(export.size_chk({'size_names': ['S', 'M'], 'sizes': {'가슴': [60, 63], '밑단': [80, 85]}}, []), {})

    def test_reversed_cell(self):
        c = export.size_chk({'size_names': ['XS', 'S', 'M', 'L', 'XL'], 'sizes': {'가슴': [52, 54, 56, 58, 41]}}, [])
        self.assertEqual(c, {'가슴': [4]})
        c = export.size_chk({'size_names': ['S', 'M', 'L', 'XL'], 'sizes': {'가슴': [54, 57.5, 56, 58]}}, [])
        self.assertEqual(c, {'가슴': [1, 2]})
        # 이미 튀는 값으로 걸린 칸은 차례 검사에서 뺀다 — 옳은 이웃 칸까지 흐리게 하지 않는다
        self.assertEqual(export.size_chk({'size_names': ['M', 'L'], 'sizes': {'소매단': [95, 10]}}, []), {'소매단': [0]})
        # 이름 차례를 모르면 보지 않는다
        self.assertEqual(export.size_chk({'size_names': ['가디건', '나시'], 'sizes': {'가슴': [42, 35]}}, []), {})


class SizeNameLadderTest(unittest.TestCase):
    def fix(self, names, sizes, source='ocr'):
        out = {'u': {'source': source, 'size_names': names, 'sizes': sizes}}
        size.fix_name_ladder(out)
        return out['u']['size_names']

    def test_fill_one_misread_rung(self):
        self.assertEqual(self.fix(['S', '41', 'L', 'XL'], {'총장': [70, 72, 74, 76]}), ['S', 'M', 'L', 'XL'])
        self.assertEqual(self.fix(['XS', '68', 'M', 'L', 'XL', '2XL'], {'가슴': [1, 2, 3, 4, 5, 6]}),
                         ['XS', 'S', 'M', 'L', 'XL', '2XL'])
        self.assertEqual(self.fix(['0', '4', '2', '3'], {'가슴': [43, 55.5, 57.5, 59.5]}), ['0', '1', '2', '3'])

    def test_store_swapped_letters(self):
        # drawfit 1046 — 그림에 「S · L · M · XL」로 적혀 있고 값은 모두 차례대로 커진다
        self.assertEqual(self.fix(['S', 'L', 'M', 'XL'], {'총장': [92, 93, 94, 95], '허리': [39, 40, 42, 44]}),
                         ['S', 'M', 'L', 'XL'])
        # 값이 차례대로가 아니면 이름을 옮기지 않는다
        self.assertEqual(self.fix(['S', 'L', 'M', 'XL'], {'총장': [92, 94, 93, 95]}), ['S', 'L', 'M', 'XL'])

    def test_ambiguous_left_alone(self):
        for names in (['S', '6', 'M', 'L'], ['36', '28', '38'], ['48', '46', '50', '52']):
            with self.subTest(names=names):
                self.assertEqual(self.fix(list(names), {'총장': [1, 2, 3, 4][:len(names)]}), names)
        self.assertEqual(self.fix(['S', '41', 'L', 'XL'], {'총장': [1, 2, 3, 4]}, source='manual'), ['S', '41', 'L', 'XL'])

    def test_reversed_axis_with_blank_cell_is_dropped(self):
        out = {'u': {'source': 'ocr', 'size_names': ['M', 'L', 'XL'], 'sizes': {'어깨': [49.0, 31.0, None], '총장': [70, 72, None]}}}
        size.drop_reversed_labels(out)
        self.assertEqual(out['u']['sizes'], {'총장': [70, 72, None]})


class TwinColumnTest(unittest.TestCase):
    def run_one(self, names, opts, source='html'):
        out = {'u': {'source': source, 'size_names': names,
                     'sizes': {'가슴': [54.5, 54.5], '총장': [63.0, 63.0], '화장': [80.5, 80.5]}}}
        size.collapse_twin_columns(out, {'u': {'options': opts}})
        return out['u']

    def test_single_option_collapses(self):
        e = self.run_one(['173cm', '174cm'], 'Free')
        self.assertEqual((e['size_names'], e['sizes']['가슴']), (['Free'], [54.5]))
        self.assertEqual(self.run_one(['1', '001'], '001')['size_names'], ['001'])
        self.assertEqual(self.run_one(['INFO', 'mocha'], 'cloud')['size_names'], ['FREE'])

    def test_two_options_or_manual_untouched(self):
        self.assertEqual(self.run_one(['S', 'M'], 'S | M')['size_names'], ['S', 'M'])
        self.assertEqual(self.run_one(['S', 'ONLY'], 'S', source='manual')['size_names'], ['S', 'ONLY'])


class ManualSetShapeTest(unittest.TestCase):
    def load(self, text):
        with patch.object(size, 'MANUAL') as manual:
            manual.exists.return_value = True
            manual.open.return_value = io.StringIO('브랜드,링크,사이즈이름,항목,값,왜,부위,품목\n' + text)
            return size.load_manual()['u']

    def test_explicit_girth_label_is_flagged_not_converted(self):
        ent = self.load('roem,u,064·067,허리둘레,69.5·72,,,바지\n'
                        'roem,u,064·067,총장,90.4·91,,,바지\n'
                        'ava,v,S·M,가슴,49.5·51.5,,,\n')
        self.assertEqual(ent['sizes']['허리'], [69.5, 72.0])       # 값은 원문 그대로
        app = export.size_entry(ent)
        self.assertEqual(app['chk'], {'허리': [0, 1]})
        self.assertNotIn('girth', app)
        # 매장이 단면 크기 값에 「둘레」라 적은 것은 흐리게 하지 않는다
        with patch.object(size, 'MANUAL') as manual:
            manual.exists.return_value = True
            manual.open.return_value = io.StringIO('브랜드,링크,사이즈이름,항목,값,왜,부위,품목\nava,v,S·M,가슴둘레,49.5·51.5,,,\n')
            ent2 = size.load_manual()['v']
        self.assertNotIn('chk', export.size_entry(ent2))

    def test_set_groups_keep_trailing_blanks(self):
        # etmon 2852 — 가디건 · 나시 두 묶음. 나시에 없는 어깨 · 소매는 빈 칸으로 자리를 지킨다
        ent = self.load('ETMON,u,FREE SIZE(가디건),총장,45.5,,,가디건\n'
                        'ETMON,u,FREE SIZE(가디건),어깨,34,,,가디건\n'
                        'ETMON,u,FREE SIZE(나시),총장,44.5,,,슬리브리스\n')
        self.assertEqual(ent['size_names'], ['FREE SIZE(가디건)', 'FREE SIZE(나시)'])
        self.assertEqual(ent['sizes'], {'총장': [45.5, 44.5], '어깨': [34.0, None]})
        app = export.size_entry(ent)
        self.assertTrue(all(len(v) == len(app['size_names']) for v in app['sizes'].values()))


if __name__ == '__main__':
    unittest.main()
