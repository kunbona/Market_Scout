"""类别映射单一来源: /api/industry-trend/categories 冒烟测试。

前端不再硬编码 31 行业 → 6 类别映射, 统一消费该端点;
后端 quant/industry_trend_daily.py 是唯一来源。
"""
import unittest

import server


class IndustryCategoriesApiTests(unittest.TestCase):
    def test_categories_endpoint_returns_backend_mapping(self) -> None:
        client = server.app.test_client()
        resp = client.get("/api/industry-trend/categories")
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["success"])
        data = payload["data"]
        self.assertEqual(len(data["order"]), 6)
        self.assertEqual(len(data["map"]), 31)

    def test_categories_endpoint_matches_module_constants(self) -> None:
        from quant.industry_trend_daily import CATEGORY_ORDER, INDUSTRY_CATEGORY
        client = server.app.test_client()
        payload = client.get("/api/industry-trend/categories").get_json()
        self.assertEqual(payload["data"]["map"], INDUSTRY_CATEGORY)
        self.assertEqual(payload["data"]["order"], CATEGORY_ORDER)


if __name__ == "__main__":
    unittest.main()
