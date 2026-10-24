"""누락 매장 회귀 검사. 네트워크와 data/에는 쓰지 않는다."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from bs4 import BeautifulSoup

import crawl_cafe24 as cc
import crawl_shopify as cs
import platforms


def response(payload, currency=None, status=200):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(payload).encode()
    if currency:
        r.cookies.set("cart_currency", currency)
    return r


def product(no=1, price="655.50"):
    return {"id": no, "handle": f"item-{no}", "title": "JACKET", "variants": [
        {"id": 10, "title": "S", "price": price, "compare_at_price": "800.00", "available": False},
        {"id": 11, "title": "M", "price": price, "available": True}]}


class StoreCollectionTest(unittest.TestCase):
    def test_shopify_routes_use_the_correct_crawler(self):
        for slug in ("fffpostalservice", "kusikohc", "yuul-yie"):
            with self.subTest(slug=slug), patch.object(cs, "crawl_one", return_value={}) as crawl:
                platforms.crawl_other("http", slug)
                crawl.assert_called_once_with("http", slug, print)
                self.assertIn(slug, platforms.NOT_CAFE24)

    def test_long_stu_season_is_discovered(self):
        title = "26 Autumn 'The Curious Case of Benjamin Button'"
        text = f'<a href="/product/list.html?cate_no=155">{title}</a>'
        shop = cc.Shop("stu", "https://stuoffice.com", "MEN")
        cc.load_categories(Mock(), shop, BeautifulSoup(text, "lxml"), text)
        self.assertEqual(shop.categories[155], title)

    def test_long_product_title_does_not_become_category(self):
        text = '<a href="/product/jacket/1/?cate_no=1">' + "JACKET " * 20 + '</a>'
        shop = cc.Shop("stu", "https://stuoffice.com", "MEN", categories={31: "Tops"})
        cc.load_categories(Mock(), shop, BeautifulSoup(text, "lxml"), text)
        self.assertNotIn(1, shop.categories)

    def test_known_starts_survive_empty_or_arrow_menus(self):
        for slug, expected in (("ahhorn", {66, 67, 68}), ("thecolorcolour", {58, 53})):
            text = '<a href="/product/list.html?cate_no=58">&gt;</a>' if slug == "thecolorcolour" else ""
            shop = cc.Shop(slug, "https://example.com", "")
            http = Mock()
            cc.load_categories(http, shop, BeautifulSoup(text, "lxml"), text)
            self.assertTrue(expected <= shop.categories.keys())
            http.get.assert_not_called()
            if slug == "thecolorcolour":
                self.assertEqual(shop.categories[58], "ALL")

    def test_lists_deduplicate_and_stop_on_repeated_page(self):
        shop = cc.Shop("ahhorn", "https://ahhorn.com", "", categories={66: "WOMAN"})
        http = Mock()
        http.get.return_value = Mock(status_code=200, text='<a href="/product/shirt/123/">shirt</a>')
        cc._crawl_one_list(http, shop, 66, "/product/list.html", max_pages=5)
        self.assertEqual(http.get.call_count, 2)
        self.assertEqual(set(shop.product_urls), {123})
        self.assertEqual(shop.membership, {123: {66}})

    def test_currency_mismatch_and_missing_products_are_failures(self):
        for r in (response({"products": []}, "USD"), response({}), response([], "KRW"),
                  response({"products": []}, status=403)):
            with self.subTest(response=r):
                http = Mock()
                http.get.return_value = r
                self.assertIsNone(cs.fetch_page(http, "https://example.com", 1, log=lambda _: None))

    def test_krw_price_guard_is_kept(self):
        with self.assertRaises(ValueError):
            cs.to_row(product(), "kusikohc", "https://kusikohc.com", "2026-10-22")
        row = cs.to_row(product(price="317000"), "kusikohc", "https://kusikohc.com", "2026-10-22")
        self.assertEqual(row["price"], 317000)

    def test_usd_keeps_cents_and_variant_availability_without_krw_price(self):
        row = cs.to_row(product(), "fffpostalservice", "https://fffpostalservice.com", "2026-10-22", "USD")
        self.assertEqual(row["price"], 0)
        self.assertNotIn("price_listed", row)
        self.assertEqual(row["source_prices"][0]["price"], "655.50")
        self.assertEqual(row["source_currency"], "USD")
        self.assertFalse(cc.is_soldout(row))
        self.assertEqual(row["soldout_options"], ["S"])

    def test_crawl_one_paginates_and_writes_only_to_trial_directory(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cs, "CRAWL_DIR", Path(folder)):
            http = Mock(delay=1.1)
            http.get.side_effect = [response({"products": [product()]}, "USD"),
                                    response({"products": [product(2)]}, "USD"),
                                    response({"products": []}, "USD")]
            report = cs.crawl_one(http, "fffpostalservice", log=lambda _: None)
            self.assertEqual(report["listed"], 2)
            self.assertFalse(report["guard"])
            rows = [json.loads(s) for s in (Path(folder) / "fffpostalservice.jsonl").read_text().splitlines()]
            self.assertEqual([r["product_no"] for r in rows], [1, 2])
            self.assertTrue(all(r["source_currency"] == "USD" and r["price"] == 0 for r in rows))
            self.assertIn("page=2&currency=USD", http.get.call_args_list[1].args[0])

    def test_failed_later_page_does_not_replace_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cs, "CRAWL_DIR", Path(folder)):
            path = Path(folder) / "fffpostalservice.jsonl"
            original = json.dumps({"product_no": 7, "soldout": False}) + "\n"
            path.write_text(original)
            http = Mock(delay=1.1)
            http.get.side_effect = [response({"products": [product()]}, "USD"), response({}, status=403)]
            report = cs.crawl_one(http, "fffpostalservice", log=lambda _: None)
            self.assertTrue(report["guard"])
            self.assertEqual(path.read_text(), original)


if __name__ == "__main__":
    unittest.main()
