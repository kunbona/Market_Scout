import unittest

from fetcher import sector_heat


class QmtMonitorHelperTests(unittest.TestCase):
    def test_build_qmt_limit_down_monitor_shapes_rows_and_summary(self) -> None:
        rows = [
            {
                "stock_code": "000016.SZ",
                "stock_name": "*ST康佳A",
                "last_price": 4.15,
                "last_close": 4.37,
                "down_limit": 4.15,
                "sector": "家用电器",
            },
            {
                "stock_code": "603022.SH",
                "stock_name": "新通联",
                "last_price": 11.06,
                "last_close": 12.29,
                "down_limit": 11.06,
                "sector": "包装印刷",
            },
        ]

        payload = sector_heat.build_qmt_limit_down_monitor_payload(rows)

        self.assertEqual(payload["summary"]["total_count"], 2)
        self.assertEqual(payload["summary"]["st_count"], 1)
        self.assertEqual(payload["summary"]["non_st_count"], 1)
        self.assertEqual(payload["summary"]["industry_count"], 2)
        self.assertEqual(payload["industry_distribution"][0]["count"], 1)
        self.assertEqual(payload["items"][0]["is_st"], True)
        self.assertEqual(payload["items"][0]["tags"], ["ST", "跌停"])
        self.assertAlmostEqual(payload["items"][0]["limit_gap_pct"], 0.0, places=6)

    def test_build_qmt_industry_draggers_aggregates_from_same_rows(self) -> None:
        rows = [
            {
                "stock_code": "000016.SZ",
                "stock_name": "*ST康佳A",
                "last_price": 4.15,
                "last_close": 4.37,
                "down_limit": 4.15,
                "sector": "家用电器",
            },
            {
                "stock_code": "000017.SZ",
                "stock_name": "深中华A",
                "last_price": 2.50,
                "last_close": 2.78,
                "down_limit": 2.50,
                "sector": "家用电器",
            },
            {
                "stock_code": "603022.SH",
                "stock_name": "新通联",
                "last_price": 11.06,
                "last_close": 12.29,
                "down_limit": 11.06,
                "sector": "包装印刷",
            },
        ]

        payload = sector_heat.build_qmt_industry_draggers_payload(rows)

        self.assertEqual(payload["summary"]["sector_count"], 2)
        self.assertEqual(payload["summary"]["top_dragger_sector"], "家用电器")
        self.assertEqual(payload["summary"]["top_dragger_limit_down_count"], 2)
        self.assertEqual(payload["items"][0]["sector"], "家用电器")
        self.assertEqual(payload["items"][0]["limit_down_count"], 2)
        self.assertEqual(payload["items"][0]["st_count"], 1)
        self.assertEqual(payload["items"][0]["non_st_count"], 1)
        self.assertEqual(payload["items"][0]["lead_stock_code"], "000016.SZ")
        self.assertAlmostEqual(payload["items"][0]["drag_score"], 2.2, places=6)

    def test_build_qmt_monitor_payload_handles_empty_rows(self) -> None:
        monitor = sector_heat.build_qmt_limit_down_monitor_payload([])
        draggers = sector_heat.build_qmt_industry_draggers_payload([])

        self.assertEqual(monitor["summary"]["total_count"], 0)
        self.assertEqual(monitor["industry_distribution"], [])
        self.assertEqual(monitor["items"], [])
        self.assertEqual(draggers["summary"]["sector_count"], 0)
        self.assertIsNone(draggers["summary"]["top_dragger_sector"])
        self.assertEqual(draggers["items"], [])


if __name__ == "__main__":
    unittest.main()
