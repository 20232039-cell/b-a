"""사이즈 출처(수집기) 회귀 검사 — 2026-10-27 사이즈 판독(816벌)에서 드러난 수집기 빈틈. data/에는 쓰지 않는다."""
import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import fetch_cremafit as fc
import fetch_sizeguide as fs
from stores import ader_error as ae


def page(body: str, head: str = "") -> str:
    return (f"<html><head><title>t</title>{head}</head><body>"
            f"<script>var iProductNo = 1452;</script>{body}</body></html>")


class SingleSizeRunTest(unittest.TestCase):
    def test_meta_description_run(self):
        # diagonal 6217 — 원사이즈 한 줄. 뒤의 「하프 슬리브 : 소매장 22.2」는 다른 칸이라 거기서 멈춘다.
        t = ("WASHED LAYERED TEE - cotton 100.  SIZE(cm) 총장 46 어깨단면 45.5 가슴단면 51.5 밑단단면 53 "
             "암홀단면 26.3 소매장 57.3 소매끝단면 12.5  하프 슬리브 : 소매장 22.2 소매끝단면 21  * 오차")
        st = cc.extract_single_size_run(t)
        self.assertEqual(st["총장"], [46.0])
        self.assertEqual(st["소매장"], [57.3])
        self.assertEqual(len(st), 7)
        self.assertNotIn("_names", st)

    def test_size_info_line(self):
        # arend 902 — 「허리 33」이 사이즈 이름으로 읽히던 줄
        self.assertEqual(cc.extract_single_size_run("SIZE INFO\n허리 33 총장 93 힙 46 밑단 52"),
                         {"허리": [33.0], "총장": [93.0], "힙": [46.0], "밑단": [52.0]})

    def test_multi_size_left_alone(self):
        self.assertEqual(cc.extract_single_size_run("SIZE(cm) S 총장 60 어깨 50 가슴 52 / M 총장 62 어깨 52 가슴 54"), {})
        self.assertEqual(cc.extract_single_size_run("SIZE(cm) 총장 60 어깨 50"), {})   # 쌍 둘은 모자란다

    def test_bogus_names(self):
        self.assertTrue(cc._bogus_names({"_names": ["46", "슬리브"]}))
        self.assertTrue(cc._bogus_names({"_names": ["33", "INFO"]}))
        self.assertFalse(cc._bogus_names({"_names": ["1(S)", "2(M)", "3(L)"]}))
        self.assertFalse(cc._bogus_names({"_names": ["S", "M", "XL", "FREE", "01", "44"]}))

    def test_parse_detail_meta_description(self):
        html = page('<div id="prdDetail"><p>사진</p></div>',
                    '<meta name="description" content="TEE - cotton 100. SIZE(cm) 총장 46 어깨단면 45.5 '
                    '가슴단면 51.5 소매장 57.3  하프 슬리브 : 소매장 22.2">'
                    '<meta property="og:title" content="WASHED LAYERED TEE">')
        d = cc.parse_detail(html.replace("<title>t</title>", "<title>WASHED LAYERED TEE</title>"),
                            "https://diagonal.co.kr/product/washed-layered-tee/6217/", cc.Shop("diagonal", "https://diagonal.co.kr", ""))
        self.assertEqual(d["size_table"].get("총장"), [46.0])
        self.assertNotIn("_names", d["size_table"])


class CrossProductGuardTest(unittest.TestCase):
    def test_labels_conflict(self):
        self.assertTrue(cc.size_labels_conflict(["어깨", "가슴", "소매", "_names"], "bottoms"))       # anotheroffice 치노
        self.assertTrue(cc.size_labels_conflict(["총장", "어깨", "가슴", "밑단", "소매"], "skirt"))   # s-e-o 미니스커트
        self.assertFalse(cc.size_labels_conflict(["총장", "허리둘레", "엉덩이둘레", "sleevelength"], "skirt"))   # and-you
        self.assertTrue(cc.size_labels_conflict(["총장", "Waist(band)", "Hip", "Thigh"], "tops"))   # crank 비키니 탑
        self.assertFalse(cc.size_labels_conflict(["총장", "허리", "힙"], "tops"))      # 허리 · 힙은 상의에도 있다
        self.assertFalse(cc.size_labels_conflict(["총장", "어깨", "가슴", "허리"], "bottoms"))
        self.assertFalse(cc.size_labels_conflict(["총장", "어깨", "가슴"], "dress"))
        self.assertFalse(cc.size_labels_conflict(["총장", "어깨", "가슴"], None))

    def test_table_category_conflict_exemptions(self):
        top = ["총장", "어깨", "가슴", "소매"]
        self.assertTrue(cc.table_category_conflict(top, "bottoms", "w. Officer Chino Pants (Khaki)"))
        self.assertFalse(cc.table_category_conflict(top, "skirt", "[SET] 위티버니 그래픽 퍼 후디 & 스커트"))
        self.assertFalse(cc.table_category_conflict(top, "bottoms", "[Set-up] Gabardine Wool Double Blazer + R-801"))
        low = ["총장", "허리단면", "엉덩이단면", "허벅지단면", "앞밑위"]
        self.assertFalse(cc.table_category_conflict(low, "tops", "RR LOGO CHARM BIKINI - BLACK"))
        self.assertTrue(cc.table_category_conflict(["총장", "Waist(band)", "Hip", "Thigh"], "tops", "BIKINI BRA TOP_ST"))

    def test_name_garment_code_sets(self):
        self.assertEqual(cc.name_garment_code("LK SET-UP JACKET(BLACK)"), "outer")
        self.assertIsNone(cc.name_garment_code("Sprayed Duck Canvas Set-up, Black"))
        self.assertIsNone(cc.name_garment_code("Denim Jumpsuit"))
        self.assertEqual(cc.name_garment_code("w. Officer Chino Pants (Khaki)"), "bottoms")

    def test_foreign_images(self):
        self.assertTrue(cc.foreign_image_reason(
            "https://luvistrue.speedgabia.com/LARTIGENT_2022_AUTUMN_LK/LKSETUPSLACKSBLACK.jpg", "LK SET-UP JACKET(BLACK)", set()))
        self.assertTrue(cc.foreign_image_reason(
            "https://cafe24img.poxo.com/x/web/upload/NNEditor/20230905/BIKINI20BRIEFS_ST.jpg", "BIKINI BRA TOP_ST", set()))
        codes = cc.own_style_codes({"Style.No": "N24SCT01-BR"})
        self.assertEqual(codes, {"N24SCT01"})
        self.assertTrue(cc.foreign_image_reason("https://noirer.hgodo.com/2024SS/JK/N24SJK03-BE/PRODUCT.jpg", "싱글 롱 코트", codes))
        self.assertFalse(cc.foreign_image_reason("https://noirer.hgodo.com/2024SS/CT/N24SCT01-BR/PRODUCT.jpg", "싱글 롱 코트", codes))
        # 제 옷 낱말 · 해시 이름 · 크기 무관 그림은 남긴다
        self.assertFalse(cc.foreign_image_reason("https://x.com/detail/santiago_slacks_01.jpg", "SANTIAGO SLACKS", set()))
        self.assertFalse(cc.foreign_image_reason("https://x.com/web/upload/NNEditor/b97e4a4584c7.jpg", "싱글 롱 코트", codes))
        self.assertFalse(cc.foreign_image_reason("https://x.com/a/denim_jacket_pants.jpg", "Denim jacket", set()))
        # 반팔(shortsleeve) · 매장 아이디 칸 · 글로 박힌 그림은 근거가 아니다
        self.assertFalse(cc.foreign_image_reason("https://x.com/img/2024/balloonlogoshortsleeve_tee_black.jpg", "벌룬 로고 반팔 티셔츠", set()))
        self.assertFalse(cc.foreign_image_reason("https://cafe24img.poxo.com/shirter/web/upload/NNEditor/20220127/IMG_4447.JPG", "wide denim pants", set()))
        self.assertFalse(cc.foreign_image_reason("data:image/png;base64,AAcoatAA", "wide pants", set()))
        self.assertIsNone(cc.name_garment_code("[SET] 루나 워싱 빈티지 그래픽 후디&팬츠"))

    def test_parse_detail_keeps_table_for_downstream(self):
        # 수집 단계는 이름만 알아 남의 표를 거르지 않는다(분류가 끝난 size_from_ocr 가 거른다). 다만 size_reject 가 있으면
        # carry_over 가 지난 판 표를 되살리지 않는다.
        d = {"size_table": {}, "size_reject": {"why": "매장 틀", "table": {"어깨": [50.0]}}}
        cc.carry_over({"size_table": {"어깨": [50.0, 52.0]}}, d, "2026-10-27T00:00:00")
        self.assertEqual(d["size_table"], {})


class SetAndTemplateTest(unittest.TestCase):
    def test_set_components(self):
        html = ("<script>var set_option_data = '{\\\"5607\\\":{\\\"product_name\\\":\\\"(set-up) Duck French Work Jacket, Black\\\"},"
                "\\\"5605\\\":{\\\"product_name\\\":\\\"(set-up) Duck Mid Straight Pants, Black\\\"}}';</script>"
                '<a href="/product/duck-french-work-jacket-black/5599/">Duck French Work Jacket, Black</a>'
                '<a href="/product/detail.html?product_no=5586">Duck Mid Straight Pants, Black</a>')
        parts = cc.set_components(html, BeautifulSoup(html, "lxml"), "https://yearsago.kr")
        self.assertEqual([(p["set_no"], p["product_no"]) for p in parts], [(5607, 5599), (5605, 5586)])
        rows = {
            5610: {"product_no": 5610, "name": "Duck Set-up, Black", "size_table": {}, "set_parts": parts},
            5599: {"product_no": 5599, "name": "Duck French Work Jacket, Black",
                   "size_table": {"어깨": [49.5, 51.0], "가슴": [57.5, 59.5], "_names": ["S", "M"]}},
            5586: {"product_no": 5586, "name": "Duck Mid Straight Pants, Black",
                   "size_table": {"허리": [39.0, 41.0], "허벅지": [37.0, 38.0], "_names": ["1(S)", "2(M)"]}},
        }
        self.assertEqual(cc.fill_set_tables(rows), 1)
        st = rows[5610]["size_table"]
        self.assertEqual(st["어깨"], [49.5, 51.0])
        self.assertEqual(st["_parts"][0]["part"], "하의")
        self.assertEqual(st["_parts"][0]["허리"], [39.0, 41.0])
        self.assertEqual(rows[5610]["size_table_from_set"], [5599, 5586])

    def test_template_tables(self):
        tpl = {"총장": [59.5, 61.0, 62.5], "가슴단면": [56.5, 58.5, 60.5], "_names": ["44", "46", "48"]}
        names = ["울 케이블 가디건", "실크 스트랩 셔츠", "울 실크 슬랙스", "플리스 집업 자켓"]
        rows = {i: {"name": names[i % 4] + f" {i}", "size_table": dict(tpl)} for i in range(12)}
        self.assertEqual(cc.mark_template_tables(rows), 12)
        self.assertTrue(all(not d["size_table"] and d["size_reject"]["why"].startswith("매장 틀") for d in rows.values()))
        # 다음 판에 새로 온 한 벌도 틀로 걸린다(지난 판에 버린 것까지 센다)
        rows[99] = {"name": "새 니트", "size_table": dict(tpl)}
        self.assertEqual(cc.mark_template_tables(rows), 1)
        # 색만 다른 같은 옷 스무 벌은 틀이 아니다
        same = {i: {"name": f"울 가디건 color{i}", "size_table": dict(tpl)} for i in range(20)}
        self.assertEqual(cc.mark_template_tables(same), 0)


class UrlTest(unittest.TestCase):
    def test_ascii_url(self):
        u = cc.ascii_url("https://kirsh.co.kr/product/위티-버니/5687/?a=한글")
        self.assertTrue(u.isascii())
        self.assertIn("%EC%9C%84%ED%8B%B0", u)
        self.assertEqual(cc.ascii_url("https://x.com/a%20b/1/"), "https://x.com/a%20b/1/")


class SizeGuideTest(unittest.TestCase):
    POPUP = ('<div class="xans-product-sizeguide"><div class="userGuide">{ug}</div>'
             '<div class="sizeFemale displaynone"><table><tr><th>KR</th><td>44 (85)</td><td>55 (90)</td></tr>'
             '<tr><th>가슴 (inches)</th><td>22</td><td>23</td></tr></table>'
             '<p class="info">※ 제공된 사이즈가이드는 각 국가별 평균 크기를 기준으로 비교한 가이드입니다.</p></div>'
             '<img src="/web/upload/common_chart.jpg"></div>')

    def test_user_guide_only(self):
        html = self.POPUP.format(ug='<img src="/web/upload/NNEditor/rain-hood.jpg"><p>총 길이 37</p>')
        self.assertEqual(fs.images_in(html, "https://cayl.co.kr"), ["https://cayl.co.kr/web/upload/NNEditor/rain-hood.jpg"])
        lines = fs.text_in(html)
        self.assertIn("총 길이 37", lines)
        self.assertFalse(any("44 (85)" in ln or "inches" in ln for ln in lines))
        self.assertTrue(fs.SIZEY.search("총 길이 37"))

    def test_generic_chart_dropped_without_user_guide(self):
        html = self.POPUP.replace('<div class="userGuide">{ug}</div>', "")
        lines = fs.text_in(html)
        self.assertFalse(any("44 (85)" in ln or "국가별" in ln for ln in lines))

    def test_comments_ignored(self):
        html = '<div><!-- <p>SIZE GUIDE 총장 70 어깨 50</p> --><p>배송 안내</p></div>'
        self.assertFalse(any("총장" in ln for ln in fs.text_in(html)))
        self.assertEqual(fs.images_after_heading('<!-- SIZE GUIDE <img src="/a.jpg"> -->', "https://x.com"), [])

    def test_label_header_one_row(self):
        html = ('<div class="userGuide"><table><tr><td>ONE SIZE</td><td>(CM)</td></tr>'
                '<tr><td>[Length 총장]</td><td>[Shoulder 어깨]</td><td>[Chest 가슴]</td><td>[Sleeve Length 소매]</td></tr>'
                '<tr><td>70</td><td>77</td><td>76</td><td>63</td></tr></table></div>')
        self.assertEqual(fs.text_in(html)[:2], ["FREE", "총장 70 어깨 77 가슴 76 소매 63"])
        import size_from_ocr
        names, cols = size_from_ocr.parse_pair_rows(fs.text_in(html))
        self.assertEqual(cols["총장"], [70.0])

    def test_text_conflict(self):
        self.assertTrue(fs.text_conflict("Size | 앞총장 | 뒷총장 | 어깨 | 가슴 | 소매\n01 | 73 | 79 | 50 | 57.5 | 25", "Pants"))
        self.assertFalse(fs.text_conflict("FREE\n총장 73 허리 45 밑단 37\n** MODEL : Chest 93cm / Waist 70cm", "Pants"))
        self.assertFalse(fs.text_conflict("M 총장 74.5 어깨 55.5 가슴 64", "Tops"))


class WidgetAndApiTest(unittest.TestCase):
    def test_crema_widget_recorded(self):
        body = ('<div id="prdDetail"><p>x</p></div><div class="crema-fit-product-combined-detail" data-product-code="6619"></div>'
                '<script src="//widgets.cre.ma/roem.com/init.js"></script><script src="//widgets.cre.ma/cafe24/init.js"></script>')
        html = page(body).replace("<title>t</title>", "<title>칼라리스 퍼자켓</title>")
        d = cc.parse_detail(html, "https://roem.com/product/x/6619/", cc.Shop("roem", "https://roem.com", ""))
        self.assertEqual(d["crema_fit"], {"code": "6619", "brand": "roem.com"})
        with tempfile.TemporaryDirectory() as td:
            Path(td, "roem.jsonl").write_text(json.dumps({"source_url": "u1", "crema_fit": d["crema_fit"]}) + "\n", encoding="utf-8")
            self.assertEqual(fc.widget_codes(Path(td)), {"roem": {"u1": {"code": "6619", "brand": "roem.com"}}})

    def test_ader_dimensions(self):
        dims = {"S": [{"title": "총장", "value": 66}, {"title": "가슴단면", "value": 54}],
                "M": [{"title": "총장", "value": 67.5}, {"title": "가슴단면", "value": 57}]}
        names, cells = ae.dims_cells(dims)
        self.assertEqual(names, ["S", "M"])
        self.assertEqual(ae._size_table(names, cells)["총장"], [66.0, 67.5])
        zero = {"A1": [{"title": "총장", "value": 0}], "A2": [{"title": "총장", "value": 0}]}
        self.assertEqual(ae.dims_cells(zero), ([], {}))

    def test_ader_api_respects_robots(self):
        class Http:
            calls = []

            def get(self, url, retries=1, headers=None):
                self.calls.append(url)
                class R:
                    status_code = 200
                    text = "User-agent: *\nDisallow: /_api/\n"
                return R()
        ae._robots = None
        h = Http()
        self.assertEqual(ae.api_size_table(h, 6042), {})
        self.assertTrue(all("/_api/" not in u for u in h.calls))
        ae._robots = None


class EmptyTabTest(unittest.TestCase):
    def test_empty_size_tab(self):
        s = BeautifulSoup('<dl><dt>SIZE GUIDE</dt><dd> </dd><dt>DETAIL</dt><dd>코튼 100%</dd></dl>', "lxml")
        self.assertTrue(cc.size_tab_empty(s))
        s = BeautifulSoup('<dl><dt>SIZE GUIDE</dt><dd>총장 70</dd></dl>', "lxml")
        self.assertFalse(cc.size_tab_empty(s))
        s = BeautifulSoup('<dl><dt>DETAIL</dt><dd>코튼</dd></dl>', "lxml")
        self.assertFalse(cc.size_tab_empty(s))


if __name__ == "__main__":
    unittest.main()
