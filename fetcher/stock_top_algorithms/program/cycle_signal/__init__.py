"""周期信号生产模块

把已验证（2014-2026）的牛熊周期定位 + 逃顶/抄底信号搬到每日生产链路。
消费 data/processed 已有指标 CSV，零新数据依赖。
"""

from program.cycle_signal.runner import (
    build_cycle_report,
    run_cycle_signal_step,
)

__all__ = ["build_cycle_report", "run_cycle_signal_step"]
