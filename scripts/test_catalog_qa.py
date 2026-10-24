"""QA에서 확인한 공용 표·코디 문장·추천 레일을 재현한다. data에는 쓰지 않는다."""
import unittest
from unittest.mock import Mock, patch

import crawl_cafe24 as cc
import export_app_data as app
import tag_items as ti


class CatalogTagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        vocab = {ax: {} for ax in ti.AXES}
        vocab.update({
            'pattern': {'카모': ['camo'], '플로럴': ['floral'], '단색': ['solid color'], '체크': ['check']},
            'sleeve_length': {'반팔': ['반팔'], '롱슬리브': ['긴팔']},
        })
        vocab['_text_blocklist'] = ['레이어드하여', '레이어드해']
        cls.tagger = ti.Tagger(vocab)

    def tag(self, name, body='', cm=None):
        return self.tagger.tag('Shirts', name, body, '', 'ok', sleeve_cm=cm)

    def test_shared_color_list_is_not_current_pattern(self):
        body = 'Color: Washed Black\nCOLOR\nCAMO , GREY , BLACK\nFABRIC\nCOTTON 70%'
        self.assertNotIn('카모', self.tag('Pants', body).get('pattern', []))
        self.assertIn('카모', self.tag('Camo Pants', body)['pattern'])
        self.assertIn('카모', self.tag('Pants', 'COLOR\nCAMO\nFABRIC')['pattern'])
        self.assertIn('카모', self.tag('Pants', 'CAMO pattern with grey pockets')['pattern'])

    def test_shared_ocr_color_choices_are_removed_before_header_denoising(self):
        lex = {'camo', 'grey', 'black'} | {f'word{i}' for i in range(500)}
        with patch.object(ti, '_LEX', lex):
            text = ti.denoise_ocr('COLOR\nCAMO , GREY , BLACK\nCOTTON 70%')
        self.assertNotIn('CAMO', text)
        self.assertIn('COTTON 70%', text)

    def test_absent_pattern_is_solid_unless_pattern_named(self):
        self.assertNotIn('단색', self.tag('Star puffer', 'STAR PATTERN PRINTING').get('pattern', []))
        self.assertIn('단색', self.tag('Dress', 'Cotton 100% 부드러운 촉감의 원피스입니다').get('pattern', []))
        self.assertIn('단색', self.tag('Solid Color Shirt')['pattern'])
        self.assertEqual(self.tag('Floral Shirt')['pattern'], ['플로럴'])
        self.assertNotIn('단색', self.tag('Shirt', '멀리서 보면 단색 같지만 체크 원단')['pattern'])

    def test_layering_partner_does_not_set_sleeve(self):
        for body in ('이너(반팔 티셔츠, 얇은 긴팔 티셔츠)와 레이어드하여 자켓 대용',
                     '반팔 티셔츠나 얇은 긴팔 티셔츠와 레이어드해 자켓 대용',
                     '반팔 티셔츠나 얇은 긴팔 티셔츠와 레이어드하여 활용',
                     '반팔 티셔츠나 BFS 긴팔 티셔츠와 레이어드하여 활용'):
            with self.subTest(body=body):
                self.assertEqual(self.tag('Shirt', body, 61)['sleeve_length'], ['롱슬리브'])
                self.assertEqual(self.tag('반팔 Shirt', body, 61)['sleeve_length'], ['반팔'])
        self.assertEqual(self.tag('Shirt', '반팔 디자인')['sleeve_length'], ['반팔'])
        self.assertEqual(self.tag('Shirt', '긴팔 셔츠로 제작되어 반팔 티셔츠와 레이어드')['sleeve_length'], ['롱슬리브'])


class CatalogExportTests(unittest.TestCase):
    def test_azure_option_is_preserved_without_changing_product_line(self):
        self.assertEqual(cc.pick_color('Fluid OverShirt', '', options=['White', 'Azure']), '화이트·스카이블루')
        self.assertEqual(cc.match_color('Azure Shirt'), '')

    def test_option_order_is_not_image_color(self):
        self.assertEqual(app.index_color({'name': 'Knot Shirt', 'representative_color': '카키·차콜'}, {}), '')
        self.assertEqual(app.index_color({'name': 'Shirt (Navy)', 'representative_color': '화이트·네이비'}, {}), '네이비')
        self.assertEqual(app.index_color({'representative_color': '차콜'}, {'color': ['카키']}), '차콜')
        self.assertEqual(app.index_color({'name': 'Shirt Black/White', 'representative_color': '블랙·화이트'}, {}), '')
        self.assertEqual(app.index_color({'name': 'Shirt [2color]', 'representative_color': '블랙·화이트'}, {}), '')
        self.assertEqual(app.index_color({}, {'color': ['레드', '블랙']}), '')
        self.assertEqual(app.index_color({}, {'color': ['블랙']}), '블랙')

    def worn(self, category, fr, stale=False):
        with patch.object(app, '_WORN', {'u': ('old' if stale else 'img', fr)}):
            return app.worn({'source_url': 'u', 'image_url': 'img', 'category': category})

    def test_worn_preserves_cropped_people_and_stale_image_guard(self):
        self.assertEqual(self.worn('Tops', {'7': .4052, '11': .0001}), 0)
        self.assertEqual(self.worn('Tops', {'4': .2, '14': .02}), 1)
        self.assertEqual(self.worn('Dresses', {'2': .0007, '11': .0123, '7': .0359}), 1)
        self.assertEqual(self.worn('Accessories', {'7': .3, '11': .04}), 1)
        self.assertEqual(self.worn('Shoes', {'12': .03, '13': .04}), 1)
        self.assertEqual(self.worn('Pants', {'4': .12, '6': .15, '9': .03, '10': .03}), 1)
        self.assertEqual(self.worn('Tops', {'14': .02}, stale=True), 0)

    def test_only_roughside_chip_slot_is_removed(self):
        chip = 'https://roughside.co.kr/web/product/small/a.jpg'
        photo = 'https://roughside.co.kr/web/product/extra/small/a.jpg'
        full = 'https://9999archive.kr/web/product/small/a.jpg'
        self.assertEqual(app.gallery_for_app({}, {'gallery': [chip, photo, full, photo]}), [photo, full])
        self.assertEqual(app.gallery_for_app({}, {'gallery': [photo], 'gallery_excluded': [photo]}), [])


class GallerySelectionTests(unittest.TestCase):
    def parse(self, body):
        html = '<title>Shirt</title><script>var product_price="100000";</script>' + body
        with patch.object(cc, '_big_slot_ok', return_value=False):
            return cc.parse_detail(html, 'https://shop.test/product/shirt/1/', cc.Shop('test', 'https://shop.test', ''))

    def test_rail_inside_image_area_is_excluded(self):
        row = self.parse('<div class="xans-product-image"><img src="/web/product/medium/main.jpg">'
                         '<div class="xans-product-relationlist"><img src="/web/product/small/other.jpg"></div>'
                         '<div class="recommend"><div><div><div><div><div><div><div><div>'
                         '<div><img src="/web/product/small/deep.jpg"></div>'
                         '</div></div></div></div></div></div></div></div></div></div>')
        self.assertFalse(any('other' in u or 'deep' in u for u in row['gallery']))

    def test_empty_gallery_does_not_resurrect_recommended_big_image(self):
        row = self.parse('<div class="xans-product-image"><img src="/web/product/big/main.jpg"></div>'
                         '<div class="xans-product-relationlist"><img src="/web/product/big/other.jpg"></div>')
        self.assertEqual(row['gallery'], [])
        self.assertFalse(any('other' in u for u in row['detail_images']))

    def test_script_only_skin_is_preserved(self):
        row = self.parse('<script>var photos=["https://shop.test/web/product/medium/a.jpg",'
                         '"https://shop.test/web/product/extra/small/b.jpg"];</script>')
        self.assertTrue(any('b.jpg' in u for u in [row['image_url']] + row['gallery']))

    def test_current_color_chip_duplicate_is_excluded(self):
        row = self.parse('<div class="xans-product-image"><img src="/web/product/medium/main.jpg">'
                         '<div><div class="current-item-label">네이비</div><div class="current-item">'
                         '<img src="/web/product/small/chip.jpg"></div></div>'
                         '<img class="ThumbImage" src="/web/product/small/chip.jpg">'
                         '<img src="/web/product/extra/small/photo.jpg"></div>')
        self.assertFalse(any('chip' in u for u in row['gallery']))
        self.assertTrue(any('photo' in u for u in row['gallery']))

    def test_big_slot_is_checked_per_image_and_failure_keeps_native(self):
        def response(url, **kwargs):
            return Mock(status_code=200 if url.endswith('first.jpg') else 404,
                        headers={'Content-Type': 'image/jpeg'})
        with patch.object(cc, '_BIG_OK', {}), patch.object(cc.requests, 'get', side_effect=response) as get:
            self.assertTrue(cc._big_slot_ok('https://shop.test/web/product/small/first.jpg'))
            self.assertFalse(cc._big_slot_ok('https://shop.test/web/product/small/second.jpg'))
            self.assertFalse(cc._big_slot_ok('https://shop.test/web/product/extra/small/second.jpg'))
            self.assertEqual(get.call_count, 3)
        with patch.object(cc, '_BIG_OK', {}), patch.object(cc.requests, 'get', side_effect=cc.requests.Timeout):
            self.assertFalse(cc._big_slot_ok('https://shop.test/web/product/small/a.jpg'))

    # 갤러리 검수 600벌(2026-11-11): 추가구성상품 칸의 남의 상품 썸네일이 갤러리가 됐다(mooneed · ava-molli · rough-side).
    ADD = ('<div class="xans-product-addproduct productSet additional"><ul class="product">'
           '<li class="xans-record-"><div class="information"><div class="thumbnail">'
           '<a href="{href}"><img src="/web/product/tiny/202610/{img}.jpg"></a></div>'
           '<p class="name">{name}</p></div></li></ul></div>')

    def test_addproduct_thumbnail_is_not_this_products_photo(self):
        # mooneed 꼴 — 이 상품 사진은 대표 한 장, 나머지는 옵션 아래 추가구성상품(다른 상품 링크)
        row = self.parse('<div class="xans-product-image"><img src="/web/product/big/main.jpg"></div>'
                         + self.ADD.format(href='/product/long-skirt/592/', img='skirt', name='PLAIN LONG KNIT SKIRT')
                         + self.ADD.format(href='/product/detail.html?product_no=679', img='bag', name='TOTE BAG'))
        self.assertEqual(row['gallery'], [])
        self.assertTrue(row['image_url'].endswith('/web/product/big/main.jpg'))

    def test_addproduct_inside_image_area_keeps_own_photos(self):
        # rough-side · kiimuir 꼴 — 진짜 추가 사진과 추가구성상품이 한 사진 칸에 있다. 자기 번호로 가는 확대 링크는 남긴다
        row = self.parse('<div class="xans-product-image"><img src="/web/product/medium/main.jpg">'
                         '<a href="/product/image_zoom2.html?product_no=1"><img src="/web/product/extra/small/own.jpg"></a>'
                         '<a href="#none"><img src="/web/product/extra/small/own2.jpg"></a>'
                         + self.ADD.format(href='/product/belt/1527/', img='belt', name='BELT') + '</div>')
        self.assertEqual([u.rsplit('/', 1)[1] for u in row['gallery']], ['own.jpg', 'own2.jpg'])

    def test_other_product_link_outside_named_rail_is_excluded(self):
        # 레일 이름이 사전에 없는 스킨이라도 다른 상품 상세로 가는 링크 안의 썸네일은 그 상품 것이다
        row = self.parse('<div class="xans-product-image"><img src="/web/product/medium/main.jpg">'
                         '<img src="/web/product/extra/small/own.jpg">'
                         '<div class="coordi"><a href="/product/pants/77/category/5/display/1/">'
                         '<img src="/web/product/small/pants.jpg"></a></div></div>')
        self.assertTrue(any('own' in u for u in row['gallery']))
        self.assertFalse(any('pants' in u for u in row['gallery']))

    def test_photo_holder_addproduct_is_kept(self):
        # insilence 꼴 — 스태프 착용 사진을 「¥¥¥¥*」 · 「스탭스냅」 가짜 상품으로 올려 추가구성상품에 건다. 이 상품 사진이다
        row = self.parse('<div class="xans-product-image"><img src="/web/product/big/main.jpg"></div>'
                         + self.ADD.format(href='/product/detail.html?product_no=7731', img='snap1', name='¥¥¥¥*')
                         + self.ADD.format(href='/product/detail.html?product_no=7723', img='snap2', name='스탭스냅 M 178cm')
                         + self.ADD.format(href='/product/detail.html?product_no=7000', img='socks', name='RIB SOCKS'))
        self.assertEqual([u.rsplit('/', 1)[1] for u in row['gallery']], ['snap1.jpg', 'snap2.jpg'])


class GalleryStrangerTests(unittest.TestCase):
    """이미 수집된 갤러리에서 남의 상품 사진을 거른다(gallery_for_app) — 갤러리 검수 600벌 2026-11-11 의 꼴들."""
    B = 'https://shop.test/web/product'

    def rows(self):
        B = self.B
        look = [f'{B}/extra/big/202106/look{i}.jpg' for i in range(2)]          # big-union 꼴 — 컬렉션 화보를 다섯 벌에 똑같이
        fam = f'{B}/extra/big/202308/family.jpg'                               # cayl 꼴 — 같은 가방의 변형들이 나눠 쓰는 사진
        rows = [dict(source_url=f'u{i}', name=n, image_url=f'{B}/big/202106/m{i}.jpg',
                     gallery=[f'{B}/small/202106/m{i}.jpg', *look, f'{B}/extra/big/202106/own{i}.jpg'])
                for i, n in enumerate(['Collab PK T-Shirts', 'Collab Coloring PK T-Shirts', 'Collab Tie-dye PK T-Shirts',
                                       'Collab Fatigue Shorts', 'Collab Ball Cap'])]
        rows += [dict(source_url=f'b{i}', name=n, image_url=f'{B}/big/202308/b{i}.jpg', gallery=[f'{B}/extra/big/202308/b{i}.jpg', fam])
                 for i, n in enumerate(['Roll Top (b-grid) / Beige', 'Roll Top (x-pac) / Black', 'Roll Top (grid) / Grey',
                                        'Roll Top (ripstop) / Olive', 'Roll Top (nylon) / Navy'])]
        # 색만 다른 형제 — 단체컷을 나눠 쓰고, 서로의 목록용 사진이 색상 레일로 꼬리에 붙었다
        rows += [dict(source_url=f's{i}', name=f'Relaxed Tee ({c})', image_url=f'{B}/big/202601/s{i}.jpg',
                      gallery=[f'{B}/small/202601/s{i}.jpg', f'{B}/extra/big/202601/group.jpg', f'{B}/small/202601/s{1 - i}.jpg'])
                 for i, c in enumerate(['Black', 'White'])]
        # 추천 레일이 꼬리에 붙은 상품 — r0 의 목록용 사진이 형제가 아닌 r1 갤러리에 있다
        rows += [dict(source_url='r0', name='Field Jacket', image_url=f'{B}/big/202609/r0.jpg',
                      gallery=[f'{B}/medium/202609/r0.jpg', f'{B}/extra/big/202609/r0a.jpg']),
                 dict(source_url='r1', name='Officer Pants', image_url=f'{B}/big/202609/r1.jpg',
                      gallery=[f'{B}/medium/202609/r1.jpg', f'{B}/extra/big/202609/r1a.jpg', f'{B}/medium/202609/r0.jpg'])]
        kind = {**{f'u{i}': k for i, k in enumerate(['tops', 'tops', 'tops', 'bottoms', 'headwear'])},
                **{f'b{i}': 'bags' for i in range(5)}, 's0': 'tops', 's1': 'tops', 'r0': 'outer', 'r1': 'bottoms'}
        return rows, kind

    def app_gallery(self, rows, kind, key):
        d = next(r for r in rows if r['source_url'] == key)
        with patch.dict(app.GALLERY_SHARED, app.gallery_shared_index(rows, kind), clear=True):
            return [u.rsplit('/', 1)[1] for u in app.gallery_for_app({}, d)]

    def test_lookbook_shared_across_item_kinds_is_removed(self):
        rows, kind = self.rows()
        self.assertEqual(self.app_gallery(rows, kind, 'u3'), ['m3.jpg', 'own3.jpg'])

    def test_family_photo_within_one_item_kind_is_kept(self):
        rows, kind = self.rows()
        self.assertEqual(self.app_gallery(rows, kind, 'b1'), ['b1.jpg', 'family.jpg'])
        # 품목을 모르면(상품표에 없는 줄) 빼지 않는다
        self.assertEqual(self.app_gallery(rows, {}, 'u3'), ['m3.jpg', 'look0.jpg', 'look1.jpg', 'own3.jpg'])

    def test_color_siblings_keep_shared_and_each_others_photos(self):
        rows, kind = self.rows()
        self.assertEqual(self.app_gallery(rows, kind, 's0'), ['s0.jpg', 'group.jpg', 's1.jpg'])

    def test_rail_thumbnail_is_removed_but_owner_keeps_it(self):
        rows, kind = self.rows()
        self.assertEqual(self.app_gallery(rows, kind, 'r1'), ['r1.jpg', 'r1a.jpg'])
        self.assertEqual(self.app_gallery(rows, kind, 'r0'), ['r0.jpg', 'r0a.jpg'])

    def test_tiny_slot(self):
        B = self.B
        with patch.dict(app.GALLERY_SHARED, {}, clear=True):
            # mooneed 꼴 — 갤러리가 통째로 추가구성상품 tiny
            d = dict(image_url=f'{B}/big/202610/main.jpg', gallery=[f'{B}/tiny/202602/top.jpg', f'{B}/tiny/202610/bag.jpg'])
            self.assertEqual(app.gallery_for_app({}, d), [])
            # rough-side 꼴 — 진짜 사진 사이에 한 장. 대표와 같은 달이어도 맨 앞이 아니면 뺀다
            d = dict(image_url=f'{B}/big/202410/main.jpg',
                     gallery=[f'{B}/medium/202410/main.jpg', f'{B}/tiny/202410/pants.jpg', f'{B}/extra/big/202410/a.jpg'])
            self.assertEqual([u.rsplit('/', 1)[1] for u in app.gallery_for_app({}, d)], ['main.jpg', 'a.jpg'])
            # nomanual 꼴 — 자기 tiny 가 맨 앞 한 장(대표와 같은 달 폴더)
            d = dict(image_url=f'{B}/big/202404/main.jpg', gallery=[f'{B}/tiny/202404/own.jpg', f'{B}/small/202404/b.jpg'])
            self.assertEqual(len(app.gallery_for_app({}, d)), 2)
            # insilence — 추가구성상품이 스태프 착용 사진 그릇이다. 통째로 둔다
            g = ['https://insilence.co.kr/web/product/tiny/202606/snap1.jpg', 'https://insilence.co.kr/web/product/tiny/202606/snap2.jpg']
            d = dict(image_url='https://insilence.co.kr/web/product/big/202604/main.jpg', gallery=g)
            self.assertEqual(app.gallery_for_app({}, d), g)


class DescriptionFallbackTests(unittest.TestCase):
    def test_boilerplate_is_not_resurrected_from_brief(self):
        import product_desc
        rows, crawl = [], {}
        notice = 'ECP 인증 제품들은 가공을 통해 수축률을 최소화한 제품들로, 자체 인증 서비스입니다.'
        for i in range(31):
            r = dict(brand_slug='test', product_no=str(i), name=f'고유 상품 {i}', source_url=f'u{i}',
                     category_code='tops' if i % 2 else 'bottoms')
            rows.append(r)
            crawl[r['source_url']] = {'description': (f'고유한 포켓 {i} 디테일의 셔츠입니다.\n' if i else '') + notice,
                                     'spec': {'상품간략설명': notice}}
        with patch.object(product_desc, 'OCR_VOCAB', None):
            result = app.build_descs(rows, crawl, {})
        self.assertNotIn('test-0', result['test'])
        self.assertTrue(any('포켓' in str(v) for v in result['test'].values()))

    def test_wrong_garment_text_can_fall_back_to_accessory_ocr(self):
        import product_desc
        row = dict(brand_slug='test', product_no='1', name='Cotton Midi Socks_Blue', source_url='u', category_code='accessories')
        with patch.object(product_desc, 'OCR_VOCAB', None):
            result = app.build_descs([row], {'u': {'description': '세미 오버핏 봄버 자켓 워시드 코튼 소재 소매 주름 가공'}},
                                    {('test', '1'): {'material': {'겉감': 'COTTON 100%'}}})
        self.assertEqual(result['test']['test-1'], {'t': 'COTTON 100%', 's': 'ocr'})

    def test_wrong_garment_text_preserves_accessory_brief(self):
        row = dict(brand_slug='test', product_no='1', name='Cotton Midi Socks_Blue', source_url='u', category_code='accessories')
        result = app.build_descs([row], {'u': {'description': '세미 오버핏 봄버 자켓 워시드 코튼 소재 소매 주름 가공',
                                'spec': {'상품간략설명': '골지 조직으로 편안한 착용감의 코튼 양말입니다.'}}}, {})
        self.assertIn('양말', str(result['test']['test-1']))
        self.assertNotIn('봄버', str(result['test']['test-1']))


class DisplayNameTests(unittest.TestCase):
    """보이는 이름(n)만 씻는다 — 텍스트 QA 2026-10-27 의 실제 이름들."""

    def test_color_and_model_identity_survive_code_and_promo_cleanup(self):
        for raw in ('22HS 오픈 칼라 코튼 폴로셔츠 (세일링샴페인)', '23FWACC01(1)-NAVY',
                    'CAYL x NB ML610TCL', 'DW5600ADER1DR + GCAP'):
            with self.subTest(raw=raw):
                self.assertEqual(app.display_name(raw), raw)

    def test_color_list_survives_color_count_cleanup(self):
        self.assertEqual(app.display_name("s.runner's bag (beige, grey, black / 4 colors)"),
                         "s.runner's bag (beige, grey, black)")

    def test_promo_shipping_restock_pick_and_color_count_are_removed(self):
        cases = {
            "[아울렛] Dual Pants Skirt (듀얼 팬츠 스커트) Navy": "Dual Pants Skirt (듀얼 팬츠 스커트) Navy",
            "JTK ARTISANAL: JTK ARTISANAL TOTE BAG (시즌오프 25% off, 회원가입 시 30% off 자동적용)": "JTK ARTISANAL: JTK ARTISANAL TOTE BAG",
            "[10/29 예약배송] 에센셜 플리스 조거 팬츠 [브라운]": "에센셜 플리스 조거 팬츠 [브라운]",
            "(10월15일 예약발송)슬랩스틱 더비슈즈 블랙": "슬랩스틱 더비슈즈 블랙",
            "[4차 리오더] Haru skirt pants (Black)": "Haru skirt pants (Black)",
            "04 Curved Derby Shoes Black (2nd Restock)": "04 Curved Derby Shoes Black",
            "4th) heavy fleece jumper (navy)": "heavy fleece jumper (navy)",
            "[서예지 PICK] NICOLE BUCKLE SHORT RAIN BOOTS_BLACK": "NICOLE BUCKLE SHORT RAIN BOOTS_BLACK",
            "[유인영 착용]ADS-SKB301-20 MOLLI UNBALANCE SKIRT (Black check)": "MOLLI UNBALANCE SKIRT (Black check)",
            "JESS KNIT [3COLOR]": "JESS KNIT",
            "SHEER LONG SKIRT (2COLORS)": "SHEER LONG SKIRT",
            "[30%] STRIPED SHIRT CREAM BEIGE 174,000": "STRIPED SHIRT CREAM BEIGE",
            "✨EXCLUSIVE✨빈티지그래픽 오프숄더 스웻셔츠 [블랙]": "EXCLUSIVE 빈티지그래픽 오프숄더 스웻셔츠 [블랙]",
            "KNOTTED LEATHER BRACELET BLACK *2nd RESTOCK*": "KNOTTED LEATHER BRACELET BLACK",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(app.display_name(raw), want)

    def test_style_codes_are_removed(self):
        cases = {
            "[w-hat26-017] 미스틱 빅패치 볼캡 - 레드블랙": "미스틱 빅패치 볼캡 - 레드블랙",
            "ADVENTURE BUCKET HAT OLIVE KHAKI_UDHE6E102K1": "ADVENTURE BUCKET HAT OLIVE KHAKI",
            "Soft basic longsleeve T-shirt [KNFW06TS12]_Melange gray": "Soft basic longsleeve T-shirt_Melange gray",
            "YUME YUME X ADSB ANDERSSON BELL TYRE SLIDE aaa507w(BLACK)": "YUME YUME X ADSB ANDERSSON BELL TYRE SLIDE (BLACK)",
            "Jacquard V-Nneck Cardigan (MRC-5722 PTC YE)": "Jacquard V-Nneck Cardigan (PTC YE)",
            "크링클 밴딩 맥시 스커트 (Ivory)_2GMUSK09": "크링클 밴딩 맥시 스커트 (Ivory)",
            "도즈 체크 셔츠_BROWN(W25-SH05)": "도즈 체크 셔츠_BROWN",
            "SIOT4129 캐시미어 블렌드 핸드메이드 코트_Camel brown": "캐시미어 블렌드 핸드메이드 코트_Camel brown",
            "Side Slit Wrap Knit Pullover SK5AP302_2color": "Side Slit Wrap Knit Pullover",
            "CUT-OFF CROPPED STRIPE SHIRTS WHITE (WT315)_3rd Restock": "CUT-OFF CROPPED STRIPE SHIRTS WHITE",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(app.display_name(raw), want)

    def test_model_names_colors_and_product_state_are_kept(self):
        for raw in ("PANELED HOODIE N-3B PARKA", "EXP-50S WELLINGTON SUNGLASSES_BLACK", "MIL SERIES CAP(456th)_KHAKI",
                    "CREST 221 REVERSE HOODIE / M.Grey(1%)", "RAT RING(SILVER925)", "RUFFLE SKIRT (OFF WHITE)",
                    "[리퍼브] Lambskin Keel Bag_Black", "[SAMPLE SALE] 셔링 디테일 나시 블라우스", "세일러 카라 블라우스 (세일러)",
                    "TOA RIBBED SOCKS_3COLORS SET", "볼룬 데님 팬츠 T44 브라운", "SCAT-300", "19SSPT29 BEIGE",
                    "VELVET ZIPPERED BLOUSON JACKET_ DARK BROWN"):
            with self.subTest(raw=raw):
                self.assertEqual(app.display_name(raw), raw)

    def test_display_name_does_not_change_the_grouping_key(self):
        # 색 형제 묶기는 원래 이름을 읽는다 — 보이는 이름을 씻어도 묶음 열쇠(품번)는 원래 이름에서 나온다
        import size_from_ocr
        raw = "ADVENTURE BUCKET HAT OLIVE KHAKI_UDHE6E102K1"
        self.assertNotEqual(app.display_name(raw), raw)
        self.assertEqual(size_from_ocr.color_base(raw), size_from_ocr.color_base(raw))


if __name__ == '__main__':
    unittest.main()
