import unittest
from unittest import mock

import server


class QmtMonitorApiTests(unittest.TestCase):
    def test_api_qmt_limit_down_monitor_returns_stable_payload(self) -> None:
        client = server.app.test_client()
        rows = [
            {
                "stock_code": "000016.SZ",
                "stock_name": "*ST康佳A",
                "last_price": 4.15,
                "last_close": 4.37,
                "down_limit": 4.15,
                "sector": "家用电器",
            }
        ]
        with mock.patch.object(server, "_read_qmt_runtime_status", return_value={
            "enabled": True,
            "connected": True,
            "version": "1.0.0",
        }), mock.patch.object(server, "_latest_total_market_breadth_row", return_value={
            "fetch_time": "2026-06-24 14:35:00",
            "source": "QMT",
            "market": "TOTAL",
        }), mock.patch.object(server, "_get_qmt_limit_down_rows", return_value=rows):
            response = client.get("/api/qmt-limit-down-monitor")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["summary"]["total_count"], 1)
        self.assertEqual(payload["summary"]["st_count"], 1)
        self.assertEqual(payload["items"][0]["tags"], ["ST", "跌停"])

    def test_api_qmt_industry_draggers_returns_aggregated_payload(self) -> None:
        client = server.app.test_client()
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
        ]
        with mock.patch.object(server, "_read_qmt_runtime_status", return_value={
            "enabled": True,
            "connected": True,
            "version": "1.0.0",
        }), mock.patch.object(server, "_latest_total_market_breadth_row", return_value={
            "fetch_time": "2026-06-24 14:35:00",
            "source": "QMT",
            "market": "TOTAL",
        }), mock.patch.object(server, "_get_qmt_limit_down_rows", return_value=rows):
            response = client.get("/api/qmt-industry-draggers")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["summary"]["sector_count"], 1)
        self.assertEqual(payload["summary"]["top_dragger_sector"], "家用电器")
        self.assertEqual(payload["items"][0]["drag_score"], 2.2)

    def test_api_qmt_monitor_endpoints_survive_empty_rows(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(server, "_read_qmt_runtime_status", return_value={
            "enabled": True,
            "connected": False,
            "version": "1.0.0",
        }), mock.patch.object(server, "_latest_total_market_breadth_row", return_value=None), mock.patch.object(
            server, "_get_qmt_limit_down_rows", return_value=[]
        ):
            monitor_response = client.get("/api/qmt-limit-down-monitor")
            dragger_response = client.get("/api/qmt-industry-draggers")

        self.assertEqual(monitor_response.status_code, 200)
        self.assertEqual(dragger_response.status_code, 200)
        self.assertEqual(monitor_response.get_json()["data"]["items"], [])
        self.assertEqual(dragger_response.get_json()["data"]["items"], [])

    def test_api_qmt_industry_stats_returns_aggregated_payload(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(server, "_read_qmt_runtime_status", return_value={
            "enabled": True,
            "connected": True,
            "version": "xtquant",
        }), mock.patch.object(server, "_get_qmt_overview_breadth", return_value={
            "fetch_time": "2026-06-27 14:35:00",
            "source": "xtquant",
            "market": "TOTAL",
        }), mock.patch.object(server, "_get_qmt_industry_stats_payload", return_value={
            "summary": {
                "sector_count": 1,
                "strong_ma10_sector_count": 1,
                "top_meat_sector": "通信",
                "top_main_inflow_sector": "通信",
            },
            "items": [
                {
                    "sector": "通信",
                    "stock_count": 1,
                    "above_ma10_count": 1,
                    "above_ma10_ratio": 1.0,
                    "meat_count": 0,
                    "limit_up_count": 0,
                    "limit_down_count": 1,
                    "yesterday_main_inflow": 120000000.0,
                }
            ],
        }, create=True):
            response = client.get("/api/qmt-industry-stats")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["summary"]["sector_count"], 1)
        self.assertEqual(payload["summary"]["top_main_inflow_sector"], "通信")
        self.assertEqual(payload["items"][0]["yesterday_main_inflow"], 120000000.0)

    def test_api_qmt_industry_stats_returns_empty_payload_and_schedules_refresh_when_cache_missing(self) -> None:
        client = server.app.test_client()
        empty_payload = {
            "summary": {
                "sector_count": 0,
                "strong_ma10_sector_count": 0,
                "top_meat_sector": None,
                "top_main_inflow_sector": None,
            },
            "items": [],
        }
        with mock.patch.object(server, "_read_qmt_runtime_status", return_value={
            "enabled": True,
            "connected": True,
            "version": "xtquant",
        }), mock.patch.object(server, "_get_qmt_overview_breadth", return_value={
            "fetch_time": "2026-06-27 14:35:00",
            "source": "xtquant",
            "market": "TOTAL",
        }), mock.patch.object(
            server,
            "_get_qmt_industry_stats_payload",
            return_value=empty_payload,
            create=True,
        ) as payload_mock:
            response = client.get("/api/qmt-industry-stats")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["summary"]["sector_count"], 0)
        self.assertEqual(payload["items"], [])
        payload_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
