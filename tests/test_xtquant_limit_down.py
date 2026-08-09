"""
A 股涨跌停价格计算测试。

覆盖：
- 各板块（主板 / 创业板 / 科创板 / 北交所）普通股与 ST 的 ratio
- 2026-07-06 新规：沪深主板 ST 涨跌幅从 ±5% 调整为 ±10%（与普通股并轨）
- 创业板/科创板/北交所 ST 不受新规影响，ratio 与普通股相同
- 北交所保留 4 位小数（其他板块 2 位）
- ST 识别：ST / *ST / S*ST 前缀
"""
from __future__ import annotations

import unittest

from fetcher.xtquant_limit_down import (
    _is_st,
    _is_main_board,
    _is_chinext,
    _is_star,
    _is_bj,
    _ratio,
    compute_down_limit,
    compute_up_limit,
)


class IsStTests(unittest.TestCase):
    def test_recognizes_st_prefix(self):
        self.assertTrue(_is_st("ST 德豪"))
        self.assertTrue(_is_st("ST洲际"))
        self.assertTrue(_is_st("st 弘业"))

    def test_recognizes_star_st_prefix(self):
        self.assertTrue(_is_st("*ST 联翔"))
        self.assertTrue(_is_st("*ST美芝"))

    def test_recognizes_s_star_st(self):
        self.assertTrue(_is_st("S*ST 中纺机"))  # 老规则（极少）

    def test_rejects_normal_names(self):
        self.assertFalse(_is_st("贵州茅台"))
        self.assertFalse(_is_st("弘业期货"))
        self.assertFalse(_is_st(""))
        # 名称中包含 ST 但不在前缀：仍按非 ST 处理
        # 实际场景几乎不存在这种命名，仅作安全检查
        self.assertFalse(_is_st("TEST"))


class BoardDetectionTests(unittest.TestCase):
    def test_main_board(self):
        self.assertTrue(_is_main_board("sh600519"))
        self.assertTrue(_is_main_board("sh601318"))
        self.assertTrue(_is_main_board("sh603259"))
        self.assertTrue(_is_main_board("sh605499"))
        self.assertTrue(_is_main_board("sz000001"))
        self.assertTrue(_is_main_board("sz002415"))
        self.assertTrue(_is_main_board("sz003000"))

    def test_main_board_excludes(self):
        self.assertFalse(_is_main_board("sh688001"))  # 科创板
        self.assertFalse(_is_main_board("sz300750"))  # 创业板
        self.assertFalse(_is_main_board("bj830799"))  # 北交所

    def test_chinext(self):
        self.assertTrue(_is_chinext("sz300750", ""))
        self.assertTrue(_is_chinext("sz301236", ""))

    def test_star(self):
        self.assertTrue(_is_star("sh688001"))
        self.assertTrue(_is_star("sh689009"))

    def test_bj(self):
        self.assertTrue(_is_bj("bj830799"))
        self.assertTrue(_is_bj("bj430047"))


class RatioTests(unittest.TestCase):
    """验证各板块 × ST 状态下的 ratio 是否正确。"""

    def test_main_board_normal(self):
        self.assertEqual(_ratio("sh600519", "贵州茅台"), 0.9)
        self.assertEqual(_ratio("sz000001", "平安银行"), 0.9)

    def test_main_board_st_new_rule(self):
        """2026-07-06 新规：主板 ST 也是 ±10%"""
        self.assertEqual(_ratio("sh600519", "ST 茅台"), 0.9)
        self.assertEqual(_ratio("sh600001", "*ST 股票"), 0.9)
        self.assertEqual(_ratio("sz000001", "*ST 平安"), 0.9)
        self.assertEqual(_ratio("sz002415", "ST 海康"), 0.9)

    def test_chinext_normal(self):
        self.assertEqual(_ratio("sz300750", "宁德时代"), 0.8)

    def test_chinext_st_unchanged(self):
        """创业板 ST 维持 ±20%（不受新规影响）"""
        self.assertEqual(_ratio("sz300750", "ST 宁德"), 0.8)
        self.assertEqual(_ratio("sz301236", "*ST 某股"), 0.8)

    def test_star_normal(self):
        self.assertEqual(_ratio("sh688001", "中芯国际"), 0.8)

    def test_star_st_unchanged(self):
        """科创板 ST 维持 ±20%"""
        self.assertEqual(_ratio("sh688001", "ST 中芯"), 0.8)

    def test_bj_normal(self):
        self.assertEqual(_ratio("bj830799", "贝特瑞"), 0.7)

    def test_bj_st_unchanged(self):
        """北交所 ST 维持 ±30%"""
        self.assertEqual(_ratio("bj830799", "ST 贝特瑞"), 0.7)


class ComputeTests(unittest.TestCase):
    """端到端：给定 code/name/close 算涨停/跌停价"""

    DATE = "2026-08-06"

    def test_main_board_st_new_rule(self):
        # 2026-07-06 新规后：主板 ST 也是 ±10%
        # 跌停价 = 100 * 0.9 = 90.0
        self.assertEqual(
            compute_down_limit("600519.SH", "ST 茅台", 100.0, self.DATE),
            90.0,
        )
        # 涨停价 = 100 * 1.1 = 110.0
        self.assertEqual(
            compute_up_limit("600519.SH", "ST 茅台", 100.0, self.DATE),
            110.0,
        )

    def test_main_board_normal(self):
        self.assertEqual(compute_down_limit("600519.SH", "贵州茅台", 100.0, self.DATE), 90.0)
        self.assertEqual(compute_up_limit("600519.SH", "贵州茅台", 100.0, self.DATE), 110.0)

    def test_chinext_st_unchanged(self):
        # 创业板 ST：±20%，跟普通股一样
        self.assertEqual(compute_down_limit("300750.SZ", "ST 宁德", 100.0, self.DATE), 80.0)
        self.assertEqual(compute_up_limit("300750.SZ", "ST 宁德", 100.0, self.DATE), 120.0)

    def test_star_st_unchanged(self):
        self.assertEqual(compute_down_limit("688001.SH", "ST 中芯", 100.0, self.DATE), 80.0)

    def test_bj_st_unchanged(self):
        # 北交所：±30%，保留 4 位小数
        self.assertEqual(compute_down_limit("830799.BJ", "ST 贝特瑞", 100.0, self.DATE), 70.0)

    def test_bj_normal(self):
        self.assertEqual(compute_down_limit("830799.BJ", "贝特瑞", 100.0, self.DATE), 70.0)
        self.assertEqual(compute_up_limit("830799.BJ", "贝特瑞", 100.0, self.DATE), 130.0)

    def test_invalid_close_returns_none(self):
        self.assertIsNone(compute_down_limit("600519.SH", "茅台", 0, self.DATE))
        self.assertIsNone(compute_down_limit("600519.SH", "茅台", -1.0, self.DATE))
        self.assertIsNone(compute_down_limit("600519.SH", "茅台", None, self.DATE))
        self.assertIsNone(compute_up_limit("600519.SH", "茅台", 0, self.DATE))

    def test_rounding(self):
        # 2 位小数（主板 / 创业 / 科创）
        # 100.99 * 0.9 = 90.891 → round(_, 2) = 90.89
        self.assertEqual(
            compute_down_limit("600519.SH", "茅台", 100.99, self.DATE),
            90.89,
        )
        # 100.99 * 0.8 = 80.792 → 80.79
        self.assertEqual(
            compute_down_limit("300750.SZ", "宁德", 100.99, self.DATE),
            80.79,
        )


class CodeNormalizationTests(unittest.TestCase):
    """代码格式兼容：6 位 / 带后缀 / 各种写法都能正确识别板块"""

    DATE = "2026-08-06"

    def test_dot_suffix(self):
        self.assertEqual(compute_down_limit("600519.SH", "茅台", 100.0, self.DATE), 90.0)
        self.assertEqual(compute_down_limit("300750.SZ", "宁德", 100.0, self.DATE), 80.0)
        self.assertEqual(compute_down_limit("830799.BJ", "贝特瑞", 100.0, self.DATE), 70.0)

    def test_bare_six_digit_main(self):
        """裸 6 位代码 + 主板可推断（600/601/603/605 都是沪市主板）"""
        self.assertEqual(compute_down_limit("600519", "茅台", 100.0, self.DATE), 90.0)
        # sz 段纯数字默认 fallback 到 ±10%（无法区分主板/创业板）
        self.assertEqual(compute_down_limit("000001", "平安", 100.0, self.DATE), 90.0)

    def test_bare_six_digit_ambiguous(self):
        """没有市场后缀的创业板/科创板代码：识别不出，按 ±10% 兜底
        （原代码同行为；调用方应在传入前补上 .SZ/.SH 后缀）"""
        # 300750 没后缀 → 不知道是哪个板块 → 兜底 10%
        self.assertEqual(compute_down_limit("300750", "宁德", 100.0, self.DATE), 90.0)
        # 688001 同理
        self.assertEqual(compute_down_limit("688001", "中芯", 100.0, self.DATE), 90.0)

    def test_lowercase_suffix(self):
        self.assertEqual(compute_down_limit("600519.sh", "茅台", 100.0, self.DATE), 90.0)

    def test_market_prefix(self):
        # 异型写法：market 在前
        self.assertEqual(compute_down_limit("sh.600519", "茅台", 100.0, self.DATE), 90.0)


if __name__ == "__main__":
    unittest.main()
