# QMT Industry Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `行业统计榜` section to the existing `QMT数据` page, backed by a dedicated `GET /api/qmt-industry-stats` endpoint that aggregates the full QMT A-share universe by `industry_l1`.

**Architecture:** Reuse the existing QMT stock universe and tick snapshot functions for realtime fields, reuse local trading data under `QUANT_DATA_ROOT` for industry classification, MA10 baseline, and yesterday main capital net inflow, and keep all heavy aggregation in Python so the frontend remains render-only. The new endpoint lives beside the current QMT overview / monitor endpoints and does not change the existing dragger logic.

**Tech Stack:** Python, Flask, SQLite, React, TypeScript, Vite, unittest

---

## File Map

- `d:\market-radar\fetcher\qmt_monitors.py`
  - Add stock-level normalization helpers for full-universe industry stats.
  - Add one backend payload builder for industry aggregation.
  - Reuse current limit-up / limit-down price rules.

- `d:\market-radar\fetcher\qmt_data_api.py`
  - Reuse `list_a_shares()` and `get_full_tick_snapshot()`.
  - Add a focused helper only if existing functions need a thinner interface for this payload.

- `d:\market-radar\quant\loader.py`
  - Reuse `get_trading_data()`.
  - No structural rewrite in v1. Only touch if a tiny helper is needed to avoid repeated local CSV parsing logic.

- `d:\market-radar\server.py`
  - Add `GET /api/qmt-industry-stats`.
  - Reuse `_read_qmt_runtime_status()`, `_get_qmt_overview_breadth()`, and `_current_qmt_trade_date()` for stable snapshot / status behavior.

- `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`
  - Add `QmtIndustryStats` type.
  - Include the new endpoint in the QMT page's parallel fetch flow.
  - Render a new `行业统计榜` card below the current `行业拖累榜`.

- `d:\market-radar\tests\test_qmt_helper_layers.py`
  - Add focused tests for stock-level stat normalization and industry aggregation.

- `d:\market-radar\tests\test_qmt_monitor_api.py`
  - Extend API coverage with the new `/api/qmt-industry-stats` route.

## Shared Rules To Preserve

- Stock universe must come from `list_a_shares()`.
- Industry must use local `industry_l1`.
- `大肉数量` must use `current_change_pct >= 5`.
- `10日线上占比` must use `last_price > ma10`.
- `昨日主力净流入` must use local `inst_buy - inst_sell`.
- The new feature must not replace or alter the existing `行业拖累榜`.
- The frontend must still call `vite build` after `.tsx` changes.

### Task 1: Add Failing Helper Tests For Industry Stats Aggregation

**Files:**
- Modify: `d:\market-radar\tests\test_qmt_helper_layers.py`
- Read for reference: `d:\market-radar\fetcher\qmt_monitors.py`

- [ ] **Step 1: Write the failing test for stock-level normalization**

Add this test class and method to `d:\market-radar\tests\test_qmt_helper_layers.py`:

```python
import unittest

from fetcher.qmt_monitors import build_qmt_industry_stats_payload


class QmtIndustryStatsHelperTests(unittest.TestCase):
    def test_build_qmt_industry_stats_payload_aggregates_expected_metrics(self) -> None:
        rows = [
            {
                "stock_code": "000070.SZ",
                "stock_name": "特发信息",
                "sector": "通信",
                "last_price": 22.29,
                "last_close": 24.77,
                "ma10": 21.50,
                "up_limit": 27.25,
                "down_limit": 22.29,
                "yesterday_main_inflow": 120000000.0,
            },
            {
                "stock_code": "600498.SH",
                "stock_name": "烽火通信",
                "sector": "通信",
                "last_price": 15.80,
                "last_close": 14.90,
                "ma10": 15.20,
                "up_limit": 16.39,
                "down_limit": 13.41,
                "yesterday_main_inflow": -20000000.0,
            },
            {
                "stock_code": "000967.SZ",
                "stock_name": "盈峰环境",
                "sector": "环保",
                "last_price": 7.10,
                "last_close": 7.25,
                "ma10": 7.30,
                "up_limit": 7.98,
                "down_limit": 6.53,
                "yesterday_main_inflow": 5000000.0,
            },
        ]

        payload = build_qmt_industry_stats_payload(rows)

        self.assertEqual(payload["summary"]["sector_count"], 2)
        self.assertEqual(payload["summary"]["strong_ma10_sector_count"], 1)
        self.assertEqual(payload["summary"]["top_meat_sector"], "通信")
        self.assertEqual(payload["summary"]["top_main_inflow_sector"], "通信")
        self.assertEqual(payload["items"][0]["sector"], "通信")
        self.assertEqual(payload["items"][0]["stock_count"], 2)
        self.assertEqual(payload["items"][0]["above_ma10_count"], 2)
        self.assertAlmostEqual(payload["items"][0]["above_ma10_ratio"], 1.0, places=6)
        self.assertEqual(payload["items"][0]["meat_count"], 1)
        self.assertEqual(payload["items"][0]["limit_up_count"], 0)
        self.assertEqual(payload["items"][0]["limit_down_count"], 1)
        self.assertEqual(payload["items"][0]["yesterday_main_inflow"], 100000000.0)
```

- [ ] **Step 2: Write the failing test for empty / invalid industry rows**

Add this second test:

```python
    def test_build_qmt_industry_stats_payload_skips_blank_sector_rows(self) -> None:
        rows = [
            {
                "stock_code": "000001.SZ",
                "stock_name": "平安银行",
                "sector": "",
                "last_price": 10.0,
                "last_close": 9.8,
                "ma10": 9.5,
                "up_limit": 10.78,
                "down_limit": 8.82,
                "yesterday_main_inflow": 3000000.0,
            }
        ]

        payload = build_qmt_industry_stats_payload(rows)

        self.assertEqual(payload["summary"]["sector_count"], 0)
        self.assertEqual(payload["summary"]["top_meat_sector"], None)
        self.assertEqual(payload["summary"]["top_main_inflow_sector"], None)
        self.assertEqual(payload["items"], [])
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m unittest tests.test_qmt_helper_layers -v`

Expected: FAIL with `ImportError` or `AttributeError` because `build_qmt_industry_stats_payload` does not exist yet.

- [ ] **Step 4: Commit the failing tests**

```bash
git add tests/test_qmt_helper_layers.py
git commit -m "test: add failing tests for qmt industry stats helper"
```

### Task 2: Implement Backend Industry Stats Helper In `qmt_monitors.py`

**Files:**
- Modify: `d:\market-radar\fetcher\qmt_monitors.py`
- Test: `d:\market-radar\tests\test_qmt_helper_layers.py`

- [ ] **Step 1: Add a stock-level normalization helper**

Insert this helper near the existing monitor payload helpers:

```python
def normalize_industry_stats_row(row: dict) -> dict | None:
    sector = str(row.get("sector") or "").strip()
    if not sector:
        return None

    try:
        last_price = float(row.get("last_price"))
        last_close = float(row.get("last_close"))
        ma10 = float(row.get("ma10"))
        up_limit = float(row.get("up_limit"))
        down_limit = float(row.get("down_limit"))
    except (TypeError, ValueError):
        return None

    try:
        yesterday_main_inflow = float(row.get("yesterday_main_inflow") or 0.0)
    except (TypeError, ValueError):
        yesterday_main_inflow = 0.0

    change_pct = (last_price / last_close - 1) * 100 if last_close > 0 else None
    return {
        "stock_code": str(row.get("stock_code") or "").strip(),
        "stock_name": str(row.get("stock_name") or "").strip(),
        "sector": sector,
        "last_price": last_price,
        "last_close": last_close,
        "ma10": ma10,
        "up_limit": up_limit,
        "down_limit": down_limit,
        "change_pct": change_pct,
        "above_ma10": last_price > ma10,
        "is_meat": change_pct is not None and change_pct >= 5,
        "is_limit_up": last_price >= up_limit,
        "is_limit_down": last_price <= down_limit,
        "yesterday_main_inflow": yesterday_main_inflow,
    }
```

- [ ] **Step 2: Add the industry payload builder**

Insert this function below the helper:

```python
def build_qmt_industry_stats_payload(rows: list[dict]) -> dict:
    normalized_rows = []
    for row in rows:
        normalized = normalize_industry_stats_row(row)
        if normalized is not None:
            normalized_rows.append(normalized)

    grouped: dict[str, list[dict]] = {}
    for item in normalized_rows:
        grouped.setdefault(item["sector"], []).append(item)

    items = []
    for sector, group in grouped.items():
        stock_count = len(group)
        above_ma10_count = sum(1 for item in group if item["above_ma10"])
        meat_count = sum(1 for item in group if item["is_meat"])
        limit_up_count = sum(1 for item in group if item["is_limit_up"])
        limit_down_count = sum(1 for item in group if item["is_limit_down"])
        yesterday_main_inflow = sum(item["yesterday_main_inflow"] for item in group)
        items.append(
            {
                "sector": sector,
                "stock_count": stock_count,
                "above_ma10_count": above_ma10_count,
                "above_ma10_ratio": above_ma10_count / stock_count if stock_count else 0.0,
                "meat_count": meat_count,
                "limit_up_count": limit_up_count,
                "limit_down_count": limit_down_count,
                "yesterday_main_inflow": yesterday_main_inflow,
            }
        )

    items.sort(
        key=lambda item: (
            -item["yesterday_main_inflow"],
            -item["meat_count"],
            item["sector"],
        )
    )
    top_meat = max(items, key=lambda item: (item["meat_count"], item["sector"]), default=None)
    top_main_inflow = items[0] if items else None
    return {
        "summary": {
            "sector_count": len(items),
            "strong_ma10_sector_count": sum(1 for item in items if item["above_ma10_ratio"] > 0.5),
            "top_meat_sector": top_meat["sector"] if top_meat else None,
            "top_main_inflow_sector": top_main_inflow["sector"] if top_main_inflow else None,
        },
        "items": items,
    }
```

- [ ] **Step 3: Run the helper tests to verify they pass**

Run: `python -m unittest tests.test_qmt_helper_layers -v`

Expected: PASS

- [ ] **Step 4: Commit the helper implementation**

```bash
git add fetcher/qmt_monitors.py tests/test_qmt_helper_layers.py
git commit -m "feat: add qmt industry stats payload helper"
```

### Task 3: Add Full-Universe Industry Stats Source Builder

**Files:**
- Modify: `d:\market-radar\fetcher\qmt_monitors.py`
- Read: `d:\market-radar\fetcher\qmt_data_api.py`
- Read: `d:\market-radar\quant\loader.py`
- Optionally modify: `d:\market-radar\quant\loader.py`
- Test: `d:\market-radar\tests\test_qmt_helper_layers.py`

- [ ] **Step 1: Write the failing test for stock snapshot enrichment**

Add this test to `d:\market-radar\tests\test_qmt_helper_layers.py`:

```python
from unittest import mock
import pandas as pd

from fetcher.qmt_monitors import build_qmt_industry_stats_source_rows


class QmtIndustryStatsSourceTests(unittest.TestCase):
    def test_build_qmt_industry_stats_source_rows_enriches_qmt_rows(self) -> None:
        sample_df = pd.DataFrame(
            [
                {
                    "trade_date": "2026-06-25",
                    "name": "特发信息",
                    "industry_l1": "通信",
                    "pre_close": 24.77,
                    "inst_buy": 80000000.0,
                    "inst_sell": 20000000.0,
                    "close": 24.77,
                },
                {
                    "trade_date": "2026-06-26",
                    "name": "特发信息",
                    "industry_l1": "通信",
                    "pre_close": 24.77,
                    "inst_buy": 0.0,
                    "inst_sell": 0.0,
                    "close": 22.29,
                },
            ]
        )

        with mock.patch("fetcher.qmt_monitors.list_a_shares", return_value=["000070.SZ"]), mock.patch(
            "fetcher.qmt_monitors.get_full_tick_snapshot",
            return_value={"000070.SZ": {"last_price": 22.29, "last_close": 24.77}},
        ), mock.patch("fetcher.qmt_monitors.get_trading_data", return_value=sample_df), mock.patch(
            "fetcher.qmt_monitors.compute_down_limit",
            return_value=22.29,
        ), mock.patch("fetcher.qmt_monitors.compute_up_limit", return_value=27.25):
            rows = build_qmt_industry_stats_source_rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sector"], "通信")
        self.assertEqual(rows[0]["ma10"], 24.77)
        self.assertEqual(rows[0]["yesterday_main_inflow"], 60000000.0)
        self.assertEqual(rows[0]["up_limit"], 27.25)
        self.assertEqual(rows[0]["down_limit"], 22.29)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_qmt_helper_layers -v`

Expected: FAIL because `build_qmt_industry_stats_source_rows` and `compute_up_limit` are not wired yet.

- [ ] **Step 3: Add a limit-up helper if missing**

In `d:\market-radar\fetcher\xtquant_limit_down.py`, add this sibling function below `compute_down_limit()`:

```python
def compute_up_limit(code: str, name: str, last_close: float, trade_date: str) -> float | None:
    if last_close is None or last_close <= 0:
        return None

    norm_code = _normalize_code(code)
    norm_name = str(name or "")
    trade_day = str(trade_date or "").replace("-", "")

    is_st = "ST" in norm_name.upper()
    is_kcb = norm_code.startswith("sh68")
    is_cyb_new = trade_day > "20200823" and norm_code.startswith("sz3")
    is_bj = norm_code.startswith("bj")

    ratio = 1.1
    if is_st:
        ratio = 1.05
    elif is_kcb or is_cyb_new:
        ratio = 1.2
    elif is_bj:
        ratio = 1.3

    raw = float(last_close) * ratio
    if is_bj:
        return float(Decimal(str(round(raw, 6))).quantize(Decimal("0.01"), ROUND_HALF_UP))
    return round(raw, 2)
```

- [ ] **Step 4: Add the source-row builder**

In `d:\market-radar\fetcher\qmt_monitors.py`, add imports:

```python
from quant.loader import get_trading_data
from fetcher.qmt_data_api import get_full_tick_snapshot, list_a_shares
from fetcher.xtquant_limit_down import compute_down_limit, compute_up_limit
```

Then add this function:

```python
def build_qmt_industry_stats_source_rows(target_trade_date: str | None = None) -> list[dict]:
    rows: list[dict] = []
    stock_codes = list_a_shares()
    if not stock_codes:
        return rows

    ticks = get_full_tick_snapshot(stock_codes)
    trade_day = str(target_trade_date or "").replace("-", "")
    for stock_code in stock_codes:
        tick = ticks.get(stock_code) or {}
        last_price = tick.get("last_price")
        last_close = tick.get("last_close")
        if last_price in (None, 0) or last_close in (None, 0):
            continue

        local_df = get_trading_data(stock_code)
        if local_df is None or local_df.empty or len(local_df) < 2:
            continue

        tail = local_df.tail(10).copy()
        if "close" not in tail.columns:
            continue
        try:
            ma10 = float(tail["close"].astype(float).mean())
        except (TypeError, ValueError):
            continue

        latest_row = local_df.iloc[-1]
        prev_row = local_df.iloc[-2]
        stock_name = str(latest_row.get("name", "") or "").strip()
        sector = str(latest_row.get("industry_l1", "") or "").strip()
        up_limit = compute_up_limit(stock_code, stock_name, float(last_close), trade_day)
        down_limit = compute_down_limit(stock_code, stock_name, float(last_close), trade_day)
        if not sector or up_limit is None or down_limit is None:
            continue

        try:
            yesterday_main_inflow = float(prev_row.get("inst_buy", 0.0) or 0.0) - float(prev_row.get("inst_sell", 0.0) or 0.0)
        except (TypeError, ValueError):
            yesterday_main_inflow = 0.0

        rows.append(
            {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "sector": sector,
                "last_price": float(last_price),
                "last_close": float(last_close),
                "ma10": ma10,
                "up_limit": up_limit,
                "down_limit": down_limit,
                "yesterday_main_inflow": yesterday_main_inflow,
            }
        )
    return rows
```

- [ ] **Step 5: Run the helper tests again**

Run: `python -m unittest tests.test_qmt_helper_layers -v`

Expected: PASS

- [ ] **Step 6: Commit the source builder**

```bash
git add fetcher/qmt_monitors.py fetcher/xtquant_limit_down.py tests/test_qmt_helper_layers.py
git commit -m "feat: build qmt industry stats source rows"
```

### Task 4: Add `GET /api/qmt-industry-stats` In Flask

**Files:**
- Modify: `d:\market-radar\server.py`
- Modify: `d:\market-radar\tests\test_qmt_monitor_api.py`

- [ ] **Step 1: Write the failing API test**

Add this test to `d:\market-radar\tests\test_qmt_monitor_api.py`:

```python
    def test_api_qmt_industry_stats_returns_aggregated_payload(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(server, "_read_qmt_runtime_status", return_value={
            "enabled": True,
            "connected": True,
            "version": "xtquant",
        }), mock.patch.object(server, "_get_qmt_overview_breadth", return_value={
            "fetch_time": "2026-06-27 14:35:00",
            "source": "xtquant",
            "market": "TOTAL",
        }), mock.patch("fetcher.qmt_monitors.build_qmt_industry_stats_source_rows", return_value=[
            {
                "stock_code": "000070.SZ",
                "stock_name": "特发信息",
                "sector": "通信",
                "last_price": 22.29,
                "last_close": 24.77,
                "ma10": 21.50,
                "up_limit": 27.25,
                "down_limit": 22.29,
                "yesterday_main_inflow": 120000000.0,
            }
        ]), mock.patch("fetcher.qmt_monitors.build_qmt_industry_stats_payload", return_value={
            "summary": {
                "sector_count": 1,
                "strong_ma10_sector_count": 1,
                "top_meat_sector": "通信",
                "top_main_inflow_sector": "通信",
            },
            "items": [
                {
                    "sector": "通信",
                    "stock_count": 1,
                    "above_ma10_count": 1,
                    "above_ma10_ratio": 1.0,
                    "meat_count": 0,
                    "limit_up_count": 0,
                    "limit_down_count": 1,
                    "yesterday_main_inflow": 120000000.0,
                }
            ],
        }):
            response = client.get("/api/qmt-industry-stats")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["summary"]["sector_count"], 1)
        self.assertEqual(payload["summary"]["top_main_inflow_sector"], "通信")
        self.assertEqual(payload["items"][0]["yesterday_main_inflow"], 120000000.0)
```

- [ ] **Step 2: Run the API tests to verify they fail**

Run: `python -m unittest tests.test_qmt_monitor_api -v`

Expected: FAIL with 404 or missing route.

- [ ] **Step 3: Add the route**

Update the imports in `d:\market-radar\server.py`:

```python
from fetcher.qmt_monitors import (
    build_qmt_industry_draggers_payload,
    build_qmt_industry_stats_payload,
    build_qmt_industry_stats_source_rows,
    build_qmt_limit_down_monitor_payload,
)
```

Then add this route below `/api/qmt-industry-draggers`:

```python
@app.route("/api/qmt-industry-stats")
def api_qmt_industry_stats():
    try:
        status = _read_qmt_runtime_status()
        breadth = _get_qmt_overview_breadth(status)
        target_trade_date = _current_qmt_trade_date()
        rows = []
        if status.get("enabled") and status.get("connected"):
            rows = build_qmt_industry_stats_source_rows(target_trade_date)
        payload = build_qmt_industry_stats_payload(rows)
        payload["status"] = status
        payload["snapshot"] = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "xtquant",
            "market": breadth["market"] if breadth else None,
        }
        return _ok(payload)
    except Exception as exc:
        return _err(exc)
```

- [ ] **Step 4: Run the API tests to verify they pass**

Run: `python -m unittest tests.test_qmt_monitor_api -v`

Expected: PASS

- [ ] **Step 5: Commit the API route**

```bash
git add server.py tests/test_qmt_monitor_api.py
git commit -m "feat: add qmt industry stats api"
```

### Task 5: Add `行业统计榜` To `QmtDataPage.tsx`

**Files:**
- Modify: `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`

- [ ] **Step 1: Add the frontend type**

Insert this interface below `QmtIndustryDraggers`:

```tsx
interface QmtIndustryStats {
  status: {
    enabled: boolean;
    connected: boolean;
    version: string | null;
  };
  snapshot: {
    updated_at: string | null;
    source: string | null;
    market: string | null;
  };
  summary: {
    sector_count: number;
    strong_ma10_sector_count: number;
    top_meat_sector: string | null;
    top_main_inflow_sector: string | null;
  };
  items: Array<{
    sector: string;
    stock_count: number;
    above_ma10_count: number;
    above_ma10_ratio: number;
    meat_count: number;
    limit_up_count: number;
    limit_down_count: number;
    yesterday_main_inflow: number;
  }>;
}
```

- [ ] **Step 2: Add the new state and loader wiring**

Add state:

```tsx
const [industryStatsData, setIndustryStatsData] = useState<QmtIndustryStats | null>(null);
```

Update `fetchAll()`:

```tsx
const fetchAll = async (): Promise<[
  QmtOverview,
  QmtLimitDownMonitor,
  QmtIndustryDraggers,
  QmtIndustryStats
]> => (
  Promise.all([
    apiFetch<QmtOverview>('/api/qmt-overview'),
    apiFetch<QmtLimitDownMonitor>('/api/qmt-limit-down-monitor'),
    apiFetch<QmtIndustryDraggers>('/api/qmt-industry-draggers'),
    apiFetch<QmtIndustryStats>('/api/qmt-industry-stats'),
  ])
);
```

Update all `set...` blocks:

```tsx
setData(next[0]);
setMonitorData(next[1]);
setDraggerData(next[2]);
setIndustryStatsData(next[3]);
```

- [ ] **Step 3: Add frontend format helper**

Insert this helper near the existing formatters:

```tsx
const fmtPercent = (value: number | null | undefined): string => (
  value == null ? '--' : `${(value * 100).toFixed(1)}%`
);
```

- [ ] **Step 4: Render the new section**

Append this block below the current `行业拖累榜` card:

```tsx
<div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)] space-y-4">
  <div className="flex items-start justify-between gap-4">
    <div>
      <p className="text-sm font-semibold text-gray-900">行业统计榜</p>
      <p className="text-xs text-gray-400 mt-1">按全市场行业横截面统计 QMT 实时强弱与昨日主力净流入。</p>
    </div>
    <span className="text-xs text-gray-400">{industryStatsData?.summary.sector_count ?? 0} 个行业</span>
  </div>

  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
    {[
      { label: '行业数', value: `${industryStatsData?.summary.sector_count ?? 0}` },
      { label: 'MA10强势行业', value: `${industryStatsData?.summary.strong_ma10_sector_count ?? 0}` },
      { label: '最大大肉行业', value: industryStatsData?.summary.top_meat_sector ?? '--' },
      { label: '主力净流入第一行业', value: industryStatsData?.summary.top_main_inflow_sector ?? '--' },
    ].map((item) => (
      <div key={item.label} className="rounded-xl border border-gray-100 p-4">
        <div className="text-xs text-gray-400">{item.label}</div>
        <div className="mt-2 text-2xl font-semibold text-gray-900">{item.value}</div>
      </div>
    ))}
  </div>

  <div className="grid grid-cols-7 px-4 py-2 text-xs text-gray-400 bg-gray-50 border border-gray-100 rounded-t-xl">
    <span>行业</span>
    <span className="text-center">个股数</span>
    <span className="text-center">10日线上占比</span>
    <span className="text-center">大肉数</span>
    <span className="text-center">涨停数</span>
    <span className="text-center">跌停数</span>
    <span className="text-center">昨日主力净流入</span>
  </div>
  {(industryStatsData?.items.length ?? 0) === 0 ? (
    <div className="px-4 py-8 text-sm text-center text-gray-400 border-x border-b border-gray-100 rounded-b-xl">
      暂无行业统计数据
    </div>
  ) : (
    <div className="divide-y divide-gray-50 border-x border-b border-gray-100 rounded-b-xl">
      {industryStatsData!.items.map((row) => (
        <div key={row.sector} className="grid grid-cols-7 px-4 py-3 text-xs text-gray-700 hover:bg-gray-50">
          <span>{row.sector}</span>
          <span className="text-center">{row.stock_count}</span>
          <span className="text-center">{fmtPercent(row.above_ma10_ratio)}</span>
          <span className="text-center">{row.meat_count}</span>
          <span className="text-center">{row.limit_up_count}</span>
          <span className="text-center">{row.limit_down_count}</span>
          <span className="text-center">{fmtAmountYi(row.yesterday_main_inflow)}</span>
        </div>
      ))}
    </div>
  )}
</div>
```

- [ ] **Step 5: Run the frontend build**

Run: `npm run build`

Working directory: `d:\market-radar\dashboard`

Expected: PASS

- [ ] **Step 6: Commit the frontend section**

```bash
git add dashboard/src/pages/QmtDataPage.tsx
git commit -m "feat: add qmt industry stats section"
```

### Task 6: Final Validation

**Files:**
- Test: `d:\market-radar\tests\test_qmt_helper_layers.py`
- Test: `d:\market-radar\tests\test_qmt_monitor_api.py`
- Check: `d:\market-radar\fetcher\qmt_monitors.py`
- Check: `d:\market-radar\server.py`
- Check: `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`

- [ ] **Step 1: Run focused backend tests**

Run: `python -m unittest tests.test_qmt_helper_layers tests.test_qmt_monitor_api tests.test_dt_pool_v3 -v`

Expected: PASS

- [ ] **Step 2: Run Python syntax verification**

Run: `python -m py_compile fetcher/qmt_monitors.py fetcher/xtquant_limit_down.py server.py tests/test_qmt_helper_layers.py tests/test_qmt_monitor_api.py`

Expected: no output

- [ ] **Step 3: Run the frontend build**

Run: `npm run build`

Working directory: `d:\market-radar\dashboard`

Expected: PASS

- [ ] **Step 4: Check diagnostics**

Check:

- `file:///d:/market-radar/fetcher/qmt_monitors.py`
- `file:///d:/market-radar/server.py`
- `file:///d:/market-radar/dashboard/src/pages/QmtDataPage.tsx`

Expected: no new diagnostics in touched files.

- [ ] **Step 5: Manual runtime verification**

Verify all of these:

- `QMT数据` 页面仍能打开
- `行业拖累榜` 仍正常显示，不被替换
- 新的 `行业统计榜` 显示在 `行业拖累榜` 下方
- `行业统计榜` 表头包含：
  - 行业
  - 个股数
  - 10日线上占比
  - 大肉数
  - 涨停数
  - 跌停数
  - 昨日主力净流入
- `/api/qmt-industry-stats` 返回 `summary.sector_count > 0`
- 第一行行业与 `summary.top_main_inflow_sector` 一致
- `above_ma10_ratio` 在页面显示为百分比
- `yesterday_main_inflow` 在页面显示为 `亿`
- QMT 断开时该区块显示空状态而不是报错

- [ ] **Step 6: Commit final validation state**

```bash
git add fetcher/qmt_monitors.py fetcher/xtquant_limit_down.py server.py dashboard/src/pages/QmtDataPage.tsx tests/test_qmt_helper_layers.py tests/test_qmt_monitor_api.py
git commit -m "feat: add qmt industry stats"
```

## Self-Review

- Spec coverage checked:
  - new endpoint covered in Task 4
  - new backend helper layer covered in Tasks 2 and 3
  - frontend section covered in Task 5
  - validation and empty-state checks covered in Task 6
- Placeholder scan passed:
  - no `TBD`
  - no “handle appropriately” placeholders
  - every command is explicit
- Type consistency checked:
  - `QmtIndustryStats` keys match the backend payload keys
  - summary keys are reused consistently across helper, route, and page tasks
