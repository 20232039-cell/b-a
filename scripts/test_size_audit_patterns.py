"""800벌 감사에서 반복된 HTML·OCR 조각의 회귀 검사. 네트워크/data 쓰기 없음."""
import copy
import unittest

import crawl_cafe24 as crawl
import size_from_ocr as size


class AuditPatternsTest(unittest.TestCase):
    def test_html_long_labels_do_not_split_into_short_labels(self):
        text = ('S (55) 어깨 40 가슴 46 소매기장 46.5 소매통 17 총장 44 '
                'M (66) 어깨 42 가슴 48 소매기장 47.5 소매통 17.8 총장 46 '
                'L (77) 어깨 44 가슴 50 소매기장 48.5 소매통 18.6 총장 48')
        cols = size.normalize_html(crawl.extract_size_table(text))
        self.assertEqual(cols['소매통'], [17, 17.8, 18.6])
        self.assertEqual(cols['소매길이'], [46.5, 47.5, 48.5])
        self.assertEqual(cols['총장'], [44, 46, 48])

    def test_html_sleeve_cuff_and_rise_aliases(self):
        raw = crawl.extract_size_any('<p>총장 108 허리단면 40 허벅지단면 33.5 '
                                     '밑위길이 33 밑단단면 24.5</p>')
        self.assertEqual(size.normalize_html(raw)['밑위'], [33])
        for label, canon in [('소매장', '소매길이'), ('소매끝단면', '소매단'),
                             ('소매부리', '소매단'), ('Cuff', '소매단'), ('Armholes', '암홀')]:
            with self.subTest(label=label):
                raw = crawl.extract_size_table(f'총장 60 가슴 50 {label} 21')
                self.assertEqual(size.normalize_html(raw)[canon], [21])

    def test_html_two_cell_table_keeps_all_axes_and_single_name(self):
        text = '<table><tr><td>SIZE (CM)</td><td>OS</td></tr>' + ''.join(
            f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in
            [('어깨', 45), ('가슴', 52.5), ('소매기장', 61), ('소매통', 22.5), ('소매단', 13.5), ('총기장', 52)]) + '</table>'
        raw = crawl.extract_size_any(text)
        self.assertEqual(raw['_names'], ['OS'])
        self.assertEqual(size.normalize_html(raw)['소매길이'], [61])
        self.assertEqual(size.normalize_html(raw)['소매통'], [22.5])
        self.assertEqual(size.normalize_html(raw)['소매단'], [13.5])

    def test_headerless_single_table_does_not_use_measurement_as_size(self):
        raw = crawl.extract_size_any('<table><tr><td>가슴</td><td>52</td></tr>'
                                     '<tr><td>총장</td><td>61</td></tr></table>')
        self.assertNotIn('_names', raw)
        self.assertEqual(raw['가슴'], [52])

    def test_forward_single_table_keeps_explicit_xs(self):
        raw = crawl.extract_size_any('<table><tr><td>Size</td><td>총장</td><td>가슴</td></tr>'
                                     '<tr><td>XS</td><td>60</td><td>48</td></tr></table>')
        self.assertEqual(raw['_names'], ['XS'])

    def test_list_table_keeps_numeric_size_header(self):
        text = '''<div class="size_guide_table"><div class="size_guide_table_header">
        <ul><li></li><li>46</li><li>48</li><li>50</li></ul></div>
        <div class="size_guide_table_body"><ul><li>총장</li><li>64.5</li><li>66</li><li>67.5</li></ul>
        <ul><li>가슴단면</li><li>54</li><li>56</li><li>58</li></ul></div></div>'''
        raw = crawl.extract_size_any(text)
        self.assertEqual(raw['_names'], ['46', '48', '50'])
        self.assertEqual(raw['총장'], [64.5, 66, 67.5])

    def test_decimal_size_name_is_one_name(self):
        text = ('1(S) : 기장 101/ 허리 33.5/ 엉덩이 50.5/ 허벅지 31/ 밑단 26.5/ 27.5 (cm) '
                '1.5(M) : 기장 103/ 허리 36/ 엉덩이 53/ 허벅지 32/ 밑단 27.5/ 28 (cm)')
        raw = crawl.extract_size_any(text)
        self.assertEqual(raw['_names'], ['1(S)', '1.5(M)'])
        self.assertEqual(raw['허리'], [33.5, 36])
        self.assertEqual(raw['밑단'], [26.5, 27.5])  # 무명 말미 값을 사이즈로 붙이지 않는다

    def test_hwajang_labels_before_shorter_labels(self):
        for label, value in [('Shoulder + Sleeve', 69), ('래글런', 82), ('옆목~소매단', 40)]:
            with self.subTest(label=label):
                raw = crawl.extract_size_any(f'<p>총장 60 가슴 55 {label} {value}</p>')
                self.assertEqual(size.normalize_html(raw)['화장'], [value])
                self.assertNotIn('소매길이', size.normalize_html(raw))

    def test_ocr_sleeve_and_opening_follow_five_value_cells(self):
        text = ('CM Length Shoulder Chest Sleeve Opening\n'
                'FREE 66 43.8 52.7 62.5 15.2\n0 a 0 0 0 0 ee ee . 4')
        names, cols = size.from_ocr(text)
        self.assertEqual(names, ['FREE'])
        self.assertEqual(cols, {'총장': [66], '어깨': [43.8], '가슴': [52.7],
                                '소매길이': [62.5], '소매단': [15.2]})

    def test_ocr_four_value_sleeve_opening_stays_one_column(self):
        names, cols = size.from_ocr('CM Length Shoulder Chest Sleeve Opening\nFREE 66 43.8 52.7 15.2')
        self.assertEqual(names, ['FREE'])
        self.assertEqual(cols['총장'], [66])
        self.assertEqual(cols['소매단'], [15.2])
        self.assertNotIn('소매길이', cols)

    def test_ocr_split_opening_uses_first_complete_reading(self):
        text = ('CM Length Shoulder Chest Sleeve Openi ng\nFREE 68.6 47 58.4 61 15.2\n'
                'COMMENT\nCM Length Shoulder Chest Sleeve Opening\nFREE 68.6 47 98.4 61 19.2')
        _, cols = size.from_ocr(text)
        self.assertEqual(cols['총장'], [68.6])
        self.assertEqual(cols['가슴'], [58.4])
        self.assertEqual(cols['소매단'], [15.2])

    def test_ocr_ambiguous_duplicate_armhole_does_not_override(self):
        self.assertIsNone(size.parse_positioned_matrix([
            'CM Length Shoulder Chest Armhole Armhole', 'FREE 52.1 43.2 39.4 15.2 20.3']))

    def test_ocr_dash_does_not_shift_raglan_chest(self):
        for dash in ['-', '=']:
            names, cols = size.from_ocr(f'CM Length Shoulder Chest Sleeve Opening\nFREE 61 {dash} 50.8 40.6 17.8')
            self.assertEqual(names, ['FREE'])
            self.assertEqual(cols['가슴'], [50.8])
            self.assertEqual(cols['소매길이'], [40.6])
            self.assertNotIn('어깨', cols)

    def test_ocr_unknown_or_repeated_header_still_occupies_a_cell(self):
        for broken in ['ve', '어깨']:
            text = f'(CM) 총장 어깨 가슴 소매길이 소매부리 {broken} 밑단\nFREE SIZE 51 35 40 60.5 10.5 19.5 39'
            _, cols = size.from_ocr(text)
            self.assertEqual(cols['밑단'], [39])
            self.assertEqual(cols['어깨'], [35])
            self.assertNotIn('암홀', cols)  # 깨진 이름을 추정하지 않는다
        self.assertIsNone(size.parse_positioned_matrix([
            '(CM) 총장 어깨 가슴 소매길이 소매부리 ve 밑단', 'FREE 51 35 40 60.5 10.5 39']))

    def test_invalid_numeric_axis_is_removed_with_or_without_names(self):
        for names in [None, ['S', 'M']]:
            raw = {'어깨': [24, 32], '총장': [60, 62]}
            if names:
                raw['_names'] = names
            self.assertNotIn('어깨', size.normalize_html(raw))
            self.assertEqual(size.normalize_html(raw)['총장'], [60, 62])

    def test_two_decimal_places_are_preserved_in_html_and_ocr(self):
        raw = crawl.extract_size_table('허리 33.5 허벅지 34.75 밑위 28.56 총장 107.5')
        cols = size.normalize_html(raw)
        self.assertEqual(cols['허벅지'], [34.75])
        self.assertEqual(cols['밑위'], [28.56])
        self.assertEqual(size.fix_value('허리', '63.5', True), 31.75)
        self.assertEqual(size._rr_value('소매통', '34.5', True, size.RANGES), 17.25)
        text = size.halve_girth_lines('허리둘레 63.5 / 68.6\n총장 80 / 82')
        self.assertIn('31.75', text)
        self.assertIn('34.3', text)

    def test_unit_guard_keeps_exact_half_and_explicit_flat_ocr(self):
        out = {'u': {'source': 'ocr', 'sizes': {'허리': [63.5, 68.6]}}}
        size.unit_guard(out)
        self.assertEqual(out['u']['sizes']['허리'], [31.75, 34.3])
        cols = {'총장': [116.8, 118.1], '소매통': [39.6, 41.5]}
        flat = size.explicit_flat_values({}, '*사이즈 측정기준 : 단면 / 단위 : cm', cols)
        out = {'u': {'source': 'ocr', 'sizes': copy.deepcopy(cols)}}
        size.unit_guard(out, {'u': flat})
        self.assertEqual(out['u']['sizes'], cols)
        self.assertEqual(size.explicit_flat_values({}, '가슴 단면 소매통', cols), {})

    def test_manual_values_survive_late_unit_and_sleeve_passes(self):
        out = {'u': {'source': 'manual', 'sizes': {'가슴': [90], '소매통': [39.6], '소매길이': [48]}}}
        before = copy.deepcopy(out)
        size.unit_guard(out)
        size.sleeve_from_neck(out, {'u': {'subtype': '반팔'}})
        size.halve_unit_outliers(out, {})
        self.assertEqual(out, before)

    def test_saved_upper_table_is_not_replaced_by_mixed_set_text(self):
        raw = {'가슴': [36.5], '밑단': [35], '총장': [39.5]}
        text = ('상의 S (55) 가슴 36.5 밑단 35 총장 39.5 '
                'M (66) 가슴 38.5 밑단 37 총장 40.5 '
                '하의 S (~27) 허리둘레 72.5 허리둘레(끈 포함) 152.5 총장 38 '
                'M (~28) 허리둘레 73.5 허리둘레(끈 포함) 156.5 총장 39')
        fixed = size.reparse_thin_table(raw, text)
        self.assertEqual(fixed, raw)
        self.assertEqual(size.normalize_html(fixed)['총장'], [39.5])


if __name__ == '__main__':
    unittest.main()
