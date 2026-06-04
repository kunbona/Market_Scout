"""
概念板块资金流抓取。
降级策略：stock_fund_flow_concept 走 push2.eastmoney.com，服务器 IP 被封时静默跳过。
"""
import logging
from datetime import datetime
import akshare as ak
from db.storage import insert_concept_flow

logger = logging.getLogger(__name__)


def fetch_concept_flow() -> None:
    try:
        fetch_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df = ak.stock_fund_flow_concept(symbol="即时")
        if df is None or df.empty:
            return

        col_concept  = next((c for c in df.columns if "行业" in c and "涨" not in c), None)
        col_change   = next((c for c in df.columns if "涨跌幅" in c), None)
        col_net      = next((c for c in df.columns if "净额" in c), None)
        col_in       = next((c for c in df.columns if "流入" in c), None)
        col_out      = next((c for c in df.columns if "流出" in c), None)
        col_lead     = next((c for c in df.columns if "领涨股" in c and "涨跌" not in c), None)
        col_lead_pct = next((c for c in df.columns if "领涨股" in c and "涨跌" in c), None)
        col_count    = next((c for c in df.columns if "公司" in c or "家数" in c), None)

        for _, row in df.iterrows():
            concept = str(row[col_concept]).strip() if col_concept else ""
            if not concept:
                continue

            def _f(col):
                try:
                    return float(row[col]) if col and col in row.index else 0.0
                except Exception:
                    return 0.0

            def _i(col):
                try:
                    return int(row[col]) if col and col in row.index else 0
                except Exception:
                    return 0

            insert_concept_flow(
                fetch_time, concept,
                _f(col_change), _f(col_net), _f(col_in), _f(col_out),
                str(row[col_lead]).strip() if col_lead else "",
                _f(col_lead_pct),
                _i(col_count),
            )
        logger.info("[concept_flow] 写入 %d 条概念资金流", len(df))
    except Exception as e:
        # push2.eastmoney.com 在服务器 IP 上被封，静默降级（不刷 warning）
        logger.debug("[concept_flow] 跳过（东财 push2 不可用）: %s", e)
