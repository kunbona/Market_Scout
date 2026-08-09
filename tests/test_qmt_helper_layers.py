import unittest
from unittest import mock

import pandas as pd


class _FakeDataFrame:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    @property
    def empty(self) -> bool:
        return not self._rows

    @property
    def iloc(self):
        rows = self._rows

        class _ILoc:
            def __getitem__(self, index: int) -> dict:
                return rows[index]

        return _ILoc()


class QmtDataApiTests(unittest.TestCase):
    def test_list_a_shares_returns_clean_codes(self) -> None:
        from fetcher import qmt_data_api

        with mock.patch.object(qmt_data_api, "qmt_connect", return_value=True), mock.patch.object(
            qmt_data_api,
            "xtdata",
            mock.Mock(get_stock_list_in_sector=mock.Mock(return_value=["000001.SZ", " 600000.SH ", ""])),
        ):
            result = qmt_data_api.list_a_shares()

        self.assertEqual(result, ["000001.SZ", "600000.SH"])

    def test_get_full_tick_snapshot_normalizes_tick_fields(self) -> None:
        from fetcher import qmt_data_api

        fake_xtdata = mock.Mock()
        fake_xtdata.get_full_tick.return_value = {
            "000001.SZ": {
                "lastPrice": 12.34,
                "lastClose": 12.8,
                "open": 12.7,
                "high": 12.9,
                "low": 12.2,
                "volume": 123456,
                "amount": 987654321.0,
                "bidPrice": 12.33,
                "askPrice": 12.34,
            }
        }

        with mock.patch.object(qmt_data_api, "qmt_connect", return_value=True), mock.patch.object(
            qmt_data_api,
            "xtdata",
            fake_xtdata,
        ):
            result = qmt_data_api.get_full_tick_snapshot(["000001.SZ"])

        self.assertEqual(result["000001.SZ"]["stock_code"], "000001.SZ")
        self.assertEqual(result["000001.SZ"]["last_price"], 12.34)
        self.assertEqual(result["000001.SZ"]["last_close"], 12.8)
        self.assertEqual(result["000001.SZ"]["bid_price"], 12.33)
        self.assertEqual(result["000001.SZ"]["ask_price"], 12.34)


class SecurityMetaTests(unittest.TestCase):
    def test_get_security_meta_prefers_local_data_and_marks_st(self) -> None:
        from quant import security_meta

        with mock.patch(
            "quant.loader.get_trading_data",
            return_value=_FakeDataFrame(
                [
                    {
                        "name": "*ST康佳A",
                        "industry_l1": "家用电器",
                        "industry_l2": "白色家电",
                        "industry_l3": "彩电",
                    }
                ]
            ),
        ), mock.patch.object(
            security_meta,
            "get_security_detail_fallback",
            return_value={"stock_name": "康佳A"},
        ):
            result = security_meta.get_security_meta("000016.SZ")

        self.assertEqual(result["stock_name"], "*ST康佳A")
        self.assertEqual(result["sector"], "家用电器")
        self.assertEqual(result["industry_l2"], "白色家电")
        self.assertTrue(result["is_st"])

    def test_get_security_meta_falls_back_to_qmt_detail(self) -> None:
        from quant import security_meta

        with mock.patch("quant.loader.get_trading_data", return_value=_FakeDataFrame([])), mock.patch.object(
            security_meta,
            "get_security_detail_fallback",
            return_value={"stock_name": "平安银行"},
        ):
            result = security_meta.get_security_meta("000001.SZ")

        self.assertEqual(result["stock_name"], "平安银行")
        self.assertEqual(result["sector"], "")
        self.assertFalse(result["is_st"])


class QmtMonitorPayloadTests(unittest.TestCase):
    def test_build_qmt_limit_down_monitor_payload_returns_summary(self) -> None:
        from fetcher.qmt_monitors import build_qmt_limit_down_monitor_payload

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

        payload = build_qmt_limit_down_monitor_payload(rows)

        self.assertEqual(payload["summary"]["total_count"], 2)
        self.assertEqual(payload["summary"]["st_count"], 1)
        self.assertEqual(payload["summary"]["industry_count"], 1)
        self.assertEqual(payload["industry_distribution"][0], {"sector": "家用电器", "count": 2})
        self.assertEqual(payload["items"][0]["tags"][1], "跌停")

    def test_build_qmt_industry_draggers_payload_aggregates_and_sorts(self) -> None:
        from fetcher.qmt_monitors import build_qmt_industry_draggers_payload

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
                "stock_code": "000004.SZ",
                "stock_name": "国华退",
                "last_price": 1.20,
                "last_close": 1.33,
                "down_limit": 1.20,
                "sector": "计算机",
            },
        ]

        payload = build_qmt_industry_draggers_payload(rows)

        self.assertEqual(payload["summary"]["sector_count"], 2)
        self.assertEqual(payload["summary"]["top_dragger_sector"], "家用电器")
        self.assertEqual(payload["summary"]["top_dragger_limit_down_count"], 2)
        self.assertEqual(payload["items"][0]["drag_score"], 2.2)


class QmtIndustryStatsHelperTests(unittest.TestCase):
    def test_build_qmt_industry_stats_payload_aggregates_expected_metrics(self) -> None:
        from fetcher.qmt_monitors import build_qmt_industry_stats_payload

        rows = [
            {
                "stock_code": "000070.SZ",
                "stock_name": "特发信息",
                "sector": "通信",
                "last_price": 22.29,
                "last_close": 24.77,
                "ma10": 21.50,
                "up_limit": 27.25,
                "down_limit": 22.29,
                "yesterday_main_inflow": 120000000.0,
            },
            {
                "stock_code": "600498.SH",
                "stock_name": "烽火通信",
                "sector": "通信",
                "last_price": 15.80,
                "last_close": 14.90,
                "ma10": 15.20,
                "up_limit": 16.39,
                "down_limit": 13.41,
                "yesterday_main_inflow": -20000000.0,
            },
            {
                "stock_code": "000967.SZ",
                "stock_name": "盈峰环境",
                "sector": "环保",
                "last_price": 7.10,
                "last_close": 7.25,
                "ma10": 7.30,
                "up_limit": 7.98,
                "down_limit": 6.53,
                "yesterday_main_inflow": 5000000.0,
            },
        ]

        payload = build_qmt_industry_stats_payload(rows)

        self.assertEqual(payload["summary"]["sector_count"], 2)
        self.assertEqual(payload["summary"]["strong_ma10_sector_count"], 1)
        self.assertEqual(payload["summary"]["top_meat_sector"], "通信")
        self.assertEqual(payload["summary"]["top_main_inflow_sector"], "通信")
        self.assertEqual(payload["items"][0]["sector"], "通信")
        self.assertEqual(payload["items"][0]["stock_count"], 2)
        self.assertEqual(payload["items"][0]["above_ma10_count"], 2)
        self.assertAlmostEqual(payload["items"][0]["above_ma10_ratio"], 1.0, places=6)
        self.assertEqual(payload["items"][0]["meat_count"], 1)
        self.assertEqual(payload["items"][0]["limit_up_count"], 0)
        self.assertEqual(payload["items"][0]["limit_down_count"], 1)
        self.assertEqual(payload["items"][0]["yesterday_main_inflow"], 100000000.0)

    def test_build_qmt_industry_stats_payload_skips_blank_sector_rows(self) -> None:
        from fetcher.qmt_monitors import build_qmt_industry_stats_payload

        rows = [
            {
                "stock_code": "000001.SZ",
                "stock_name": "平安银行",
                "sector": "",
                "last_price": 10.0,
                "last_close": 9.8,
                "ma10": 9.5,
                "up_limit": 10.78,
                "down_limit": 8.82,
                "yesterday_main_inflow": 3000000.0,
            }
        ]

        payload = build_qmt_industry_stats_payload(rows)

        self.assertEqual(payload["summary"]["sector_count"], 0)
        self.assertEqual(payload["summary"]["top_meat_sector"], None)
        self.assertEqual(payload["summary"]["top_main_inflow_sector"], None)
        self.assertEqual(payload["items"], [])


class QmtIndustryStatsSourceTests(unittest.TestCase):
    def test_build_qmt_industry_stats_source_rows_enriches_qmt_rows_for_target_trade_date(self) -> None:
        from fetcher.qmt_monitors import build_qmt_industry_stats_source_rows

        sample_df = pd.DataFrame(
            [
                {
                    "trade_date": "2026-06-24",
                    "name": "特发信息",
                    "industry_l1": "通信",
                    "inst_buy": 20000000.0,
                    "inst_sell": 5000000.0,
                    "close": 25.10,
                },
                {
                    "trade_date": "2026-06-25",
                    "name": "特发信息",
                    "industry_l1": "通信",
                    "inst_buy": 80000000.0,
                    "inst_sell": 20000000.0,
                    "close": 24.77,
                },
                {
                    "trade_date": "2026-06-26",
                    "name": "特发信息",
                    "industry_l1": "通信",
                    "inst_buy": 0.0,
                    "inst_sell": 0.0,
                    "close": 22.29,
                },
                {
                    "trade_date": "2026-06-27",
                    "name": "特发信息",
                    "industry_l1": "通信",
                    "inst_buy": 999999999.0,
                    "inst_sell": 111111111.0,
                    "close": 30.00,
                },
            ]
        )

        with mock.patch("fetcher.qmt_monitors.list_a_shares", return_value=["000070.SZ"]), mock.patch(
            "fetcher.qmt_monitors.get_full_tick_snapshot",
            return_value={"000070.SZ": {"last_price": 22.29, "last_close": 24.77}},
        ), mock.patch("fetcher.qmt_monitors.get_trading_data", return_value=sample_df), mock.patch(
            "fetcher.qmt_monitors.compute_down_limit",
            return_value=22.29,
        ), mock.patch("fetcher.qmt_monitors.compute_up_limit", return_value=27.25):
            rows = build_qmt_industry_stats_source_rows("2026-06-26")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sector"], "通信")
        self.assertAlmostEqual(rows[0]["ma10"], (25.10 + 24.77 + 22.29) / 3, places=6)
        self.assertEqual(rows[0]["yesterday_main_inflow"], 60000000.0)
        self.assertEqual(rows[0]["up_limit"], 27.25)
        self.assertEqual(rows[0]["down_limit"], 22.29)


if __name__ == "__main__":
    unittest.main()
