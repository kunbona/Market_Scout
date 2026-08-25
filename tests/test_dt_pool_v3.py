import tempfile
import unittest
from datetime import date as real_date
from pathlib import Path
from unittest import mock

from db import storage
from fetcher import sector_heat
import server


class DtPoolV3StorageTests(unittest.TestCase):
    def test_dt_pool_v3_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "market.db"
            with mock.patch.object(storage, "DB_PATH", db_path):
                storage.init_db()
                storage.clear_dt_pool_v3("2099-01-02")
                storage.insert_dt_pool_v3(
                    "2099-01-02",
                    "603022",
                    "新通联",
                    11.06,
                    12.29,
                    11.06,
                    "",
                )

                rows = storage.get_dt_pool_v3("2099-01-02")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_code"], "603022")
        self.assertEqual(rows[0]["stock_name"], "新通联")
        self.assertEqual(rows[0]["last_price"], 11.06)
        self.assertEqual(rows[0]["last_close"], 12.29)
        self.assertEqual(rows[0]["down_limit"], 11.06)

    def test_replace_dt_pool_v3_replaces_existing_snapshot_atomically(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "market.db"
            with mock.patch.object(storage, "DB_PATH", db_path):
                storage.init_db()
                storage.insert_dt_pool_v3(
                    "2099-01-02",
                    "600000.SH",
                    "旧数据",
                    10.0,
                    11.0,
                    9.9,
                    "银行",
                )

                storage.replace_dt_pool_v3(
                    "2099-01-02",
                    [
                        {
                            "stock_code": "603022.SH",
                            "stock_name": "新通联",
                            "last_price": 11.06,
                            "last_close": 12.29,
                            "down_limit": 11.06,
                            "sector": "包装印刷",
                        },
                        {
                            "stock_code": "000070.SZ",
                            "stock_name": "特发信息",
                            "last_price": 22.29,
                            "last_close": 24.77,
                            "down_limit": 22.29,
                            "sector": "通信",
                        },
                    ],
                )

                rows = storage.get_dt_pool_v3("2099-01-02")

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["stock_code"] for row in rows}, {"603022.SH", "000070.SZ"})


class DtPoolV3LimitHelperTests(unittest.TestCase):
    def test_compute_down_limit_matches_project_rules(self) -> None:
        from fetcher.xtquant_limit_down import compute_down_limit

        self.assertEqual(compute_down_limit("600000.SH", "", 10.0, "2026-06-24"), 9.0)
        self.assertEqual(compute_down_limit("300001.SZ", "", 10.0, "2026-06-24"), 8.0)
        self.assertEqual(compute_down_limit("688001.SH", "", 10.0, "2026-06-24"), 8.0)
        self.assertEqual(compute_down_limit("430001.BJ", "", 10.0, "2026-06-24"), 7.0)
        # 2026-07-06 起 ST 与主板普通股并轨 (±10%), 简称含 ST 不再影响比例
        self.assertEqual(compute_down_limit("600000.SH", "ST测试", 10.0, "2026-06-24"), 9.0)


class DtPoolV3FetcherTests(unittest.TestCase):
    def test_fetch_dt_pool_v3_replaces_snapshot_in_single_batch(self) -> None:
        with mock.patch.object(sector_heat, "date") as mock_date, \
             mock.patch.object(sector_heat, "get_current_qmt_trade_date", return_value="2026-06-24"), \
             mock.patch.object(sector_heat, "connect", return_value=True, create=True), \
             mock.patch.object(sector_heat, "list_a_shares", return_value=["603022.SH", "600000.SH"]), \
             mock.patch.object(sector_heat, "get_full_tick_snapshot", return_value={
                 "603022.SH": {"last_price": 11.06, "last_close": 12.29},
                 "600000.SH": {"last_price": 10.01, "last_close": 10.00},
             }), \
             mock.patch.object(sector_heat, "get_security_meta", side_effect=[
                 {
                     "stock_code": "603022.SH",
                     "stock_name": "新通联",
                     "sector": "包装印刷",
                     "industry_l1": "包装印刷",
                     "industry_l2": "",
                     "industry_l3": "",
                     "is_st": False,
                 },
                 {
                     "stock_code": "600000.SH",
                     "stock_name": "浦发银行",
                     "sector": "银行",
                     "industry_l1": "银行",
                     "industry_l2": "",
                     "industry_l3": "",
                     "is_st": False,
                 },
             ]), \
             mock.patch.object(sector_heat, "replace_dt_pool_v3", create=True) as replace_mock, \
             mock.patch.object(sector_heat, "clear_dt_pool_v3", create=True) as clear_mock, \
             mock.patch.object(sector_heat, "insert_dt_pool_v3", create=True) as insert_mock:
            mock_date.today.return_value = real_date(2026, 6, 24)

            sector_heat.fetch_dt_pool_v3()

        clear_mock.assert_not_called()
        insert_mock.assert_not_called()
        replace_mock.assert_called_once()
        replace_trade_date, replace_rows = replace_mock.call_args.args
        self.assertEqual(replace_trade_date, "2026-06-24")
        self.assertEqual(len(replace_rows), 1)
        self.assertEqual(replace_rows[0]["stock_code"], "603022.SH")
        self.assertEqual(replace_rows[0]["stock_name"], "新通联")
        self.assertEqual(replace_rows[0]["down_limit"], 11.06)

    def test_fetch_dt_pool_v3_filters_current_limit_down_snapshot(self) -> None:
        with mock.patch.object(sector_heat, "date") as mock_date, \
             mock.patch.object(sector_heat, "get_current_qmt_trade_date", return_value="2026-06-24"), \
             mock.patch.object(sector_heat, "clear_dt_pool_v3", create=True) as clear_mock, \
             mock.patch.object(sector_heat, "insert_dt_pool_v3", create=True) as insert_mock, \
             mock.patch.object(sector_heat, "replace_dt_pool_v3", create=True) as replace_mock, \
             mock.patch.object(sector_heat, "connect", return_value=True, create=True), \
             mock.patch.object(sector_heat, "list_a_shares", return_value=["603022.SH", "600000.SH"]), \
             mock.patch.object(sector_heat, "get_full_tick_snapshot", return_value={
                 "603022.SH": {"last_price": 11.06, "last_close": 12.29},
                 "600000.SH": {"last_price": 10.01, "last_close": 10.00},
             }), \
             mock.patch.object(sector_heat, "get_security_meta", side_effect=[
                 {
                     "stock_code": "603022.SH",
                     "stock_name": "新通联",
                     "sector": "包装印刷",
                     "industry_l1": "包装印刷",
                     "industry_l2": "",
                     "industry_l3": "",
                     "is_st": False,
                 },
                 {
                     "stock_code": "600000.SH",
                     "stock_name": "浦发银行",
                     "sector": "银行",
                     "industry_l1": "银行",
                     "industry_l2": "",
                     "industry_l3": "",
                     "is_st": False,
                 },
             ]):
            mock_date.today.return_value = real_date(2026, 6, 24)

            sector_heat.fetch_dt_pool_v3()

        clear_mock.assert_not_called()
        insert_mock.assert_not_called()
        replace_mock.assert_called_once()
        replace_trade_date, replace_rows = replace_mock.call_args.args
        self.assertEqual(replace_trade_date, "2026-06-24")
        self.assertEqual(len(replace_rows), 1)
        self.assertEqual(replace_rows[0]["stock_code"], "603022.SH")
        self.assertEqual(replace_rows[0]["stock_name"], "新通联")
        self.assertEqual(replace_rows[0]["down_limit"], 11.06)
        self.assertEqual(replace_rows[0]["sector"], "包装印刷")


class DtPoolV3ApiTests(unittest.TestCase):
    def test_api_dt_pool_v3_returns_rows(self) -> None:
        client = server.app.test_client()
        # 路由已拆到 api/market_data.py, 函数体内从 db.storage 懒导入 →
        # mock db.storage.get_dt_pool_v3 (调用时才解析, patch 生效)
        with mock.patch.object(
            storage,
            "get_dt_pool_v3",
            return_value=[{"stock_code": "603022.SH", "last_price": 11.06}],
        ):
            response = client.get("/api/dt-pool-v3?date=2099-01-02")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"][0]["stock_code"], "603022.SH")


if __name__ == "__main__":
    unittest.main()
