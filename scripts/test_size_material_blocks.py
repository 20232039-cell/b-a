"""잡화 축·누락 사이즈 행·명시된 소재 비율 회귀 검사. data에는 쓰지 않는다."""
import unittest
from bs4 import BeautifulSoup

import accessory_sizes as accessory
import crawl_cafe24 as crawl
import export_app_data as export
import product_desc as desc
import size_from_ocr as size


class SizeMaterialBlocksTest(unittest.TestCase):
    def test_bag_header_keeps_every_named_row(self):
        text = 'WIDTH / HEIGHT (cm) (S): 26.5 / 46 (M): 30 / 50'
        table = accessory.parse_dimensions(text)
        self.assertEqual(table['size_names'], ['S', 'M'])
        self.assertEqual(table['sizes'], {'가로': [26.5, 30], '세로': [46, 50]})
        out = {}
        size.add_dimensions(out, {'u': ({'brand_slug': 'test', 'category_code': 'bags'},
                                       {'description': text})})
        self.assertEqual(export.size_entry(out['u'])['sizes'], table['sizes'])
        self.assertEqual(out['u']['axis'], 'bag')

    def test_original_bag_axes_and_unlabeled_order(self):
        self.assertEqual(accessory.parse_dimensions('가로 35 X 세로 13 X 폭 12 핸들스트랩 수직 11 (cm)')['sizes'],
                         {'가로': [35], '세로': [13], '폭': [12]})
        raw = accessory.parse_dimensions('37.5x15.5x26(cm) / 끈높이 25cm / 약 700g')
        self.assertEqual(raw['sizes'], {'치수 1': [37.5], '치수 2': [15.5], '치수 3': [26]})
        self.assertEqual(raw['dimension_text'], '37.5x15.5x26(cm)')
        self.assertIsNone(accessory.parse_dimensions('HEIGHT 174cm WAIST 59 Hips 86 shoes 235mm'))
        self.assertIsNone(accessory.parse_dimensions('WIDTH / HEIGHT (OS): 26.5 / 46 / 7'))

    def test_hlw_and_hwd_are_distinct_conventions(self):
        for text in ['H: 11CM L: 30CM W: 6CM', 'H: 11CM W: 30CM D: 6CM']:
            self.assertEqual(accessory.parse_dimensions(text)['sizes'],
                             {'세로': [11], '가로': [30], '폭': [6]})

    def test_dimension_table_survives_parse_detail(self):
        body = '''<title>가방</title><div id="prdDetail"><table>
          <tr><th>SIZE</th><th>WIDTH</th><th>HEIGHT</th></tr>
          <tr><td>OS</td><td>26.5</td><td>46</td></tr></table></div>'''
        result = crawl.parse_detail(body, 'https://example.test/product/bag/1/',
                                    crawl.Shop('test', 'https://example.test', ''))
        self.assertEqual(result['dimension_table']['size_names'], ['OS'])
        self.assertEqual(result['dimension_table']['sizes'], {'가로': [26.5], '세로': [46]})
        existing = {'u': {'source': 'manual', 'sizes': {'가로': [20]}}}
        size.add_dimensions(existing, {'u': ({'category_code': 'bags', 'brand_slug': 'test'}, result)})
        self.assertEqual(existing['u']['sizes'], {'가로': [20]})

    def test_size_accordion_outside_image_detail_and_strap(self):
        html = '''<div id="prdDetail"><img src="photo.jpg"></div>
          <li><a>SIZE GUIDE+</a><ol class="tab_wrap">WIDTH / HEIGHT / STRAP<br>(OS): 57 / 43 / 68</ol></li>'''
        table = accessory.dimensions_from_html(BeautifulSoup(html, 'lxml'))
        self.assertEqual(table['sizes'], {'가로': [57], '세로': [43], '스트랩길이': [68]})

    def test_transposed_accessory_table_keeps_size_names(self):
        html = '''<table><tr><th>SIZE</th><td>S</td><td>M</td></tr>
          <tr><th>WIDTH</th><td>9.5</td><td>11</td></tr>
          <tr><th>HEIGHT</th><td>4</td><td>5</td></tr></table>'''
        table = accessory.dimensions_from_html(BeautifulSoup(html, 'lxml'))
        self.assertEqual(table['size_names'], ['S', 'M'])
        self.assertEqual(table['sizes'], {'가로': [9.5, 11], '세로': [4, 5]})

    def test_aeae_hat_header_does_not_become_garment_axes(self):
        text = 'CIRCUMFERENCE / DEPTH / BRIM LENGTH (cm) (OS) : 58 / 17 / 7.5'
        self.assertEqual(size.parse_head(text),
                         ({'머리둘레': [58], '깊이': [17], '챙길이': [7.5]}, {}))
        self.assertIsNone(size.parse_head(text.replace('58 /', '158 /')))

    def test_nuria_parentheses_use_only_product_flat_section(self):
        text = ('SIZE INFO 상품 치수 안내 ( 단면 ) '
                'S ( 총장 111 cm, B 41 cm, W 35 cm, 소매 56 cm ) '
                'M ( 총장 111.5 cm, B 43.5 cm, W 37.5 cm, 소매 56.5 cm ) '
                'L ( 총장 112 cm, B 46 cm, W 40 cm, 소매 57 cm ) '
                '재는 방법에 따라 오차 모델 사이즈 ( Height 178 cm, B 80 cm, W 58 cm )')
        got = crawl.extract_size_text_forms(text)
        self.assertEqual(got['_names'], ['S', 'M', 'L'])
        self.assertEqual(size.normalize_html(got)['가슴'], [41, 43.5, 46])
        self.assertEqual(size.normalize_html(got)['허리'], [35, 37.5, 40])
        self.assertFalse(crawl.parenthesized_size_rows('MODEL S ( B 80 cm W 58 cm ) M ( B 90 cm W 60 cm )'))

    def test_vern_slash_rows_and_repeated_first_ocr_row(self):
        row = '1/ 총장 62 어깨너비 50.5 가슴단면 55 소매길이 61'
        text = row + '\n' + row + '\n2 / 총장 64 어깨너비 52.5 가슴단면 57 소매길이 62'
        names, cols = size.from_ocr(text)
        self.assertEqual(names, ['1', '2'])
        self.assertEqual(cols['총장'], [62, 64])
        html = '<div id="prdDetail">' + text.replace('\n', '<br>') + '</div>'
        table = crawl.extract_size_any(html)
        self.assertEqual(table['_names'], ['1', '2'])

    def test_material_table_is_kept_in_detail_text(self):
        html = '''<title>shirt</title><div id="prdDetail"><table>
          <tr><th>Material</th><td>Cotton 70% Polyester 30%</td></tr>
          <tr><th>Size</th><td>M L</td></tr></table></div>'''
        got = crawl.parse_detail(html, 'https://example.test/product/shirt/1/',
                                 crawl.Shop('test', 'https://example.test', ''))
        self.assertEqual(desc.mix(got['detail_text'])[0],
                         [{'p': '', 'v': [['코튼', 70], ['폴리에스터', 30]]}])
        self.assertNotIn('Size:', got['detail_text'])

    def test_material_label_outside_known_detail_and_subpart(self):
        html = '''<title>top</title><div id="prdDetail"><img src="photo.jpg"></div>
          <section class="instruction"><dl><dt>MAIN MATERIAL :</dt><dd>Rayon 74%, Poly 21%, Span 5%</dd>
          <dt>SUB MATERIAL :</dt><dd>Acrylic 60%, Nylon 25%, Wool 10%, Span 5%</dd></dl></section>'''
        got = crawl.parse_detail(html, 'https://example.test/product/top/1/',
                                 crawl.Shop('test', 'https://example.test', ''))
        material = desc.mix(got['detail_text'])[0]
        self.assertEqual([p['p'] for p in material], ['겉감', '배색'])
        self.assertEqual(material[0]['v'], [['레이온', 74], ['폴리에스터', 21], ['스판덱스', 5]])

    def test_partial_ratio_does_not_erase_complete_ocr(self):
        material = desc.mix('FABRIC Cotton 60% Polyester 38%\n\n\nFABRIC Cotton 60% Polyester 38% Spandex 2%', True)[0]
        self.assertEqual(material, [{'p': '', 'v': [['코튼', 60], ['폴리에스터', 38], ['스판덱스', 2]]}])
        self.assertFalse(desc.mix('Fabric Cotton 70% Nylon 30%\nFabric Cotton 60% Nylon 40%')[0])
        text = ('- HAIRY FABRIC NYLON 50% / ACRYLIC 40% / WOOL 5% /\n'
                "- AEAE'S SIGNATURE WEB LOGO ANGORA 5%")
        self.assertEqual(desc.mix(text, True)[0],
                         [{'p': '', 'v': [['나일론', 50], ['아크릴', 40], ['울', 5], ['앙고라', 5]]}])

    def test_explicit_original_totals_and_spelling(self):
        text = 'Outshell - Polyester 63% Wool 14% Acrylic 11% Nylon 6% Cotton 2% Lining - Polyester 100%'
        material = desc.mix(text)[0]
        self.assertEqual(sum(v for _, v in material[0]['v']), 96)
        self.assertEqual(desc.mix('FABRIC COTTON 71% POLYESTER 16% RAYON 11%')[0],
                         [{'p': '', 'v': [['코튼', 71], ['폴리에스터', 16], ['레이온', 11]]}])
        self.assertFalse(desc.mix('FABRIC COTTON 71 POLYESTER 16 RAYON 11')[0])
        for text in ['FABRIC COTTON 80% Unknownfiber 20%', 'Cotton 100% Nylon 100%']:
            self.assertFalse(desc.mix(text)[0])
        self.assertEqual(desc.mix('Body - Lambskin Shearling 100% Lining - Recycled Polyester 100%')[0],
                         [{'p': '겉감', 'v': [['양가죽', 100]]}, {'p': '안감', 'v': [['폴리에스터', 100]]}])
        self.assertEqual(desc.mix('MAIN MATERIAL: Polyurathan100%')[0],
                         [{'p': '겉감', 'v': [['폴리우레탄', 100]]}])
        self.assertEqual(desc.mix('폴리63 레이온20 울15 스판2 블랜딩된 원사')[0],
                         [{'p': '', 'v': [['폴리에스터', 63], ['레이온', 20], ['울', 15], ['스판덱스', 2]]}])


if __name__ == '__main__':
    unittest.main()
