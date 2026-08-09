import unittest
from unittest import mock

import server


class QmtOverviewApiTests(unittest.TestCase):
    def test_current_qmt_trade_date_uses_qmt_trading_calendar(self) -> None:
        with mock.patch.object(
            server,
            "_today",
            return_value="2026-06-27",
        ), mock.patch(
            "fetcher.qmt_data_api.get_current_qmt_trade_date",
            return_value="2026-06-26",
        ):
            self.assertEqual(server._current_qmt_trade_date(), "2026-06-26")

    def test_get_qmt_overview_focus_list_returns_stale_rows_and_schedules_refresh(self) -> None:
        stale_rows = [
            {
                "trade_date": "2026-06-27",
                "stock_code": "000070.SZ",
                "stock_name": "特发信息",
                "last_price": 22.29,
                "last_close": 24.77,
                "down_limit": 22.29,
                "sector": "通信",
            }
        ]

        with mock.patch.object(
            server,
            "_current_qmt_trade_date",
            return_value="2026-06-26",
        ), mock.patch.object(
            server,
            "get_dt_pool_v3",
            side_effect=lambda trade_date=None: [] if trade_date == "2026-06-26" else stale_rows,
            create=True,
        ), mock.patch.object(
            server,
            "_schedule_qmt_background_refresh",
            return_value=True,
        ) as schedule_mock:
            result = server._get_qmt_overview_focus_list(
                {"enabled": True, "connected": True, "version": "1.0.0"}
            )

        self.assertEqual(result, stale_rows)
        schedule_mock.assert_called_once()
        refresh_name, refresh_fn = schedule_mock.call_args.args
        self.assertEqual(refresh_name, "dt_pool_v3:2026-06-26")
        self.assertTrue(callable(refresh_fn))

    def test_get_qmt_overview_focus_list_prefers_target_trade_date_rows(self) -> None:
        latest_rows = [
            {
                "trade_date": "2026-06-27",
                "stock_code": "000001.SZ",
                "stock_name": "平安银行",
                "last_price": 10.0,
                "last_close": 10.5,
                "down_limit": 9.45,
                "sector": "银行",
            }
        ]
        target_rows = [
            {
                "trade_date": "2026-06-26",
                "stock_code": "000070.SZ",
                "stock_name": "特发信息",
                "last_price": 22.29,
                "last_close": 24.77,
                "down_limit": 22.29,
                "sector": "通信",
            }
        ]

        def _get_dt_pool_v3_side_effect(trade_date=None):
            if trade_date == "2026-06-26":
                return target_rows
            if trade_date is None:
                return latest_rows
            return []

        with mock.patch.object(
            server,
            "_current_qmt_trade_date",
            return_value="2026-06-26",
        ), mock.patch.object(
            server,
            "get_dt_pool_v3",
            side_effect=_get_dt_pool_v3_side_effect,
            create=True,
        ), mock.patch.object(
            server,
            "_schedule_qmt_background_refresh",
            return_value=True,
        ) as schedule_mock:
            result = server._get_qmt_overview_focus_list(
                {"enabled": True, "connected": True, "version": "1.0.0"}
            )

        self.assertEqual(result, target_rows)
        schedule_mock.assert_not_called()

    def test_get_qmt_overview_breadth_returns_stale_snapshot_and_schedules_refresh(self) -> None:
        stale_breadth = {
            "fetch_time": "2026-06-27 02:26:03",
            "source": "xtquant",
            "market": "TOTAL",
            "up_count": 758,
            "down_count": 4394,
            "flat_count": 56,
            "total_amount": 3552080881500.0,
        }

        with mock.patch.object(
            server,
            "_current_qmt_trade_date",
            return_value="2026-06-26",
        ), mock.patch.object(
            server,
            "get_market_breadth_latest",
            return_value=[stale_breadth],
            create=True,
        ), mock.patch.object(
            server,
            "_schedule_qmt_background_refresh",
            return_value=True,
        ) as schedule_mock:
            result = server._get_qmt_overview_breadth(
                {"enabled": True, "connected": True, "version": "1.0.0"}
            )

        self.assertEqual(result, stale_breadth)
        schedule_mock.assert_called_once()
        refresh_name, refresh_fn = schedule_mock.call_args.args
        self.assertEqual(refresh_name, "market_breadth")
        self.assertTrue(callable(refresh_fn))

    def test_get_qmt_overview_breadth_prefers_target_trade_date_snapshot(self) -> None:
        rows = [
            {
                "fetch_time": "2026-06-27 02:26:03",
                "source": "xtquant",
                "market": "TOTAL",
                "up_count": 758,
                "down_count": 4394,
                "flat_count": 56,
                "total_amount": 3552080881500.0,
            },
            {
                "fetch_time": "2026-06-26 20:41:00",
                "source": "xtquant",
                "market": "TOTAL",
                "up_count": 1300,
                "down_count": 3800,
                "flat_count": 80,
                "total_amount": 3552080881500.0,
            },
        ]

        with mock.patch.object(
            server,
            "_current_qmt_trade_date",
            return_value="2026-06-26",
        ), mock.patch.object(
            server,
            "get_market_breadth_latest",
            return_value=rows,
            create=True,
        ), mock.patch.object(
            server,
            "_schedule_qmt_background_refresh",
            return_value=True,
        ) as schedule_mock:
            result = server._get_qmt_overview_breadth(
                {"enabled": True, "connected": True, "version": "1.0.0"}
            )

        self.assertEqual(result["fetch_time"], "2026-06-26 20:41:00")
        self.assertEqual(result["market"], "TOTAL")
        schedule_mock.assert_not_called()

    def test_api_qmt_overview_uses_total_market_snapshot(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(
            server,
            "get_market_breadth_latest",
            return_value=[
                {
                    "fetch_time": "2026-06-25 14:59:16",
                    "source": "xtquant",
                    "market": "TOTAL",
                    "up_count": 1222,
                    "down_count": 3914,
                    "flat_count": 74,
                    "total_amount": 3561788668600.0,
                },
                {
                    "fetch_time": "2026-06-25 14:59:16",
                    "source": "xtquant",
                    "market": "SZ",
                    "up_count": 628,
                    "down_count": 2232,
                    "flat_count": 35,
                    "total_amount": None,
                },
                {
                    "fetch_time": "2026-06-25 14:59:16",
                    "source": "xtquant",
                    "market": "SH",
                    "up_count": 594,
                    "down_count": 1682,
                    "flat_count": 39,
                    "total_amount": None,
                },
            ],
            create=True,
        ), mock.patch.object(
            server,
            "get_dt_pool_v3",
            return_value=[],
            create=True,
        ), mock.patch.object(
            server,
            "fetch_dt_pool_v3",
            create=True,
        ), mock.patch.dict(server.os.environ, {"QMT_ENABLED": "true"}, clear=False), mock.patch(
            "fetcher.xtquant_breadth.connect",
            return_value=True,
        ), mock.patch(
            "fetcher.xtquant_breadth.get_version",
            return_value="1.0.0",
        ):
            response = client.get("/api/qmt-overview")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["snapshot"]["market"], "TOTAL")
        self.assertEqual(payload["data"]["kpis"]["up_count"], 1222)
        self.assertEqual(payload["data"]["kpis"]["down_count"], 3914)
        self.assertEqual(payload["data"]["kpis"]["flat_count"], 74)
        self.assertEqual(payload["data"]["kpis"]["turnover"], 3561788668600.0)

    def test_api_qmt_overview_returns_aggregated_payload(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(
            server,
            "get_market_breadth_latest",
            return_value=[
                {
                    "fetch_time": "14:35",
                    "source": "qmt",
                    "market": "A股",
                    "up_count": 1234,
                    "down_count": 3456,
                    "flat_count": 78,
                    "total_amount": 1234567890,
                }
            ],
            create=True,
        ), mock.patch.object(
            server,
            "get_dt_pool_v3",
            return_value=[
                {
                    "stock_code": "603022.SH",
                    "stock_name": "新通联",
                    "last_price": 11.06,
                    "last_close": 12.29,
                    "down_limit": 11.06,
                    "sector": "",
                }
            ],
            create=True,
        ), mock.patch.object(
            server,
            "fetch_dt_pool_v3",
            create=True,
        ), mock.patch.dict(server.os.environ, {"QMT_ENABLED": "true"}, clear=False), mock.patch(
            "fetcher.xtquant_breadth.connect",
            return_value=True,
        ), mock.patch(
            "fetcher.xtquant_breadth.get_version",
            return_value="1.0.0",
        ):
            response = client.get("/api/qmt-overview")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["status"]["enabled"], True)
        self.assertEqual(payload["data"]["status"]["connected"], True)
        self.assertEqual(payload["data"]["kpis"]["limit_down_count"], 1)
        self.assertEqual(payload["data"]["kpis"]["up_count"], 1234)
        self.assertEqual(payload["data"]["focus_list"][0]["stock_code"], "603022.SH")

    def test_api_qmt_overview_survives_disconnected_qmt(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(
            server,
            "get_market_breadth_latest",
            return_value=[],
            create=True,
        ), mock.patch.object(
            server,
            "get_dt_pool_v3",
            return_value=[],
            create=True,
        ), mock.patch.object(
            server,
            "fetch_dt_pool_v3",
            create=True,
        ), mock.patch.dict(server.os.environ, {"QMT_ENABLED": "true"}, clear=False), mock.patch(
            "fetcher.xtquant_breadth.connect",
            return_value=False,
        ), mock.patch(
            "fetcher.xtquant_breadth.get_version",
            return_value="1.0.0",
        ):
            response = client.get("/api/qmt-overview")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["status"]["connected"], False)
        self.assertEqual(payload["data"]["focus_list"], [])
        self.assertIsNone(payload["data"]["snapshot"]["updated_at"])


if __name__ == "__main__":
    unittest.main()
