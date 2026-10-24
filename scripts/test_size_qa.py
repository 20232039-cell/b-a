"""공식 사이즈표 라벨·범위·중복 OCR 회귀 검사."""
import copy
import io
import unittest
from unittest.mock import patch

import crawl_cafe24 as crawl
import export_app_data as export
import size_from_ocr as size


class SizeQATest(unittest.TestCase):
    def manual(self, rows):
        with patch.object(size, 'MANUAL') as path:
            path.exists.return_value = True
            path.open.return_value = io.StringIO('브랜드,링크,사이즈이름,항목,값,왜,부위,품목\n' + rows)
            return size.load_manual()['u']

    def test_sleeve_width_with_separate_cuff(self):
        text = '어깨 37 가슴 48.5 총기장 55 밑단 50 소매기장 15 암홀 20.2 소매단면 18.5 소매밑단 17'
        old = {'어깨': [37], '가슴': [48.5], '총기장': [55], '소매밑단': [17]}
        fixed = size.normalize_html(size.reparse_thin_table(old, text))
        self.assertEqual(fixed['소매통'], [18.5])
        self.assertEqual(fixed['소매단'], [17])
        self.assertNotIn('소매통', old)

    def test_existing_sleeve_width_and_cuff_are_preserved(self):
        old = {'총장': [59.5], '가슴': [36], '소매통': [13.5], '소매단면': [8.5]}
        self.assertEqual(size.normalize_html(old)['소매통'], [13.5])
        self.assertEqual(size.normalize_html(old)['소매단'], [8.5])

    def test_stale_combined_sleeve_label_is_replaced(self):
        text = 'Size Free Length 56 Shoulder + Sleeve 69 Chest 56 Hem 41 Armholes 21'
        old = {'length': [56], 'sleeve': [69], 'chest': [56], 'hem': [41]}
        fixed = size.normalize_html(size.reparse_thin_table(old, text))
        self.assertEqual(fixed['화장'], [69])
        self.assertNotIn('소매길이', fixed)

    def test_paren_range_survives_without_midpoint(self):
        text = 'Size (Length / Chest / Shoulder / Armhole) OS: 43–48 / 38 / 28 / 20'
        entry = {'size_names': ['OS'], 'sizes': {'총장': [45.5], '가슴': [38], '어깨': [28], '암홀': [20]}}
        saved = copy.deepcopy(entry)
        fixed = size.preserve_measurement_ranges(entry, text)
        self.assertEqual(fixed['ranges'], {'총장': [[43, 48]]})
        self.assertNotIn('총장', fixed['sizes'])
        self.assertEqual(entry, saved)

    def test_dates_and_tolerance_are_not_measurement_ranges(self):
        entry = {"sizes": {"총장": [60], "가슴": [40]}}
        with patch.object(size, 'parse_paren_slash') as parse:
            self.assertEqual(size.preserve_measurement_ranges(entry, '리뷰 2026-10-26 / 오차 1~3cm'), entry)
            parse.assert_not_called()

    def test_long_description_keeps_range_without_parsing_reviews(self):
        text = '설명 ' * 2000 + 'Size (Length / Chest / Shoulder) OS: 43-48 / 38 / 28' + ' 후기' * 2000
        entry = {"size_names": ["OS"], "sizes": {"총장": [45.5], "가슴": [38], "어깨": [28]}}
        fixed = size.preserve_measurement_ranges(entry, text)
        self.assertEqual(fixed["ranges"]["총장"], [[43, 48]])

    def test_ocr_banding_range_and_blank_axis(self):
        text = 'SIZE LENGTH WAIST THIGH HIP HEM\nONE 56 33~42 (밴딩) = 50 54'
        entry = {'size_names': ['ONE'], 'sizes': {'총장': [56], '허리': [37.5], '엉덩이': [50], '밑단': [54]}}
        fixed = size.preserve_measurement_ranges(entry, text)
        self.assertEqual(fixed['ranges'], {'허리': [[33, 42]]})
        self.assertNotIn('허리', fixed['sizes'])
        self.assertNotIn('허벅지', fixed['sizes'])

    def test_named_ranges_ignore_model_measurements(self):
        text = ('SIZE (cm) S Length 76~84.5 / Shoulder 29 / Bust 42 / Waist 38 / Hip 55 '
                'M Length 77~88.5 / Shoulder 31.5 / Bust 44.5 / Waist 40.5 / Hip 57.5 '
                'MODEL SIZE (cm) Height 173 / Bust 79 / Waist 59 / Hips 90')
        entry = {"size_names": ["S", "M"], "sizes": {"총장": [76, 77], "어깨": [29, 31.5],
                 "가슴": [42, 44.5], "허리": [38, 40.5], "엉덩이": [55, 57.5]}}
        fixed = size.preserve_measurement_ranges(entry, text)
        self.assertEqual(fixed["ranges"]["총장"], [[76, 84.5], [77, 88.5]])
        self.assertEqual(fixed["sizes"]["허리"], [38, 40.5])

    def test_comma_lists_keep_separate_size_values(self):
        text = '총장 85,84 가슴 50,49 허리 30~40 / 32~42'
        entry = {"sizes": {"총장": [85, 84], "가슴": [50, 49], "허리": [35, 37]}}
        fixed = size.preserve_measurement_ranges(entry, text)
        self.assertEqual(fixed["ranges"]["허리"], [[30, 40], [32, 42]])
        self.assertEqual(fixed["sizes"]["총장"], [85, 84])

    def test_range_from_other_table_is_not_attached(self):
        text = 'Size (Length / Chest / Shoulder) OS: 43-48 / 38 / 28'
        entry = {'size_names': ['OS'], 'sizes': {'총장': [60], '가슴': [50], '어깨': [40]}}
        self.assertEqual(size.preserve_measurement_ranges(entry, text), entry)

    def test_front_back_lengths_are_not_stretch_ranges(self):
        text = '총장(앞/뒤) 65~70 가슴 50 어깨 40'
        entry = {"sizes": {"총장": [65], "가슴": [50], "어깨": [40]}}
        self.assertEqual(size.preserve_measurement_ranges(entry, text), entry)
        self.assertEqual(size.preserve_measurement_ranges(entry, raw_ranges={"총장(앞/뒤)": [[65, 70]]}), entry)

    def test_girth_range_does_not_replace_flat_values(self):
        text = 'Size (Length / Chest / Waist) S: 60 / 40 / 65-70'
        entry = {"size_names": ["S"], "sizes": {"총장": [60], "가슴": [40], "허리": [33.75]}}
        self.assertEqual(size.preserve_measurement_ranges(entry, text), entry)
        fixed = size.preserve_measurement_ranges(entry, raw_ranges={"허리둘레": [[65, 70]]})
        self.assertEqual(fixed["ranges"]["허리"], [[32.5, 35]])

    def test_structured_range_axis_is_preserved(self):
        raw = crawl.extract_size_any('<table><tr><th>Size</th><th>총장</th><th>가슴</th><th>어깨</th></tr>'
                                     '<tr><td>S</td><td>43~48</td><td>38</td><td>28</td></tr></table>')
        self.assertEqual(raw['_ranges']['총장'], [[43, 48]])
        entry = {'size_names': raw['_names'], 'sizes': size.normalize_html(raw)}
        fixed = size.preserve_measurement_ranges(entry, raw_ranges=raw['_ranges'])
        self.assertEqual(export.size_entry(fixed)['ranges']['총장'], [[43, 48]])

    def test_app_keeps_row_with_only_range_values(self):
        entry = {"size_names": ["S", "M"], "sizes": {"가슴": [None, 40]},
                 "ranges": {"총장": [[43, 48], None]}}
        app = export.size_entry(entry)
        self.assertEqual(app["size_names"], ["S", "M"])
        self.assertEqual(app["ranges"]["총장"], [[43, 48], None])

    def test_archive_223_measurements_match_official_table(self):
        text = 'Size (Length / Chest / Shoulder / Arm) 2: 64 / 65 / 64 / 64 3: 65 / 67 / 66 / 65'
        names, cols = size.parse_paren_slash(text)
        self.assertEqual(names, ["2", "3"])
        self.assertEqual(cols, {"총장": [64, 65], "가슴": [65, 67], "어깨": [64, 66], "소매길이": [64, 65]})

    def test_structured_range_keys_are_canonical(self):
        entry = {"sizes": {"가슴": [38], "어깨": [28]}, "ranges": {"length": [[43, 48]]}}
        fixed = size.preserve_measurement_ranges(entry, raw_ranges={"length": [[43, 48]]})
        self.assertEqual(fixed["ranges"], {"총장": [[43, 48]]})

    def test_manual_ranges_groups_and_components(self):
        ent = self.manual('b,u,S·M,총장,59.5~69.5·60.5~70.5,,,\n'
                          'b,u,S·M,가슴,41·43.5,,,\n'
                          'b,u,L,총장,65~75,,,\n'
                          'b,u,L,가슴,46,,,\n'
                          'b,u,S·M,총장,78~85·79~86,,하의,스커트\n'
                          'b,u,S·M,허리,33·35,,하의,스커트\n')
        self.assertEqual(ent['size_names'], ['S', 'M', 'L'])
        self.assertEqual(ent['ranges']['총장'], [[59.5, 69.5], [60.5, 70.5], [65, 75]])
        self.assertNotIn('총장', ent['sizes'])
        app = export.size_entry(ent)
        self.assertEqual(app['size_parts'][0]['ranges']['총장'], [[78, 85], [79, 86]])
        self.assertEqual(app['sizes']['가슴'], [41, 43.5, 46])

    def test_manual_mixed_scalar_and_range(self):
        ent = self.manual('b,u,S·M,총장,43~48·50,,,\nb,u,S·M,가슴,38·40,,,\n')
        self.assertEqual(ent['sizes']['총장'], [None, 50])
        self.assertEqual(ent['ranges']['총장'], [[43, 48], None])

    def test_numbered_name_does_not_take_decimal_tail(self):
        text = ('SIZE DETAIL 1사이즈38.5 힙63 허벅지34 밑단29 총장111 '
                '2사이즈 허리41 힙65 허벅지35 밑단30 총장112 '
                '3사이즈 허리43 힙65 허벅지36 밑단31 총장113')
        old = {'힙': [63, 65, 65], '밑단': [29, 30, 31], '총장': [111, 112, 113],
               '_names': ['5', '2사이즈', '3사이즈']}
        fixed = size.reparse_thin_table(old, text)
        self.assertEqual(fixed['_names'], ['1사이즈', '2사이즈', '3사이즈'])
        self.assertEqual(fixed['허리'], [None, 41, 43])  # 첫 숫자에는 원문 라벨이 없다

    def test_numbered_row_with_official_typos_is_recovered(self):
        text = ('1사이즈 허리40 엉덩이57 허벅지39 밑단37 총장57 '
                '2사이즈 허리42 엉덩이59 허벅지41 밑단39 총장59 '
                '3사티즈 허리44 앙덩이61 허벅지43 밑딘41 총장61')
        old = {'허리': [40, 42], '엉덩이': [57, 59], '허벅지': [39, 41], '밑단': [37, 39], '총장': [57, 59]}
        fixed = size.reparse_thin_table(old, text)
        self.assertEqual(fixed['_names'], ['1사이즈', '2사이즈', '3사이즈'])
        self.assertEqual(size.normalize_html(fixed)['엉덩이'], [57, 59, 61])
        self.assertEqual(size.normalize_html(fixed)['밑단'], [37, 39, 41])

    def test_clean_ocr_copy_wins_over_corrupted_duplicate(self):
        text = ('Length) Chest Half) Sleeve)\nM 71.1 76.2 82.6\nL 73.0 78.7 85.1\n'
                '총장 가슴단면 소매길이\n(Length) (Chest Half) (Sleeve)\n'
                'M 7121 76.2 82.6\nL 7320 78.7 85.1')
        names, cols = size.from_ocr(text)
        self.assertEqual(names, ['M', 'L'])
        self.assertEqual(cols['총장'], [71.1, 73])
        self.assertEqual(cols['가슴'], [76.2, 78.7])

    def test_prefix_recovers_missing_decimal_and_fullwidth_name(self):
        text = ('Length) Shoulder) Chest Half) Sleeve)\nS 66 49.5 61 63\nM 67.3 50.8 63.5 64.1\nＬ 68.6 521 66 65.4\n'
                '-_ 총장 어깨너비 가슴단면 소매길이\n(Length) (Shoulder) (Chest Half) (Sleeve)\n'
                'S 66 49.5 61 63\nM 67.3 50.8 63.5 64.1\nL 68.6 32.1 66 65.4')
        names, cols = size.from_ocr(text)
        self.assertEqual(names, ['S', 'M', 'L'])
        self.assertEqual(cols['어깨'], [49.5, 50.8, 52.1])

    def test_clean_ocr_shoulder_copy_wins(self):
        text = ('Length) Shoulder) Chest Half) Sleeve)\nS 65 53.3 57.2 63\nM 66 55 59.7 64.1\nL 67.3 55.9 62.2 65.4\n'
                '-_ 총장 어깨너비 가슴단면 소매길이\n(Length) (Shoulder) (Chest Half) (Sleeve)\n'
                'S 65 53.3 57.2 63\nM 66 53 59.7 64.1\nL 67.3 95.9 62.2 65.4')
        self.assertEqual(size.from_ocr(text)[1]['어깨'], [53.3, 55, 55.9])


if __name__ == '__main__':
    unittest.main()
