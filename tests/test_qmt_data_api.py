from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest import mock

from fetcher import qmt_data_api


def _to_millis(date_text: str) -> int:
    return int(datetime.strptime(date_text, "%Y-%m-%d").timestamp() * 1000)


class QmtDataApiTests(unittest.TestCase):
    def test_get_current_qmt_trade_date_falls_back_to_latest_qmt_trading_day(self) -> None:
        fake_xtdata = SimpleNamespace(
            get_trading_dates=lambda market, start_time="", end_time="": [
                _to_millis("2026-06-26")
            ]
        )

        with mock.patch.object(qmt_data_api, "xtdata", fake_xtdata), mock.patch.object(
            qmt_data_api,
            "qmt_connect",
            return_value=True,
        ):
            self.assertEqual(
                qmt_data_api.get_current_qmt_trade_date("2026-06-27"),
                "2026-06-26",
            )

    def test_get_recent_qmt_trading_dates_merges_and_normalizes_multiple_markets(self) -> None:
        def _get_trading_dates(market: str, start_time: str = "", end_time: str = "") -> list[object]:
            if market == "SH":
                return [_to_millis("2026-06-25"), "20260626"]
            if market == "SZ":
                return ["2026-06-26", datetime(2026, 6, 27)]
            return []

        fake_xtdata = SimpleNamespace(get_trading_dates=_get_trading_dates)

        with mock.patch.object(qmt_data_api, "xtdata", fake_xtdata), mock.patch.object(
            qmt_data_api,
            "qmt_connect",
            return_value=True,
        ):
            self.assertEqual(
                qmt_data_api.get_recent_qmt_trading_dates("2026-06-27"),
                ["2026-06-25", "2026-06-26", "2026-06-27"],
            )


if __name__ == "__main__":
    unittest.main()
