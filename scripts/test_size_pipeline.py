"""사이즈 행·라벨·단위 회귀 검사. data/에는 쓰지 않는다."""
import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bs4 import BeautifulSoup

import crawl_cafe24 as crawl
import size_from_ocr as size


class SizePipelineTest(unittest.TestCase):
    def match_sizes(self, names, opts, cols=None, full_opts=None):
        cols = cols if cols is not None else len(names)
        out = {'u': {'size_names': names, 'sizes': {'총장': list(range(60, 60 + cols))}}}
        size.add_size_keys(out, {'u': {'options': ' | '.join(opts)}},
                           {'u': full_opts} if full_opts else {})
        return out['u']['size_keys'], out['u']['size_match']

    def test_number_suffixes_and_uni(self):
        for opts in [['1번', '2번'], ['UNI 1', 'UNI 2'], ['1사이즈', '2사이즈']]:
            with self.subTest(opts=opts):
                self.assertEqual(self.match_sizes(['1', '2'], opts), (['1', '2'], '같음'))
        self.assertEqual(self.match_sizes(['1시이즈', '2사이즈'], ['1번', '2번']), (['1', '2'], '같음'))

    def test_color_number_size_and_typo(self):
        opts = ['black 1 size', 'black 2size', 'dust charcoal 1size', 'dust charcoal 2ize']
        self.assertEqual(self.match_sizes(['1', '2'], opts), (['1', '2'], '같음'))
        self.assertEqual(self.match_sizes(['1', '2'], ['빈티지 블랙 1size', '빈티지 블랙 2size']),
                         (['1', '2'], '같음'))
        self.assertEqual(self.match_sizes(['1', '2'], ['Black 1', 'black 2']), (['1', '2'], '같음'))

    def test_gender_keeps_same_number_separate(self):
        names = ['MAN SIZE 1', 'WOMAN SIZE 1']
        self.assertEqual(self.match_sizes(names, ['WOMEN 1SIZE', 'MEN 1SIZE']),
                         (['MEN-1', 'WOMEN-1'], '같음'))
        self.assertEqual(self.match_sizes(['1', '2'], ['WOMEN 1', 'UNISEX 2']),
                         (['WOMEN-1', 'UNISEX-2'], '같음'))
        self.assertEqual(self.match_sizes(['1번', '2번'], ['MEN 1SIZE', 'WOMEN 1SIZE'])[1], '모름')

    def test_length_variants_follow_table_order(self):
        names = ['S original', 'S long', 'M original', 'M long']
        opts = ['m_LONG', 'original-S', 'm-original', 'LONG_s']
        self.assertEqual(self.match_sizes(names, opts),
                         (['S-ORIGINAL', 'S-LONG', 'M-ORIGINAL', 'M-LONG'], '같음'))
        self.assertNotEqual(size.size_key('S original'), size.size_key('S long'))
        self.assertEqual(size.size_aliases('M_long'), ['M-LONG'])
        self.assertEqual(self.match_sizes(['original', 'long'], ['FREE_original', 'FREE_long', 'BLACK']),
                         (['FREE-ORIGINAL', 'FREE-LONG'], '같음'))

    def test_separate_waist_and_length_options(self):
        names = ['W28 / L30', 'W30 / L30', 'W28 / L32', 'W30 / L32']
        opts = ['W28', 'W30', 'L30 [10/30 예약 출고]', 'L32']
        self.assertEqual(self.match_sizes(names, opts),
                         (['W28/L30', 'W30/L30', 'W28/L32', 'W30/L32'], '같음'))
        self.assertNotIn('L30', size.size_aliases('W28 / L30'))
        self.assertEqual(self.match_sizes(names[:-1], opts)[1], '모름')
        self.assertEqual(self.match_sizes([names[0], names[0], *names[2:]], opts)[1], '모름')

    def test_parenthesized_color_codes_are_not_sizes(self):
        for color in ['(39)Ivory', '(85)Brown', '(88)Lavender', '(31)Light Yellow']:
            with self.subTest(color=color):
                self.assertEqual(size.size_aliases(color), [])
                self.assertEqual(size._opt_keys(color), set())
        self.assertEqual(size.size_aliases('S(085)'), ['S', '85'])
        self.assertEqual(self.match_sizes(['S(085)', 'M(088)'],
                                        ['S(085)', 'M(088)', '(39)Ivory', '(85)Brown']),
                         (['S', 'M'], '같음'))
        self.assertEqual(self.match_sizes(['64', '67', '70'], ['064', '067', '070', '(67)Brown']),
                         (['64', '67', '70'], '같음'))

    def test_spelled_sizes_with_body_ranges(self):
        self.assertEqual(size.size_key('MEDIUM(95-100)'), 'M')
        self.assertEqual(size.size_key('LARGE(105-110)'), 'L')
        self.assertEqual(self.match_sizes(['M', 'L'], ['MEDIUM(95-100)', 'LARGE(105-110)']),
                         (['M', 'L'], '같음'))

    def test_padded_numeric_codes_use_rank(self):
        self.assertEqual(self.match_sizes(['1', '2'], ['000', '001']), (['000', '001'], '차례로'))
        self.assertEqual(self.match_sizes(['1', '2'], ['001', '000'])[1], '모름')
        self.assertEqual(self.match_sizes(['1', '2'], ['000', '003', '004'])[1], '모름')
        self.assertEqual(size._opt_rank('000'), 100)
        self.assertEqual(size._opt_rank('001'), 101)

    def test_color_alpha_sizes_and_stock(self):
        for opts in [['BLUE-S', 'IVORY-M'], ['네이비 S', '베이지 M'],
                     ['SMALL - Only 2 left', 'MEDIUM - Only 1 left']]:
            with self.subTest(opts=opts):
                self.assertEqual(self.match_sizes(['S', 'M'], opts), (['S', 'M'], '같음'))
        self.assertEqual(size.size_key('CARDIGAN S'), None)
        self.assertIn('M', size._opt_keys('NAVY-M'))

    def test_large_sizes_and_zipper_options(self):
        names = ['M', 'L', 'XL', '2XL']
        self.assertEqual(self.match_sizes(names, ['M', 'L', 'XL', 'XXL']),
                         (['M', 'L', 'XL', 'XXL'], '같음'))
        self.assertEqual(size.size_key('2X-LARGE'), 'XXL')
        self.assertEqual(size.size_key('2XL 엔틱지퍼 (+KRW 50,000)'), 'XXL')

    def test_korean_length_and_color_length_variants(self):
        self.assertEqual(self.match_sizes(['SHORT', 'LONG'], ['숏', '롱', '블랙']),
                         (['SHORT', 'LONG'], '같음'))
        self.assertEqual(self.match_sizes(['S short', 'S long'], ['mustard-s-short', 'mustard-s-long']),
                         (['S-SHORT', 'S-LONG'], '같음'))

    def test_expansion_rejects_ambiguity_and_bad_names(self):
        self.assertEqual(self.match_sizes(['1번', '2번'], ['1(S)', '1(M)', '2(L)'])[1], '모름')
        self.assertEqual(self.match_sizes(['1번', '1size'], ['1(S)', '2(M)'])[1], '모름')
        self.assertEqual(self.match_sizes(None, ['1번', '2번'], cols=2)[1], '모름')
        self.assertEqual(self.match_sizes(['1번'], ['1번', '2번'], cols=2)[1], '모름')
        self.assertEqual(self.match_sizes(['1번', None], ['1번', '2번'])[1], '모름')
        self.assertEqual(self.match_sizes(['1번', '2번'], ['1번', '2번', '3번'], cols=3)[1], '모름')
        self.assertEqual(self.match_sizes(['0', '4', '2', '3'], ['0번', '1번', '2번', '3번'])[1], '모름')
        self.assertEqual(self.match_sizes(['XS', '68', 'M', 'L', 'XL', '2XL'],
                                        ['XS', 'S', 'M', 'L', 'XL', '2XL'])[1], '모름')

    def test_existing_matches_and_full_options_are_preserved(self):
        # 기존 키는 넓은 별칭의 정규화로 바꾸지 않는다.
        self.assertEqual(self.match_sizes(['S_original', 'M_original'], ['S_original', 'M_original']),
                         (['S', 'M'], '같음'))
        self.assertEqual(self.match_sizes(['A1', 'A2'], ['A1', 'A2']), (['A1', 'A2'], '같음'))
        self.assertEqual(self.match_sizes(['1', '2'], ['1번'], full_opts=['1번', '2번']),
                         (['1', '2'], '같음'))

    def test_dunst_zero_options_keep_all_rows(self):
        html = '''<div>XS_총장62 어깨46 가슴49 팔길이19<br>
        S_총장64 어깨48 가슴52 팔길이20.5<br>M_총장69 어깨50 가슴55 팔길이22<br>
        L_총장71 어깨52 가슴58 팔길이23.5<br>XL_총장73 어깨54 가슴61 팔길이25<br>
        XXL_총장75.5 어깨56.5 가슴64 팔길이26.5</div>'''
        raw = crawl.extract_size_any(html)
        names = ['XS', 'S', 'M', 'L', 'XL', 'XXL']
        self.assertEqual(raw['_names'], names)
        out = {'u': {'size_names': raw['_names'], 'sizes': size.normalize_html(raw)}}
        size.trim_unsold_sizes(out, {'u': {'brand_slug': 'dunst'}},
                               {'u': ['0XS', '00S', '00M', '00L', '0XL', 'XXL']})
        self.assertEqual(out['u']['size_names'], names)
        self.assertEqual(out['u']['sizes']['총장'], [62, 64, 69, 71, 73, 75.5])
        size.trim_unsold_sizes(out, {'u': {'brand_slug': 'dunst'}}, {'u': ['00M', '00L']})
        self.assertEqual(out['u']['size_names'], ['M', 'L'])
        self.assertEqual(size._opt_keys('30L'), {'30L'})

    def test_aeae_parenthesized_zero_and_breaks(self):
        html = '''<div>SHOULDER / CHEST / SLEEVE / LENGTH (cm)<br>
        (0) : 49 / 54 / 20 / 69<br>(1) : 51 / 57 / 21 / 71.5<br>
        (2) : 54 / 61 / 22 / 73<br></div><p>PAYMENT INFO</p>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['0', '1', '2'])
        self.assertEqual(raw['length'], [69, 71.5, 73])
        self.assertEqual(size.normalize_html(raw)['어깨'], [49, 51, 54])

    def test_badblood_size_guide_precedes_details(self):
        html = '''<ul><li><a>Details</a><ol class="tab_wrap">
        SIZE (총장 / 허리 단면 / 밑단)<br>XS l 102cm / 31cm / 33cm<br>
        S l 107cm / 34cm / 34cm</ol></li>
        <li><a>Size Guide</a><ol class="tab_wrap">SIZE (총장 / 허리 단면 / 밑단)<br>
        XXS l 100cm / 33cm / 30cm<br>XS l 105cm / 35cm / 31cm<br>
        S l 109cm / 38cm / 32cm<br>M l 112cm / 40cm / 33cm</ol></li></ul>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['XXS', 'XS', 'S', 'M'])
        self.assertEqual(raw['총장'], [100, 105, 109, 112])
        full = '<title>팬츠</title><script>var product_price = "100000";</script>' + html
        product = crawl.parse_detail(full, 'https://example.com/product/detail.html?product_no=34',
                                     crawl.Shop('badblood', 'https://example.com', ''))
        self.assertEqual(product['size_table'], raw)

    def test_badblood_decimal_space_keeps_last_row(self):
        html = '''<div>SIZE (총장 / 허리 / 힙 / 밑위 / 허벅지 / 밑단)<br>
        1 l 109 / 30 / 41 / 20 / 26.5 / 26<br>3 l 109 / 30.5 / 41.5 / 21 / 27 / 26.5<br>
        5 l 110 / 31 / 42 / 22 / 27.5 / 27<br>7 l 110 / 31.5 / 42 .5 / 22.5 / 28.5 / 28
        </div><p>MODEL INFO 9 l 180 / 70 / 90 / 30 / 40 / 20</p>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['1', '3', '5', '7'])
        self.assertEqual(raw['힙'], [41, 41.5, 42, 42.5])
        self.assertIsNone(crawl._table_number('42 5'))
        self.assertIsNone(crawl._table_number('42.5 / 43'))

    def test_badblood_glued_m_typo_keeps_row(self):
        txt = ('Size Guide SIZE (총장 / 어깨 너비 / 소매 길이 / 가슴 단면 / 밑단) S | 57 cm / 37cm / 62cm / 43cm / 39cm '
               'M | 63 cm / 44cm / 64cm / 50cm / 41cm L | 66 cm / 46cm / 65m / 53cm / 44cm SIZE TIP')
        raw = crawl.extract_size_any(txt)
        self.assertEqual(raw['_names'], ['S', 'M', 'L'])
        self.assertEqual(raw['소매길이'], [62, 64, 65])
        raw = crawl.extract_size_any(txt.replace('66 cm', '66 m'))
        self.assertEqual(raw['총장'], [57, 63, 66])
        txt = ('SIZE (총장 / 허리 단면 / 밑위) S l 109cm / 38cm / 35cm M l 112cm / 40cm / 36cmL l 113cm / 43cm / 37cm '
               '* 본 상품은')
        self.assertEqual(crawl.extract_size_any(txt)['_names'], ['S', 'M', 'L'])

    def test_badblood_split_number_and_missing_separator(self):
        html = '''<li><a>Size Guide</a><ol class="tab_wrap"><p>SIZE (총장 / 허리 단면 / 밑위)<br>
        XS l 104cm / 31cm / 20cm<br>S l 1<span style="color: red">05cm / 33cm / 21cm</span><br>
        M 106cm / 35cm / 22cm<br></p></ol></li>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['XS', 'S', 'M'])
        self.assertEqual(raw['총장'], [104, 105, 106])

    def test_badblood_rejected_hip_cell_keeps_size_position(self):
        html = '''SIZE (총장 / 허리 단면 / 엉덩이 단면 / 밑단)<br>
        XS l 21cm / 32cm / 28cm / 6cm<br>S l 22cm / 34cm / 30cm / 6cm<br>
        M l 23cm / 36cm / 32cm / 6cm'''
        raw = crawl.extract_size_any(html)
        cols = size.normalize_html(raw)
        self.assertEqual(raw['_names'], ['XS', 'S', 'M'])
        self.assertEqual(cols['허리'], [32, 34, 36])
        self.assertNotIn('엉덩이', cols)       # 범위 밖 칸이 있는 축은 통째로 뺀다 — M 행을 지킨다
        self.assertEqual(size.column_count(cols, len(raw['_names'])), 3)

    def test_anotheroffice_set_keeps_lower_part(self):
        html = '''<div data-name="size-guide"><table><tr><td>Size(자켓)</td>
        <td>총길이</td><td>어깨</td><td>가슴</td><td>소매</td></tr>
        <tr><td>01</td><td>74.5</td><td>46</td><td>56.5</td><td>61.5</td></tr>
        <tr><td>02</td><td>76</td><td>48</td><td>58.5</td><td>62.5</td></tr></table>
        <table><tr><td>Size(팬츠)</td><td>총길이</td><td>허리</td><td>허벅지</td><td>밑위</td></tr>
        <tr><td>00</td><td>104</td><td>38.5</td><td>35</td><td>32.5</td></tr>
        <tr><td>01</td><td>105.5</td><td>40.5</td><td>36</td><td>33</td></tr>
        <tr><td>02</td><td>107</td><td>42.5</td><td>37.5</td><td>33.5</td></tr></table></div>'''
        raw = crawl.extract_size_any(html + html)
        self.assertEqual(raw['_names'], ['01', '02'])
        self.assertEqual(len(raw['_parts']), 1)
        low = size.html_low_part(raw['_parts'], 'anotheroffice', None, None)
        self.assertEqual(low['part'], '하의')
        self.assertEqual(low['size_names'], ['00', '01', '02'])
        self.assertEqual(low['sizes']['총장'], [104, 105.5, 107])
        separated = html.replace('<table><tr><td>Size(팬츠)', '</div><div><table><tr><td>Size(팬츠)')
        self.assertNotIn('_parts', crawl.extract_size_any(separated))

    def test_suare_set_preserves_waist_ranges(self):
        html = '''<div><table><tr><td>사이즈</td><td>M</td><td>L</td><td>XL</td></tr>
        <tr><td>어깨</td><td>45</td><td>46</td><td>47</td></tr>
        <tr><td>가슴</td><td>53</td><td>55</td><td>58</td></tr>
        <tr><td>허리</td><td>-</td><td>-</td><td>-</td></tr></table>
        <table><tr><td>사이즈</td><td>S</td><td>M</td><td>L</td><td>XL</td></tr>
        <tr><td>허리</td><td>33-42</td><td>35.5-44.5</td><td>38.5-47</td><td>40.5-49.5</td></tr>
        <tr><td>허벅지</td><td>29.5</td><td>30</td><td>30.5</td><td>31</td></tr>
        <tr><td>밑위</td><td>25</td><td>26</td><td>27</td><td>28</td></tr>
        <tr><td>총기장</td><td>91</td><td>92</td><td>93</td><td>94</td></tr></table></div>'''
        raw = crawl.extract_size_any(html)
        low = size.html_low_part(raw['_parts'], 'suare', None, None)
        self.assertEqual(raw['_names'], ['M', 'L', 'XL'])
        self.assertEqual(low['size_names'], ['S', 'M', 'L', 'XL'])
        self.assertEqual(low['ranges']['허리'], [[33, 42], [35.5, 44.5], [38.5, 47], [40.5, 49.5]])
        self.assertNotIn('허리', low['sizes'])

    def test_gender_tables_keep_both_mediums(self):
        html = '''<div><div id="contents-size"><table>
        <tr><td>Women Size</td><td>XS</td><td>S</td><td>M</td></tr>
        <tr><td>총장</td><td>62.5</td><td>64.5</td><td>66</td></tr>
        <tr><td>가슴</td><td>53.5</td><td>56</td><td>58</td></tr></table></div>
        <div id="contents-size"><table><tr><td>Men Size</td><td>M</td><td>L</td><td>XL</td></tr>
        <tr><td>총장</td><td>66.5</td><td>68.5</td><td>70.5</td></tr>
        <tr><td>가슴</td><td>58.5</td><td>61</td><td>63.5</td></tr></table></div></div>'''
        raw = crawl.extract_size_any(html + html)
        self.assertEqual(raw['_names'], ['Women-XS', 'Women-S', 'Women-M', 'Men-M', 'Men-L', 'Men-XL'])
        self.assertEqual(size.normalize_html(raw)['총장'], [62.5, 64.5, 66, 66.5, 68.5, 70.5])

    def test_length_tables_keep_both_groups(self):
        html = '''<div><table><tr><td>Size(30L)</td><td>총길이</td><td>허리</td></tr>
        <tr><td>01</td><td>103</td><td>40.5</td></tr><tr><td>02</td><td>103</td><td>42.5</td></tr></table>
        <table><tr><td>Size(32L)</td><td>총길이</td><td>허리</td></tr>
        <tr><td>01</td><td>107</td><td>40.5</td></tr><tr><td>02</td><td>107</td><td>42.5</td></tr></table></div>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['30L-01', '30L-02', '32L-01', '32L-02'])
        self.assertEqual(raw['총길이'], [103, 103, 107, 107])
        self.assertEqual(size.normalize_html(raw)['허리'], [40.5, 42.5, 40.5, 42.5])
        plain = html.replace('Size(30L)', 'Size').replace('Size(32L)', 'Size')
        self.assertEqual(crawl.extract_size_any(plain)['_names'], ['01', '02'])

    def test_length_rows_keep_blank_position(self):
        html = '''<table><tr><td>Size</td><td>44(0)</td><td>55(1)</td><td>66(2)</td></tr>
        <tr><td>총장 (Short)</td><td>96.3</td><td>97.6</td><td>98.9</td></tr>
        <tr><td>총장 (Original)</td><td>100.3</td><td>101.6</td><td>102.9</td></tr>
        <tr><td>총장 (Long)</td><td>-</td><td>106.6</td><td>107.9</td></tr>
        <tr><td>허리단면</td><td>33.6</td><td>36.2</td><td>38.7</td></tr></table>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'][6:], ['Long-44(0)', 'Long-55(1)', 'Long-66(2)'])
        self.assertEqual(size.normalize_html(raw)['총장'],
                         [96.3, 97.6, 98.9, 100.3, 101.6, 102.9, None, 106.6, 107.9])
        self.assertEqual(size.normalize_html(raw)['허리'], [33.6, 36.2, 38.7] * 3)

        # 이전 파서는 빈 칸을 지웠고, 갱신 판정은 총장 세 행을 세 라벨로 셌다.
        old = crawl._extract_size_table_node(BeautifulSoup(html, 'lxml').table)
        old['총장long'] = [106.6, 107.9]
        self.assertTrue(crawl.table_is_better(raw, old))
        self.assertFalse(crawl.table_is_better(old, raw))

    def flexible_set_html(self, garment):
        # 실제 Size(팬츠-30L)/Size(슬랙스-30L) 머리와 서로 다른 행 수.
        html = '''<table><tr><td>Size(자켓)</td><td>총길이</td><td>어깨</td><td>가슴</td><td>소매</td></tr>
        <tr><td>01</td><td>74.5</td><td>46</td><td>56.5</td><td>61.5</td></tr>
        <tr><td>02</td><td>76</td><td>48</td><td>58.5</td><td>62.5</td></tr>
        <tr><td>03</td><td>77.5</td><td>50</td><td>60.5</td><td>63.5</td></tr></table>'''
        for length, values in [('30L', [103, 103, 104, 104]), ('32L', [107, 107, 108])]:
            html += f'''<table><tr><td>Size({garment}-{length})</td><td>총길이</td><td>허리</td>
            <td>엉덩이</td><td>허벅지</td><td>밑위</td><td>밑단</td></tr>'''
            for i, total in enumerate(values):
                html += (f'<tr><td>0{i + 1}</td><td>{total}</td><td>{40.5 + i * 2}</td>'
                         f'<td>{58.5 + i * 2}</td><td>{36 + i}</td><td>{33 + i * .5}</td>'
                         f'<td>{24.5 + i * .5}</td></tr>')
            html += '</table>'
        return f'<div data-name="size-guide">{html}</div>'

    def test_set_with_two_lower_lengths_survives_refetch(self):
        import refetch_sizes
        from weekly_update import load_rows

        for garment in ['팬츠', '슬랙스']:
            with self.subTest(garment=garment), tempfile.TemporaryDirectory() as tmp:
                html = self.flexible_set_html(garment)
                old = crawl._extract_size_table_node(BeautifulSoup(html, 'lxml').find_all('table')[1])
                raw = crawl.extract_size_any(html + html)
                self.assertEqual(raw['_names'], ['01', '02', '03'])
                low = size.html_low_part(raw['_parts'], 'anotheroffice', None, None)
                self.assertEqual(low['size_names'], ['30L-01', '30L-02', '30L-03', '30L-04',
                                                     '32L-01', '32L-02', '32L-03'])
                self.assertEqual(low['sizes']['총장'], [103, 103, 104, 104, 107, 107, 108])
                self.assertEqual(low['sizes']['허리'], [40.5, 42.5, 44.5, 46.5, 40.5, 42.5, 44.5])
                self.assertTrue(crawl.table_is_better(raw, old))
                self.assertFalse(crawl.table_is_better(old, raw))
                root = Path(tmp)
                path = root / 'anotheroffice.jsonl'
                url = 'https://example.com/product/detail.html?product_no=1950'
                http = Mock()
                http.get.return_value = SimpleNamespace(status_code=200, url=url, text=html + html)
                for out_dir in [None, root / 'parts']:
                    path.write_text(json.dumps({'product_no': 1950, 'price': 100000,
                                                'source_url': url, 'size_table': old}))
                    with patch.object(crawl, 'CRAWL_DIR', root), patch.object(crawl, 'load_robots'):
                        stats = refetch_sizes.refetch(http, crawl.Shop('anotheroffice', 'https://example.com', ''),
                                                     False, lambda _: None, select='all', out_dir=out_dir)
                    self.assertEqual(stats['got'], 1)
                    saved = load_rows(out_dir / 'anotheroffice.0.jsonl' if out_dir else path)[1950]
                    self.assertEqual(saved['size_table'], raw)
                    self.assertEqual(saved['size_source'], 'html')

    def test_table_quality_keeps_existing_protections(self):
        named = {'총장': [70, 72], '가슴': [50, 52], '_names': ['S', 'M']}
        self.assertFalse(crawl.table_is_better({}, named))
        self.assertFalse(crawl.table_is_better({'총장': [70, 72], '_names': ['S', 'M']}, named))
        self.assertFalse(crawl.table_is_better({'총장': [70, 72], '가슴': [50, 52], '어깨': [40, 42]}, named))
        self.assertTrue(crawl.table_is_better({**named, '총장': [71, 73]}, named))

    def test_transposed_trailing_space_is_not_a_size(self):
        for extra in ['', '<td></td>']:
            html = f'''<table><tr><td>SIZE</td><td>S</td><td>M</td>{extra}</tr>
            <tr><td>총장</td><td>67.5</td><td>69.5</td><td></td></tr>
            <tr><td>가슴</td><td>59</td><td>61.5</td></tr></table>'''
            raw = crawl.extract_size_any(html)
            self.assertEqual(raw['_names'], ['S', 'M'])
            self.assertEqual(raw['총장'], [67.5, 69.5])
        html = '''<table><tr><td>SIZE</td><td>1</td><td>2</td><td>3</td></tr>
        <tr><td>총장</td><td>102</td><td>103</td><td>-</td></tr>
        <tr><td>허리</td><td>34</td><td>36</td><td>-</td></tr></table>'''
        self.assertEqual(crawl.extract_size_any(html)['_names'], ['1', '2'])

    def test_conversion_ranges_do_not_hide_measurements(self):
        html = '''<table><tr><td>Clothing</td><td>XS</td><td>S</td><td>M</td></tr>
        <tr><td>Bust (inch)</td><td>22</td><td>23</td><td>32-33</td></tr>
        <tr><td>Waist (inch)</td><td>21</td><td>23</td><td>32-33</td></tr></table>
        <table><tr><td>Size</td><td>Length</td><td>Chest</td><td>Hem</td></tr>
        <tr><td>1</td><td>36</td><td>30.5</td><td>21</td></tr>
        <tr><td>2</td><td>37</td><td>33</td><td>23</td></tr></table>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['1', '2'])
        self.assertEqual(raw['length'], [36, 37])

    def test_goodlifeworks_broken_list_cell(self):
        html = '''<div class="detail_table"><ul><li>사이즈(cm)</li><li>어깨</li><li>가슴</li><li>소매</li><li>총길이</li></ul>
        <ul><li>S</li><li>54</li><li>60</li><li class="row"61</li><li>62</li></ul>
        <ul><li>M</li><li>55</li><li>63</li><li>62</li><li>63.5</li></ul>
        <ul><li>L</li><li>56</li><li>66</li><li>54</li><li>65</li></ul></div>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['S', 'M', 'L'])
        self.assertEqual(raw['소매'], [61, 62, 54])
        self.assertEqual(crawl._repair_size_html('<li class="other"61</li>'), '<li class="other"61</li>')

    def test_belier_decimal_typo_keeps_lengths(self):
        html = '''<table><tr><td>사이즈</td><td>총장</td><td>밑단</td></tr>
        <tr><td>W28 / L30</td><td>105cm</td><td>25.5cm</td></tr>
        <tr><td>W28 / L32</td><td>109cm</td><td>25..5cm</td></tr>
        <tr><td>W30 / L32</td><td>110cm</td><td>26.5cm</td></tr></table>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['W28 / L30', 'W28 / L32', 'W30 / L32'])
        self.assertEqual(raw['밑단'], [25.5, 25.5, 26.5])
        # 기장마다 작은 허리부터 다시 시작해도 각 묶음은 정상이다.
        raw = {'_names': ['W28 / L30', 'W30 / L30', 'W28 / L32', 'W30 / L32'],
               '총장': [105, 106, 109, 110], '허리': [38, 40, 38, 40], '밑단': [25.5, 26.5, 25.5, 26.5]}
        self.assertEqual(size.normalize_html(raw)['허리'], [38, 40, 38, 40])

    def test_etce_and_lie_tight_hyphen(self):
        html = '''<div>SIZE GUIDE<br>M- Length 70 Shoulder 48 Chest 52 Arm 65.5<br>
        L - Length 71 Shoulder 49.5 Chest 54.5 Arm 66.5<br>
        XL - Length 72 Shoulder 51 Chest 57 Arm 67.5</div>'''
        self.assertEqual(crawl.extract_size_any(html)['length'], [70, 71, 72])
        html = '''<div>Size Guide<br>SMALL- 총장:63.5 어깨단면:41 가슴단면:52<br>
        MEDIUM- 총장:64 어깨단면:42 가슴단면:53.5<br>LARGE- 총장:64.5 어깨단면:43 가슴단면:55</div>'''
        self.assertEqual(crawl.extract_size_any(html)['_names'], ['SMALL', 'MEDIUM', 'LARGE'])

    def test_le_split_small_keeps_original_values(self):
        html = '''<div><span>SMAL</span><span>L : 허리 32.5cm , 힙 42cm , 총기장 62.5cm</span><br>
        MEDIUM : 허리 34.5cm , 힙 44cm , 총기장 63.5cm<br>
        LARGE 허리 41cm , 힙 54.5cm , 총기장 103.5cm</div>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['SMALL', 'MEDIUM', 'LARGE'])
        self.assertEqual(raw['총기장'], [62.5, 63.5, 103.5])

    def test_far_grid_keeps_bilingual_cell_boundaries(self):
        html = '''<details><summary>Size &amp; Fit Guide (cm)</summary>
        <div style="display: grid; grid-template-columns: 15% repeat(2, 1fr)">
        <div>(cm)</div><div><span class="ko">허리</span><span class="en">Waist</span></div>
        <div><span class="ko">총장</span><span class="en">Length</span></div></div>
        <div style="display: grid; grid-template-columns: 15% repeat(2, 1fr)"><div>SIZE 2</div><div>32</div><div>108</div></div>
        <div style="display: grid; grid-template-columns: 15% repeat(2, 1fr)"><div>SIZE 3</div><div>33.25</div><div>110</div></div>
        </details>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['SIZE 2', 'SIZE 3'])
        self.assertEqual(raw['허리'], [32, 33.25])
        self.assertEqual(raw['총장'], [108, 110])

    def test_fourth_row_and_empty_shoulder_survive(self):
        html = '''<table><tr><td>Size/cm</td><td>Length</td><td>Shoulder</td><td>Chest</td><td>Arm</td></tr>
        <tr><td>1 Size</td><td>55</td><td></td><td>59</td><td>92</td></tr>
        <tr><td>2 Size</td><td>60</td><td></td><td>63</td><td>93.5</td></tr>
        <tr><td>3 Size</td><td>64</td><td></td><td>66</td><td>95</td></tr>
        <tr><td>4 Size</td><td>68</td><td></td><td>70</td><td>97</td></tr></table>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['1 Size', '2 Size', '3 Size', '4 Size'])
        self.assertNotIn('shoulder', raw)
        self.assertEqual(raw['arm'], [92, 93.5, 95, 97])
        cols = size.normalize_html(raw)
        self.assertEqual(size.column_count(cols, len(raw['_names'])), 4)
        self.assertEqual(size.sleeve_to_hwajang(cols)['화장'], [92, 93.5, 95, 97])

    def test_sansan_fourth_span_row_survives(self):
        html = '''<h2>SIZE GUIDE</h2><div class="sz__guide sg__"><div class="label">
        <span></span><span>Chest</span><span>Sleeve</span><span>Length</span></div>
        <ul class="sz__row"><li><span>1</span><span>56.5</span><span>89.5</span><span>65</span></li>
        <li><span>2</span><span>59</span><span>92</span><span>67.5</span></li>
        <li><span>3</span><span>61.5</span><span>94.5</span><span>70</span></li>
        <li><span>4</span><span>64</span><span>97</span><span>72.5</span></li></ul></div>'''
        raw = crawl.extract_size_any(html)
        self.assertEqual(raw['_names'], ['1', '2', '3', '4'])
        self.assertEqual(raw['sleeve'], [89.5, 92, 94.5, 97])
        cols = size.normalize_html(raw)
        self.assertEqual(cols['소매길이'], [89.5, 92, 94.5, 97])
        self.assertEqual(size.column_count(cols, len(raw['_names'])), 4)

    def test_named_invalid_cell_does_not_shift_sizes(self):
        html = '''<table><tr><td>Size</td><td>Length</td><td>Shoulder</td><td>Sleeve</td></tr>
        <tr><td>S</td><td>60</td><td>40</td><td>93</td></tr>
        <tr><td>M</td><td>62</td><td>42</td><td>95</td></tr>
        <tr><td>L</td><td>64</td><td>44</td><td>97</td></tr></table>'''
        raw = crawl.extract_size_any(html)
        cols = size.normalize_html(raw)
        # 범위 밖 숫자가 있는 축은 빼고 칸 수는 지킨다
        self.assertNotIn('소매길이', cols)
        self.assertEqual(size.column_count(cols, len(raw['_names'])), 3)

    def test_empty_and_partial_table_rows_keep_names(self):
        html = '''<table><tr><th>Size</th><th>Length</th><th>Waist</th></tr>
        <tr><td>-</td><td>-</td><td>-</td></tr>
        <tr><td>M (30)</td><td>105</td><td>40</td></tr>
        <tr><td>L (32)</td><td>107</td><td>-</td></tr>
        <tr><td>XL (34)</td><td>109</td><td>45</td></tr></table>'''
        got = crawl.extract_size_from_tables(BeautifulSoup(html, 'lxml'))
        self.assertEqual(got['_names'], ['M (30)', 'L (32)', 'XL (34)'])
        self.assertEqual(got['waist'], [40, None, 45])

    def test_comments_are_not_measurements(self):
        hidden = '<!-- <table><tr><th>어깨</th><th>가슴</th></tr><tr><td>53.5</td><td>60</td></tr></table> -->'
        self.assertEqual(crawl.extract_size_any(hidden), {})
        self.assertEqual(crawl.extract_size_table('<!-- 어깨 53.5 가슴 60 -->'), {})
        self.assertEqual(crawl.extract_size_any(hidden + '<p>총장 70 가슴 55</p>'), {'총장': [70], '가슴': [55]})
        self.assertNotIn('53.5', crawl._strip_tags(hidden))

    def test_sleeve_hem_is_not_body_hem(self):
        for cuff, hem in [(10, 39), (11.5, 33)]:
            text = f'총장 56.5 가슴 54 소매밑단 {cuff} 밑단 {hem}'
            fresh = crawl.extract_size_table(text)
            self.assertEqual(fresh['소매밑단'], [cuff])
            self.assertEqual(fresh['밑단'], [hem])
            old = {'총장': [56.5], '가슴': [54], '밑단': [cuff]}
            fixed = size.reparse_thin_table(old, text)
            self.assertEqual(size.normalize_html(fixed)['소매단'], [cuff])
            self.assertEqual(fixed['밑단'], [hem])
            self.assertEqual(old['밑단'], [cuff])

    def test_piece_headings_are_not_size_rows(self):
        text = ('CARDIGAN - 총장 55 가슴 51 소매밑단 9 밑단 40 '
                'SLEEVELESS - 총장 41 어깨 20.5 가슴 33 밑단 29')
        got = crawl.extract_size_table(text)
        self.assertEqual(got['총장'], [55])
        self.assertEqual(got['밑단'], [40])

    def test_last_inline_row_survives_note(self):
        text = ('SMALL 총장 103.5 허리단면 36.5 밑단 24 '
                'MEDIUM 총장 104.5 허리단면 38.4 밑단 24.7 '
                'LARGE 총장 105.5 허리단면 40.3 밑단 25.3 '
                '* 사이즈는 재는 방법과 신축성에 따라 오차가 있습니다.')
        names, cols = size.parse_inline_named_rows(text)
        self.assertEqual(names, ['S', 'M', 'L'])
        self.assertEqual(cols['총장'], [103.5, 104.5, 105.5])

    def test_inline_annotations_and_two_decimal_places(self):
        text = ('S 허리 35 허벅지 34.55 (Short) 총장 104 밑단 27.5 '
                'M 허리 37 허벅지 35.6 (Short) 총장 104.6 밑단 28.45 '
                'L 허리 39 허벅지 36.65 (Short) 총장 105.2 밑단 29.4 * 총장 오차 안내')
        names, cols = size.parse_inline_named_rows(text)
        self.assertEqual(names, ['S', 'M', 'L'])
        self.assertEqual(cols['밑단'], [27.5, 28.45, 29.4])  # 원문 소수 두 자리를 보존한다
        self.assertEqual(cols['총장'], [104, 104.6, 105.2])

    def test_unmeasurable_raglan_column_keeps_later_values(self):
        text = ('S 총장 60 어깨 레글런 가슴 50 밑단 40 '
                'M 총장 62 어깨 레글런 가슴 52 밑단 42 * 가슴 오차 안내')
        names, cols = size.parse_inline_named_rows(text)
        self.assertEqual(names, ['S', 'M'])
        self.assertEqual(cols['밑단'], [40, 42])

    def test_slash_rows_skip_description_and_trailing_slash(self):
        for sep in ['l', '|', 'ㅣ']:
            text = (f'SIZE (총장 / 허리 단면 / 밑단) XXS {sep} 100cm / 33cm / 30cm / '
                    f'부드러운 코튼 소재와 스티치 디테일. XS {sep} 105cm / 35cm / 31cm / '
                    f'S {sep} 109cm / 38cm / 32cm MODEL INFO L {sep} 180cm / 60cm / 90cm')
            names, cols = size.parse_paren_slash(text)
            self.assertEqual(names, ['XXS', 'XS', 'S'])
            self.assertEqual(cols['총장'], [100, 105, 109])

    def test_size_word_and_joined_korean_rows(self):
        names, cols = size.parse_size_rows('- S size 허리 34 힙 46.5 총장 105 - M size 허리 36 힙 49 총장 105 * 사이즈 오차 안내')
        self.assertEqual(names, ['S', 'M'])
        self.assertEqual(cols['엉덩이'], [46.5, 49])
        names, cols = size.parse_size_rows('SIZE DETAIL 1사이즈 어깨48 가슴52 밑단49 소매18 총장63 '
                                         '2사이즈어깨50 가슴54 밑단51 소매18.5 총장65 '
                                         '3사이즈어깨52 가슴56 밑딘53 소매19 총장67 (model - 1size 착용)')
        self.assertEqual(names, ['1', '2', '3'])
        self.assertEqual(cols['총장'], [63, 65, 67])
        self.assertEqual(cols['밑단'], [49, 51, None])

    def test_decimal_comma_and_value_list(self):
        text = '총장 53,7 55.2 어깨너비 46.5 48.1 가슴단면 57 60.2 소매길이 59.5 60.1'
        self.assertEqual(crawl.extract_size_table(text)['총장'], [53.7, 55.2])
        self.assertEqual(crawl.extract_size_table('총장 53, 55 가슴 57,60')['총장'], [53, 55])
        old = {'총장': [53, 7, 55.2], '어깨너비': [46.5, 48.1], '가슴단면': [57, 60.2], '소매길이': [59.5, 60.1]}
        self.assertEqual(size.reparse_thin_table(old, text)['총장'], [53.7, 55.2])

    def test_explicit_width_survives_all_halving_stages(self):
        for hips in [[69, 71], [74, 76, 78]]:
            raw = {'엉덩이단면': hips, '허리단면': [26] * len(hips)}
            cols = size.normalize_html(raw, 'massnoun', {('massnoun', '엉덩이'), ('massnoun', '허리')})
            self.assertEqual(cols['엉덩이'], hips)
            out = {'u': {'sizes': cols}}
            size.unit_guard(out, {'u': size.explicit_flat_values(raw)})
            self.assertEqual(out['u']['sizes']['엉덩이'], hips)
        out = {'u': {'sizes': {'엉덩이': [76]}}, 'v': {'sizes': {'엉덩이': [38]}}}
        rows = {u: {'brand_slug': 'test', 'item_type': '바지', 'name': '팬츠'} for u in out}
        size.halve_unit_outliers(out, rows, {'u': {'엉덩이': {76}}})
        self.assertEqual(out['u']['sizes']['엉덩이'], [76])
        size.unit_guard(out)
        self.assertEqual(out['u']['sizes']['엉덩이'], [38])
        self.assertEqual(size.normalize_html({'엉덩이둘레': [100]})['엉덩이'], [50])

    def test_model_measurements_end_at_product_section(self):
        product = 'Shoulder 42 Chest 56.5 Length 57'
        model = 'Model: Height 179 / Bust 79 / Waist 60 / Hips 87'
        for text in [product + ' ' + model, model + ' SIZE GUIDE ' + product]:
            got = crawl.extract_size_table(text)
            self.assertEqual(got, {'shoulder': [42], 'chest': [56.5], 'length': [57]})
        self.assertEqual(crawl.extract_size_table(model + ' SIZE GUIDE Waist 60 Length 100')['waist'], [60])


if __name__ == '__main__':
    unittest.main()
