"""
腾讯行情接口封装 — qt.gtimg.cn

特点：零封禁风险、62ms 延迟、含 PE/PB/市值/换手率等字段。
作为 P1 级数据源，替代已损坏的 mootdx。
"""
import logging
from typing import Optional

from fetcher.http_util import get_session

logger = logging.getLogger(__name__)

_SESSION = None

# qt.gtimg.cn 返回 GBK 编码，~ 分隔，字段索引（0-based，经实测确认）
# v_sh600519="1~贵州茅台~600519~1272.86~昨收~今开~成交量~..."
_FIELD_MAP = {
    "name":         1,
    "code":         2,
    "price":        3,   # 最新价
    "prev_close":   4,   # 昨收
    "open":         5,   # 今开
    "volume":       6,   # 成交量（手）
    "high":         31,  # 今日最高
    "low":          32,  # 今日最低
    "change":       33,  # 涨跌额
    "change_pct":   34,  # 涨跌幅%
    "amplitude":    35,  # 振幅%
    "float_cap":    36,  # 流通市值（亿元）
    "market_cap":   37,  # 总市值（亿元）
    "pe_ttm":       38,  # 市盈率 TTM
    "turnover":     44,  # 换手率%
    "volume_ratio": 45,  # 量比
    "pb":           46,  # 市净率 PB
}


def _get_session():
    global _SESSION
    if _SESSION is None:
        _SESSION = get_session("qt.gtimg.cn")
    return _SESSION


def code_to_tencent(code: str) -> str:
    """将 A 股代码转为腾讯格式。600519 → sh600519，000858 → sz000858。"""
    code = code.strip()
    if code[:2].lower() in ("sh", "sz", "bj"):
        return code.lower()
    if code.startswith(("60", "68", "51", "58", "56", "50")):
        return f"sh{code}"
    if code.startswith(("00", "30", "15", "16", "18", "12", "13")):
        return f"sz{code}"
    if code.startswith(("43", "83", "87", "88", "92")):
        return f"bj{code}"
    return f"sh{code}"


def _parse_line(line: str) -> Optional[dict]:
    """解析一行 qtimg 数据，返回规范化字段 dict 或 None。"""
    if "=" not in line or "~" not in line:
        return None
    try:
        raw_key, rest = line.split("=", 1)
        orig_code = raw_key.strip().lstrip("v_")  # sh600519
        values = rest.strip().strip('"').split("~")
        if len(values) < 50:
            return None

        def _f(idx: int, default: float = 0.0) -> float:
            try:
                v = values[idx].replace("%", "").replace(",", "").strip()
                return float(v) if v not in ("", "-", "--", "N/A") else default
            except Exception:
                return default

        return {
            "tencent_code": orig_code,
            "name":         values[_FIELD_MAP["name"]],
            "price":        _f(_FIELD_MAP["price"]),
            "prev_close":   _f(_FIELD_MAP["prev_close"]),
            "open":         _f(_FIELD_MAP["open"]),
            "high":         _f(_FIELD_MAP["high"]),
            "low":          _f(_FIELD_MAP["low"]),
            "change":       _f(_FIELD_MAP["change"]),
            "change_pct":   _f(_FIELD_MAP["change_pct"]),
            "amplitude":    _f(_FIELD_MAP["amplitude"]),
            "volume":       _f(_FIELD_MAP["volume"]),
            "float_cap":    _f(_FIELD_MAP["float_cap"]),
            "market_cap":   _f(_FIELD_MAP["market_cap"]),
            "pe_ttm":       _f(_FIELD_MAP["pe_ttm"]),
            "pb":           _f(_FIELD_MAP["pb"]),
            "turnover":     _f(_FIELD_MAP["turnover"]),
            "volume_ratio": _f(_FIELD_MAP["volume_ratio"]),
        }
    except Exception:
        return None


def fetch_quotes(codes: list[str]) -> dict[str, dict]:
    """
    批量获取腾讯实时行情。
    输入：6 位 A 股代码列表，如 ["600519", "000858"]
    返回：{code: {字段dict}}，失败的代码不在结果中。
    """
    if not codes:
        return {}

    session = _get_session()
    result: dict[str, dict] = {}

    for i in range(0, len(codes), 80):
        batch = codes[i: i + 80]
        q = ",".join(code_to_tencent(c) for c in batch)
        try:
            resp = session.get(
                "https://qt.gtimg.cn/q=" + q,
                timeout=8,
                headers={"Referer": "https://gu.qq.com/"},
            )
            resp.encoding = "gbk"
            for line in resp.text.strip().split("\n"):
                parsed = _parse_line(line)
                if parsed is None:
                    continue
                tc = parsed["tencent_code"]
                raw6 = tc[2:] if tc[:2] in ("sh", "sz", "bj") else tc
                if raw6 in codes:
                    result[raw6] = parsed
        except Exception as e:
            logger.warning("[tencent] fetch_quotes batch %d failed: %s", i // 80, e)

    return result
