"""
A 股涨跌停价格计算（基于前收盘价 + 涨跌幅比例）。

涨跌幅规则（按板块 × 是否 ST 区分）：

| 板块                  | 普通股 | ST/*ST | 备注                         |
|-----------------------|--------|--------|------------------------------|
| 沪深主板（60/00 开头）| ±10%   | ±10%   | 2026-07-06 起 ST 跟普通股并轨 |
| 创业板（30 开头）     | ±20%   | ±20%   | ST 不影响                    |
| 科创板（68/688 开头）  | ±20%   | ±20%   | ST 不影响                    |
| 北交所（8/43 开头）   | ±30%   | ±30%   | ST 不影响                    |

历史变更：
- 2026-07-06：沪深主板 ST/*ST 涨跌幅从 ±5% 上调至 ±10%（沪深北交易所同日实施）
- 2020-08-24：创业板涨跌幅从 ±10% 上调至 ±20%（注册制改革）
- 2019-07-22：科创板首批上市，涨跌幅 ±20%

输入：code（任意格式，6 位数字 + 可选 .SH/.SZ/.BJ）、name（股票简称）、
      last_close（前收盘价）、trade_date（YYYY-MM-DD 或 YYYYMMDD）
输出：涨停价 / 跌停价（浮点，None 表示无法计算，如 last_close 无效）
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


def _normalize_code(code: str) -> str:
    raw = str(code or "").strip()
    if not raw:
        return ""
    if "." not in raw:
        return raw.lower()
    symbol, market = raw.split(".", 1)
    return f"{market.lower()}{symbol.lower()}"


def _is_main_board(norm_code: str) -> bool:
    """
    沪深主板：sh60xxxx（600/601/603/605）、sz00xxxx（000/001/002/003）。
    注意：科创板 sh68xxxx / 创业板 sz3xxxx 不属于主板。
    """
    return norm_code.startswith("sh60") or norm_code.startswith("sz00")


def _is_chinext(norm_code: str, trade_day: str) -> bool:
    """
    创业板：sz3xxxx。注册制改革（2020-08-24）后涨跌幅 ±20%。
    改革前上市的创业板股票用旧规则（±10%），但代码格式仍是 sz3xxxx。
    本计算统一按 ±20% 处理（改革后已 6 年，存量股基本都已纳入新规则体系）。
    """
    return norm_code.startswith("sz3")


def _is_star(norm_code: str) -> bool:
    """科创板：sh68xxxx（688/689）。"""
    return norm_code.startswith("sh68")


def _is_bj(norm_code: str) -> bool:
    """北交所：bj 开头（8/43/83/87 等）。"""
    return norm_code.startswith("bj")


def _is_st(name: str) -> bool:
    """
    ST/*ST 识别：通过简称前缀判断。
    匹配模式：名称以 ST / *ST / S*ST 开头。
    注意：此判断依赖简称前缀，不能识别"公司被 ST 但简称尚未变更"的瞬时状态。
    """
    n = str(name or "").upper()
    return n.startswith("ST") or n.startswith("*ST") or n.startswith("S*ST")


def _ratio(norm_code: str, name: str) -> float:
    """
    根据板块 + ST 状态返回涨跌幅比例。
    """
    is_st = _is_st(name)

    if _is_star(norm_code) or _is_chinext(norm_code, ""):
        # 创业板 / 科创板：±20%，ST 不影响
        return 0.8
    if _is_bj(norm_code):
        # 北交所：±30%，ST 不影响
        return 0.7
    if _is_main_board(norm_code):
        # 沪深主板：±10%（2026-07-06 起 ST 跟普通股并轨）
        return 0.9

    # 未知板块（极少：B 股、债券等），默认 ±10%
    return 0.9


def compute_down_limit(code: str, name: str, last_close: float, trade_date: str) -> float | None:
    if last_close is None or last_close <= 0:
        return None

    norm_code = _normalize_code(code)
    norm_name = str(name or "")
    trade_day = str(trade_date or "").replace("-", "")

    ratio = _ratio(norm_code, norm_name)
    raw = float(last_close) * ratio
    if norm_code.startswith("bj"):
        return float(Decimal(str(round(raw, 6))).quantize(Decimal("0.01"), ROUND_DOWN))
    return round(raw, 2)


def compute_up_limit(code: str, name: str, last_close: float, trade_date: str) -> float | None:
    if last_close is None or last_close <= 0:
        return None

    norm_code = _normalize_code(code)
    norm_name = str(name or "")
    trade_day = str(trade_date or "").replace("-", "")

    ratio = 1.0 + (1.0 - _ratio(norm_code, norm_name))
    raw = float(last_close) * ratio
    if norm_code.startswith("bj"):
        return float(Decimal(str(round(raw, 6))).quantize(Decimal("0.01"), ROUND_HALF_UP))
    return round(raw, 2)
