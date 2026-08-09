"""
顶底标注模块

阶段1：基于上证指数的历史顶底识别。

包含算法自动识别和手动标注两种方式，
并实现非对称预警窗口（pre_top=20天, pre_bottom=45天）。
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from config import RAW_DATA_DIR, DATA_DIR


# ==================== 常量配置 ====================

# 算法参数
PEAK_WINDOW = 90          # 局部极值搜索窗口（前后各N个交易日）
MIN_PEAK_WINDOW = 45      # 数据边界处的最小窗口
DRAWDOWN_THRESHOLD = 0.15  # 顶部区域扩展阈值（回撤达15%）
REBOUND_THRESHOLD = 0.15   # 底部区域扩展阈值（反弹达15%）
MIN_DRAWDOWN = 0.18        # 顶部确认的最小回撤
MIN_REBOUND = 0.20         # 底部确认的最小反弹
MIN_SAME_TYPE_GAP = 60     # 同类标注最小间隔（交易日）
UNCONFIRMED_WINDOW = 120   # 未确认候选的窗口（交易日）

# 预警窗口
PRE_TOP_DAYS = 30          # 顶部预警窗口（交易日）
PRE_BOTTOM_DAYS = 45       # 底部预警窗口（交易日）

# 标签优先级（数值越大优先级越高）
LABEL_PRIORITY = {
    "top_zone": 100,
    "bottom_zone": 100,
    "pre_top_zone": 50,
    "pre_bottom_zone": 50,
    "neutral": 0,
}

# 置信度权重映射
CONFIDENCE_WEIGHTS = {
    "high": 1.0,
    "medium": 0.7,
    "low": 0.4,
}

# 手动标注文件路径
MANUAL_LABELS_PATH = DATA_DIR / "top_bottom_labels.csv"


def load_index_data(
    filepath: Optional[Path] = None,
) -> pd.DataFrame:
    """加载上证指数日线数据

    Args:
        filepath: 上证指数CSV路径，默认使用标准路径

    Returns:
        pd.DataFrame: 包含 date 和 close 列，以 date 为索引
    """
    if filepath is None:
        filepath = RAW_DATA_DIR / "stock_zh_index_daily_sh000001.csv"

    df = pd.read_csv(filepath, encoding="utf-8-sig")
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    # 只保留收盘价
    df = df[["close"]].copy()
    df["close"] = df["close"].astype(float)

    return df


def _find_local_extrema(
    close: pd.Series,
    window: int,
    min_window: int,
    kind: str,
) -> List[int]:
    """寻找局部极值点

    Args:
        close: 收盘价序列
        window: 搜索窗口大小
        min_window: 最小窗口（边界处）
        kind: "max"（顶部）或 "min"（底部）

    Returns:
        极值点在 close 中的位置索引列表
    """
    n = len(close)
    values = close.values
    extrema = []

    for i in range(n):
        # 动态窗口（边界处缩短，但不低于 min_window）
        left = max(min_window, min(window, i))
        right = max(min_window, min(window, n - 1 - i))

        start = max(0, i - left)
        end = min(n, i + right + 1)
        local = values[start:end]

        if kind == "max":
            if values[i] == np.max(local):
                extrema.append(i)
        else:
            if values[i] == np.min(local):
                extrema.append(i)

    return extrema


def _find_zone_end(
    close: pd.Series,
    peak_idx: int,
    threshold: float,
    kind: str,
) -> Optional[int]:
    """从极值点向后扩展，找到区域结束位置

    Args:
        close: 收盘价序列
        peak_idx: 极值点位置
        threshold: 扩展阈值（回撤/反弹比例）
        kind: "top"（顶部，找回撤）或 "bottom"（底部，找反弹）

    Returns:
        区域结束位置索引，若未达阈值则返回 None
    """
    values = close.values
    peak_val = values[peak_idx]

    for i in range(peak_idx + 1, len(values)):
        if kind == "top":
            change = (peak_val - values[i]) / peak_val
        else:
            change = (values[i] - peak_val) / peak_val

        if change >= threshold:
            return i

    return None


def _check_confirmation(
    close: pd.Series,
    peak_idx: int,
    min_threshold: float,
    kind: str,
) -> bool:
    """检查极值点后续是否达到确认阈值

    Args:
        close: 收盘价序列
        peak_idx: 极值点位置
        min_threshold: 确认阈值
        kind: "top"（需≥18%回撤）或 "bottom"（需≥20%反弹）

    Returns:
        是否确认
    """
    values = close.values
    peak_val = values[peak_idx]

    if kind == "top":
        subsequent_min = np.min(values[peak_idx:])
        return (peak_val - subsequent_min) / peak_val >= min_threshold
    else:
        subsequent_max = np.max(values[peak_idx:])
        return (subsequent_max - peak_val) / peak_val >= min_threshold


def auto_detect_tops_bottoms(
    index_data: pd.DataFrame,
) -> pd.DataFrame:
    """算法自动识别顶底区域

    Args:
        index_data: 上证指数数据（含 close 列）

    Returns:
        pd.DataFrame: 包含 start_date, end_date, label, confidence, note 列
    """
    close = index_data["close"]
    dates = index_data.index
    n = len(close)

    events = []

    # === 识别顶部 ===
    top_peaks = _find_local_extrema(close, PEAK_WINDOW, MIN_PEAK_WINDOW, "max")
    for peak_idx in top_peaks:
        # 检查确认条件
        if not _check_confirmation(close, peak_idx, MIN_DRAWDOWN, "top"):
            # 检查是否为未确认候选
            if n - peak_idx <= UNCONFIRMED_WINDOW:
                continue  # 最近的未确认，跳过
            continue

        # 扩展区域
        zone_end_idx = _find_zone_end(close, peak_idx, DRAWDOWN_THRESHOLD, "top")
        if zone_end_idx is None:
            zone_end_idx = min(peak_idx + 20, n - 1)  # 默认扩展20天

        start_date = dates[peak_idx]
        end_date = dates[zone_end_idx]
        peak_price = close.iloc[peak_idx]

        events.append({
            "start_date": start_date,
            "end_date": end_date,
            "label": "top_zone",
            "confidence": "medium",
            "note": f"算法识别: {peak_price:.0f}点顶",
            "_peak_idx": peak_idx,
        })

    # === 识别底部 ===
    bottom_troughs = _find_local_extrema(close, PEAK_WINDOW, MIN_PEAK_WINDOW, "min")
    for trough_idx in bottom_troughs:
        if not _check_confirmation(close, trough_idx, MIN_REBOUND, "bottom"):
            if n - trough_idx <= UNCONFIRMED_WINDOW:
                continue
            continue

        zone_end_idx = _find_zone_end(close, trough_idx, REBOUND_THRESHOLD, "bottom")
        if zone_end_idx is None:
            zone_end_idx = min(trough_idx + 20, n - 1)

        start_date = dates[trough_idx]
        end_date = dates[zone_end_idx]
        trough_price = close.iloc[trough_idx]

        events.append({
            "start_date": start_date,
            "end_date": end_date,
            "label": "bottom_zone",
            "confidence": "medium",
            "note": f"算法识别: {trough_price:.0f}点底",
            "_peak_idx": trough_idx,
        })

    # 按开始日期排序
    events.sort(key=lambda x: x["start_date"])

    # 合并过近的同类事件
    events = _merge_nearby_events(events, close, dates)

    # 转为 DataFrame
    result = pd.DataFrame(events)
    if not result.empty:
        result = result.drop(columns=["_peak_idx"], errors="ignore")

    return result


def _merge_nearby_events(
    events: List[Dict],
    close: pd.Series,
    dates: pd.DatetimeIndex,
) -> List[Dict]:
    """合并间隔过近的同类事件

    两个同类标注之间至少间隔60个交易日，否则合并。
    """
    if len(events) <= 1:
        return events

    # 构建交易日位置映射
    date_to_idx = {d: i for i, d in enumerate(dates)}

    merged = [events[0]]
    for event in events[1:]:
        prev = merged[-1]
        if event["label"] == prev["label"]:
            prev_end_idx = date_to_idx.get(prev["end_date"], 0)
            curr_start_idx = date_to_idx.get(event["start_date"], 0)
            gap = curr_start_idx - prev_end_idx

            if gap < MIN_SAME_TYPE_GAP:
                # 合并：扩展 end_date
                prev["end_date"] = max(prev["end_date"], event["end_date"])
                prev["note"] = prev["note"] + " (已合并)"
                continue

        merged.append(event)

    return merged


def load_manual_labels(
    filepath: Optional[Path] = None,
) -> pd.DataFrame:
    """加载手动标注文件

    Args:
        filepath: 标注文件路径

    Returns:
        pd.DataFrame: 手动标注数据
    """
    filepath = filepath or MANUAL_LABELS_PATH

    if not filepath.exists():
        print(f"⚠ 手动标注文件不存在: {filepath}")
        return pd.DataFrame(
            columns=["start_date", "end_date", "label", "confidence", "note"]
        )

    df = pd.read_csv(filepath, encoding="utf-8-sig")
    df["start_date"] = pd.to_datetime(df["start_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])

    return df


def create_default_manual_labels(
    filepath: Optional[Path] = None,
) -> None:
    """创建默认的手动标注文件（基于 spec 3.2 节）

    Args:
        filepath: 输出路径
    """
    filepath = filepath or MANUAL_LABELS_PATH

    labels = pd.DataFrame([
        {"start_date": "2015-06-05", "end_date": "2015-07-08", "label": "top_zone",
         "confidence": "high", "note": "5178点大顶"},
        {"start_date": "2015-08-25", "end_date": "2015-09-15", "label": "bottom_zone",
         "confidence": "medium", "note": "3373点次级底(杠杆清洗)"},
        {"start_date": "2016-01-27", "end_date": "2016-02-29", "label": "bottom_zone",
         "confidence": "high", "note": "熔断底"},
        {"start_date": "2018-01-29", "end_date": "2018-02-09", "label": "top_zone",
         "confidence": "medium", "note": "3587点顶"},
        {"start_date": "2018-10-19", "end_date": "2018-12-31", "label": "bottom_zone",
         "confidence": "high", "note": "2449点底"},
        {"start_date": "2019-04-08", "end_date": "2019-04-22", "label": "top_zone",
         "confidence": "low", "note": "3288点次级顶"},
        {"start_date": "2020-03-19", "end_date": "2020-03-31", "label": "bottom_zone",
         "confidence": "low", "note": "疫情底(次级)"},
        {"start_date": "2021-02-10", "end_date": "2021-03-09", "label": "top_zone",
         "confidence": "medium", "note": "抱团瓦解顶"},
        {"start_date": "2022-04-27", "end_date": "2022-05-10", "label": "bottom_zone",
         "confidence": "low", "note": "2863点次级底"},
        {"start_date": "2024-02-05", "end_date": "2024-02-08", "label": "bottom_zone",
         "confidence": "high", "note": "2635点底"},
    ])

    labels.to_csv(filepath, index=False, encoding="utf-8-sig")
    print(f"✓ 已创建手动标注文件: {filepath} ({len(labels)} 条记录)")


def merge_labels(
    algo_labels: pd.DataFrame,
    manual_labels: pd.DataFrame,
    trading_dates: pd.DatetimeIndex,
) -> pd.Series:
    """合并算法标注和手动标注，生成每日标签

    手动标注优先级高于算法标注。
    包含非对称预警窗口。

    Args:
        algo_labels: 算法生成的标注
        manual_labels: 手动标注
        trading_dates: 完整的交易日序列

    Returns:
        pd.Series: 每个交易日的标签，索引为日期
    """
    # 初始化所有日期为 neutral
    daily_labels = pd.Series("neutral", index=trading_dates, name="label")

    # 同时维护优先级和置信度
    daily_priority = pd.Series(0, index=trading_dates, dtype=int)
    daily_confidence = pd.Series("none", index=trading_dates, name="confidence")
    daily_is_manual = pd.Series(False, index=trading_dates, dtype=bool)

    # 收集所有事件（用于预警窗口计算）
    all_events = []

    # 先处理算法标注（低优先级）
    if not algo_labels.empty:
        for _, row in algo_labels.iterrows():
            all_events.append({
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "label": row["label"],
                "confidence": row.get("confidence", "medium"),
                "is_manual": False,
                "source": "algorithm",
            })

    # 再处理手动标注（高优先级，会覆盖算法标注）
    if not manual_labels.empty:
        for _, row in manual_labels.iterrows():
            all_events.append({
                "start_date": pd.Timestamp(row["start_date"]),
                "end_date": pd.Timestamp(row["end_date"]),
                "label": row["label"],
                "confidence": row.get("confidence", "high"),
                "is_manual": True,
                "source": "manual",
            })

    # 按时间排序
    all_events.sort(key=lambda x: x["start_date"])

    # 1. 先标记所有 zone（手动优先）
    for event in all_events:
        label = event["label"]
        priority = LABEL_PRIORITY[label]
        if event["is_manual"]:
            priority += 10  # 手动标注额外加权

        mask = (trading_dates >= event["start_date"]) & (
            trading_dates <= event["end_date"]
        )

        for dt in trading_dates[mask]:
            if priority >= daily_priority[dt]:
                daily_labels[dt] = label
                daily_priority[dt] = priority
                daily_confidence[dt] = event["confidence"]
                daily_is_manual[dt] = event["is_manual"]

    # 2. 添加预警窗口
    date_to_pos = {d: i for i, d in enumerate(trading_dates)}

    for event in all_events:
        label = event["label"]
        start_pos = date_to_pos.get(event["start_date"])
        if start_pos is None:
            # 找最近的交易日
            diffs = (trading_dates - event["start_date"]).total_seconds()
            pos_diffs = diffs[diffs >= 0]
            if len(pos_diffs) == 0:
                continue
            start_pos = np.argmin(pos_diffs)

        # 根据类型确定预警窗口大小
        if label == "top_zone":
            pre_label = "pre_top_zone"
            pre_days = PRE_TOP_DAYS
        elif label == "bottom_zone":
            pre_label = "pre_bottom_zone"
            pre_days = PRE_BOTTOM_DAYS
        else:
            continue

        pre_priority = LABEL_PRIORITY[pre_label]
        if event["is_manual"]:
            pre_priority += 5  # 手动标注的预警窗口也有略高优先级

        # 预警窗口的范围
        pre_start_pos = max(0, start_pos - pre_days)
        pre_end_pos = start_pos  # 不含 start_pos 本身

        for pos in range(pre_start_pos, pre_end_pos):
            dt = trading_dates[pos]
            if pre_priority > daily_priority[dt]:
                daily_labels[dt] = pre_label
                daily_priority[dt] = pre_priority
                daily_confidence[dt] = event["confidence"]

    return daily_labels


def build_event_list(
    daily_labels: pd.Series,
    manual_labels: pd.DataFrame,
) -> List[Dict]:
    """从每日标签构建事件列表

    将连续的同标签日期聚合为事件，并关联置信度和来源。

    Args:
        daily_labels: 每日标签序列
        manual_labels: 手动标注（用于获取置信度和来源判定）

    Returns:
        事件列表，每个事件包含:
        - start_date, end_date
        - label: top_zone 或 bottom_zone
        - confidence: high/medium/low
        - confidence_weight: 数值权重
        - source: "manual" 或 "algorithm"
        - zone_mask: 布尔数组（zone 区域）
        - pre_mask: 布尔数组（预警窗口）
    """
    events = []
    dates = daily_labels.index

    # 从手动标注获取置信度映射和来源判定
    confidence_map = {}
    manual_date_ranges = []
    if not manual_labels.empty:
        for _, row in manual_labels.iterrows():
            key = (pd.Timestamp(row["start_date"]), row["label"])
            confidence_map[key] = row.get("confidence", "medium")
            manual_date_ranges.append({
                "start": pd.Timestamp(row["start_date"]),
                "end": pd.Timestamp(row["end_date"]),
                "label": row["label"],
            })

    # 遍历每日标签，识别 zone 事件
    current_label = None
    current_start = None

    for i, (dt, label) in enumerate(daily_labels.items()):
        if label in ("top_zone", "bottom_zone"):
            if current_label != label:
                # 保存上一个事件
                if current_label is not None and current_start is not None:
                    _save_event(events, current_label, current_start, dates[i - 1],
                                daily_labels, confidence_map, manual_date_ranges)
                current_label = label
                current_start = dt
        else:
            if current_label is not None and current_start is not None:
                _save_event(events, current_label, current_start, dates[i - 1],
                            daily_labels, confidence_map, manual_date_ranges)
                current_label = None
                current_start = None

    # 处理最后一个事件
    if current_label is not None and current_start is not None:
        _save_event(events, current_label, current_start, dates[-1],
                    daily_labels, confidence_map, manual_date_ranges)

    return events


def _save_event(
    events: List[Dict],
    label: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    daily_labels: pd.Series,
    confidence_map: Dict,
    manual_date_ranges: List[Dict] = None,
) -> None:
    """保存一个事件到列表"""
    dates = daily_labels.index

    # 查找置信度
    confidence = "medium"
    for (key_date, key_label), conf in confidence_map.items():
        if key_label == label:
            if start_date <= key_date <= end_date or abs((start_date - key_date).days) <= 5:
                confidence = conf
                break

    # 判断来源：与手动标注有重叠则标为manual
    source = "algorithm"
    if manual_date_ranges:
        for mr in manual_date_ranges:
            if mr["label"] == label:
                # 日期有重叠或非常接近
                if (start_date <= mr["end"] and end_date >= mr["start"]) or \
                   abs((start_date - mr["start"]).days) <= 5:
                    source = "manual"
                    break

    # confidence_weight: manual来源额外提升
    base_weight = CONFIDENCE_WEIGHTS.get(confidence, 0.7)
    confidence_weight = base_weight * 1.5 if source == "manual" else base_weight

    # 构建 zone_mask 和 pre_mask
    zone_mask = (daily_labels == label) & (dates >= start_date) & (dates <= end_date)

    if label == "top_zone":
        pre_label = "pre_top_zone"
    else:
        pre_label = "pre_bottom_zone"

    pre_mask = (daily_labels == pre_label) & (dates < start_date)
    # 只取紧邻 zone 之前的连续预警窗口
    pre_indices = np.where(pre_mask)[0]
    zone_start_idx = np.where(dates == start_date)[0]
    if len(zone_start_idx) > 0 and len(pre_indices) > 0:
        zone_start_pos = zone_start_idx[0]
        valid_pre = pre_indices[pre_indices < zone_start_pos]
        if len(valid_pre) > 0:
            # 只保留连续的（从 zone_start 向前）
            pre_mask_arr = np.zeros(len(dates), dtype=bool)
            for idx in reversed(valid_pre):
                if idx == zone_start_pos - 1 or (len(pre_mask_arr.nonzero()[0]) > 0 and idx == pre_mask_arr.nonzero()[0].min() - 1):
                    pre_mask_arr[idx] = True
                else:
                    break
            pre_mask = pd.Series(pre_mask_arr, index=dates)

    events.append({
        "start_date": start_date,
        "end_date": end_date,
        "label": label,
        "confidence": confidence,
        "confidence_weight": confidence_weight,
        "source": source,
        "zone_mask": zone_mask.values,
        "pre_mask": pre_mask.values if isinstance(pre_mask, pd.Series) else pre_mask,
    })


def generate_labels(
    index_data: Optional[pd.DataFrame] = None,
    manual_labels_path: Optional[Path] = None,
    create_manual_if_missing: bool = True,
) -> Dict:
    """执行完整的标注流程

    Args:
        index_data: 上证指数数据，None 则自动加载
        manual_labels_path: 手动标注路径
        create_manual_if_missing: 若手动标注不存在是否创建默认版

    Returns:
        Dict:
            - index_data: 上证指数数据
            - daily_labels: 每日标签序列
            - events: 事件列表
            - manual_labels: 手动标注 DataFrame
            - algo_labels: 算法标注 DataFrame
            - stats: 标注统计
    """
    print("=" * 60)
    print("阶段1: 历史顶底标注")
    print("=" * 60)

    # 1. 加载上证指数
    if index_data is None:
        index_data = load_index_data()
    print(f"✓ 上证指数: {index_data.index[0].date()} ~ {index_data.index[-1].date()}, "
          f"{len(index_data)} 个交易日")

    # 2. 加载/创建手动标注
    manual_path = manual_labels_path or MANUAL_LABELS_PATH
    if create_manual_if_missing and not manual_path.exists():
        create_default_manual_labels(manual_path)
    manual_labels = load_manual_labels(manual_path)
    print(f"✓ 手动标注: {len(manual_labels)} 条")

    # 3. 算法识别
    algo_labels = auto_detect_tops_bottoms(index_data)
    print(f"✓ 算法识别: {len(algo_labels)} 个事件")

    # 4. 合并
    trading_dates = index_data.index
    daily_labels = merge_labels(algo_labels, manual_labels, trading_dates)

    # 5. 构建事件列表
    events = build_event_list(daily_labels, manual_labels)

    # 6. 统计
    label_counts = daily_labels.value_counts()
    top_events = [e for e in events if e["label"] == "top_zone"]
    bottom_events = [e for e in events if e["label"] == "bottom_zone"]

    stats = {
        "total_days": len(daily_labels),
        "label_counts": label_counts.to_dict(),
        "top_events": len(top_events),
        "bottom_events": len(bottom_events),
        "high_conf_events": sum(1 for e in events if e["confidence"] == "high"),
        "medium_conf_events": sum(1 for e in events if e["confidence"] == "medium"),
        "low_conf_events": sum(1 for e in events if e["confidence"] == "low"),
    }

    print()
    print("标注统计:")
    for label_name, count in label_counts.items():
        pct = count / len(daily_labels) * 100
        print(f"  {label_name}: {count} 天 ({pct:.1f}%)")
    print(f"  顶部事件: {len(top_events)}, 底部事件: {len(bottom_events)}")

    return {
        "index_data": index_data,
        "daily_labels": daily_labels,
        "events": events,
        "top_events": top_events,
        "bottom_events": bottom_events,
        "manual_labels": manual_labels,
        "algo_labels": algo_labels,
        "stats": stats,
    }
