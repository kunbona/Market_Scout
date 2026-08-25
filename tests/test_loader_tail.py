"""quant/loader.py 个股 CSV 读取收口后的单元测试。

覆盖: _normalize_code 交易所后缀剥离、get_trading_data_tail 尾部快读、
get_stock_name 轻量取名 (收口自 fetcher/qmt_monitors 与 agent/query)。
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import quant.loader as loader

# stock-trading-data-pro CSV 格式: 第 1 行水印注释, 第 2 行表头, 之后数据行
_HEADER_COLS = ["股票代码", "股票名称", "交易日期", "收盘价", "成交额"]


def _make_csv(dir_path: Path, filename: str, rows: list[list]) -> Path:
    path = dir_path / filename
    with open(path, "w", encoding="gbk") as f:
        f.write("数据由 xx 提供，仅供研究使用。\n")
        f.write(",".join(_HEADER_COLS) + "\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")
    return path


class NormalizeCodeTests(unittest.TestCase):
    def test_plain_and_prefixed_codes(self) -> None:
        self.assertEqual(loader._normalize_code("600000"), "sh600000")
        self.assertEqual(loader._normalize_code("sz000001"), "sz000001")

    def test_exchange_suffix_stripped(self) -> None:
        self.assertEqual(loader._normalize_code("600000.SH"), "sh600000")
        self.assertEqual(loader._normalize_code("000070.SZ"), "sz000070")
        self.assertEqual(loader._normalize_code("832566.BJ"), "bj832566")


class TradingDataTailTests(unittest.TestCase):
    def test_tail_reads_only_last_n_rows_with_renamed_columns(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [[600000, "浦发银行", f"2026-08-{d:02d}", 10 + d, 1000 + d]
                    for d in range(1, 21)]
            trading = root / "stock-trading-data-pro"
            trading.mkdir()
            _make_csv(trading, "sh600000.csv", rows)

            with mock.patch.object(loader, "DATA_ROOT", root):
                df = loader.get_trading_data_tail("600000.SH", rows=5)

            self.assertEqual(len(df), 5)
            self.assertEqual(df["code"].iloc[-1], 600000)
            self.assertEqual(df["name"].iloc[-1], "浦发银行")
            self.assertEqual(str(df["trade_date"].iloc[-1]), "2026-08-20")
            # 只保留尾部 5 天
            self.assertEqual(str(df["trade_date"].iloc[0]), "2026-08-16")

    def test_tail_missing_file_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            with mock.patch.object(loader, "DATA_ROOT", Path(tmp)):
                self.assertTrue(loader.get_trading_data_tail("sh600519").empty)


class GetStockNameTests(unittest.TestCase):
    def test_returns_latest_name(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            trading = root / "stock-trading-data-pro"
            trading.mkdir()
            _make_csv(trading, "sh600000.csv", [
                [600000, "浦发银行", "2026-08-19", 10.1, 100],
                [600000, "浦发银行", "2026-08-20", 10.2, 110],
            ])
            with mock.patch.object(loader, "DATA_ROOT", root):
                self.assertEqual(loader.get_stock_name("600000"), "浦发银行")

    def test_missing_code_returns_empty_string(self) -> None:
        with TemporaryDirectory() as tmp:
            with mock.patch.object(loader, "DATA_ROOT", Path(tmp)):
                self.assertEqual(loader.get_stock_name("600519"), "")


if __name__ == "__main__":
    unittest.main()
