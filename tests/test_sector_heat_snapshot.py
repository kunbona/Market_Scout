import unittest
from unittest import mock

import pandas as pd

from fetcher import sector_heat


class SectorHeatSnapshotTests(unittest.TestCase):
    def test_fetch_zt_pool_replaces_today_snapshot(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "连板数": 2,
                    "首次封板时间": "093000",
                    "最后封板时间": "145600",
                    "所属行业": "银行",
                    "封板资金": 123456789,
                    "炸板次数": 1,
                    "换手率": 3.2,
                    "流通市值": 987654321,
                }
            ]
        )
        events: list[tuple[str, str]] = []

        with mock.patch.object(sector_heat.ak, "stock_zt_pool_em", return_value=df), \
             mock.patch.object(sector_heat, "clear_zt_pool", side_effect=lambda d: events.append(("clear", d))), \
             mock.patch.object(sector_heat, "insert_zt_pool", side_effect=lambda *args, **kwargs: events.append(("insert", args[0]))):
            sector_heat.fetch_zt_pool()

        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0][0], "clear")
        self.assertEqual(events[1][0], "insert")

    def test_fetch_dt_pool_clears_today_snapshot_when_source_empty(self) -> None:
        df = pd.DataFrame()
        events: list[tuple[str, str]] = []

        with mock.patch.object(sector_heat.ak, "stock_zt_pool_dtgc_em", return_value=df), \
             mock.patch.object(sector_heat, "clear_dt_pool", side_effect=lambda d: events.append(("clear", d))), \
             mock.patch.object(sector_heat, "insert_dt_pool", side_effect=lambda *args, **kwargs: events.append(("insert", args[0]))):
            sector_heat.fetch_dt_pool()

        self.assertEqual(events, [("clear", events[0][1])])

    def test_fetch_zbgc_pool_replaces_today_snapshot(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "首次封板时间": "101000",
                    "炸板次数": 2,
                    "振幅": 8.1,
                    "所属行业": "银行",
                }
            ]
        )
        events: list[tuple[str, str]] = []

        with mock.patch.object(sector_heat.ak, "stock_zt_pool_zbgc_em", return_value=df), \
             mock.patch.object(sector_heat, "clear_zbgc_pool", side_effect=lambda d: events.append(("clear", d))), \
             mock.patch.object(sector_heat, "insert_zbgc_pool", side_effect=lambda *args, **kwargs: events.append(("insert", args[0]))):
            sector_heat.fetch_zbgc_pool()

        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0][0], "clear")
        self.assertEqual(events[1][0], "insert")

    def test_fetch_strong_pool_replaces_today_snapshot(self) -> None:
        df = pd.DataFrame(
            [
                {
                    "代码": "000001",
                    "名称": "平安银行",
                    "涨跌幅": 9.99,
                    "新高": "是",
                    "量比": 2.5,
                    "入选理由": "60日新高",
                    "所属行业": "银行",
                }
            ]
        )
        events: list[tuple[str, str]] = []

        with mock.patch.object(sector_heat.ak, "stock_zt_pool_strong_em", return_value=df), \
             mock.patch.object(sector_heat, "clear_strong_pool", side_effect=lambda d: events.append(("clear", d))), \
             mock.patch.object(sector_heat, "insert_strong_pool", side_effect=lambda *args, **kwargs: events.append(("insert", args[0]))):
            sector_heat.fetch_strong_pool()

        self.assertGreaterEqual(len(events), 2)
        self.assertEqual(events[0][0], "clear")
        self.assertEqual(events[1][0], "insert")


if __name__ == "__main__":
    unittest.main()
