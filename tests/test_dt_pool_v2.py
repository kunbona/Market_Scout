import tempfile
import unittest
from datetime import date as real_date
from pathlib import Path
from unittest import mock

from db import storage
from fetcher import sector_heat
import server


class DtPoolV2StorageTests(unittest.TestCase):
    def test_dt_pool_v2_round_trip_latest_date(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "market.db"
            with mock.patch.object(storage, "DB_PATH", db_path):
                storage.init_db()
                storage.clear_dt_pool_v2("2099-01-02")
                storage.insert_dt_pool_v2("2099-01-02", "603022", "新通联", "143625", "包装材料")

                rows = storage.get_dt_pool_v2("2099-01-02")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stock_code"], "603022")
        self.assertEqual(rows[0]["stock_name"], "新通联")
        self.assertEqual(rows[0]["first_dt_time"], "143625")
        self.assertEqual(rows[0]["sector"], "包装材料")


class DtPoolV2FetcherTests(unittest.TestCase):
    def test_fetch_dt_pool_v2_replaces_today_snapshot_from_eastmoney(self) -> None:
        payload = {
            "data": {
                "pool": [
                    {
                        "c": "603022",
                        "n": "新通联",
                        "lbt": 143625,
                        "hybk": "包装材料",
                    },
                    {
                        "c": "300849",
                        "n": "锦盛新材",
                        "lbt": 112957,
                        "hybk": "美容护理",
                    },
                ]
            }
        }
        events: list[tuple[str, str, str]] = []
        mocked_requests = mock.Mock()
        mocked_requests.get.return_value.raise_for_status.return_value = None
        mocked_requests.get.return_value.json.return_value = payload

        with mock.patch.object(sector_heat, "date") as mock_date, \
             mock.patch.object(sector_heat, "clear_dt_pool_v2", side_effect=lambda d: events.append(("clear", d, "")), create=True), \
             mock.patch.object(sector_heat, "insert_dt_pool_v2", side_effect=lambda *args: events.append(("insert", args[1], args[2])), create=True), \
             mock.patch.object(sector_heat, "requests", mocked_requests, create=True):
            mock_date.today.return_value = real_date(2026, 6, 24)

            sector_heat.fetch_dt_pool_v2()

        self.assertEqual(events[0], ("clear", "2026-06-24", ""))
        self.assertEqual(events[1], ("insert", "603022", "新通联"))
        self.assertEqual(events[2], ("insert", "300849", "锦盛新材"))


class DtPoolV2ApiTests(unittest.TestCase):
    def test_api_dt_pool_v2_returns_rows(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(
            server,
            "get_dt_pool_v2",
            return_value=[{"stock_code": "603022", "stock_name": "新通联"}],
            create=True,
        ):
            response = client.get("/api/dt-pool-v2?date=2099-01-02")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"][0]["stock_code"], "603022")


if __name__ == "__main__":
    unittest.main()
