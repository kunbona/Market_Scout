# QMT Monitor Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing `QMT数据` page with a `跌停监控中心` and an `行业拖累榜`, powered by two new backend endpoints that reuse the current QMT status logic, latest breadth snapshot, and `dt_pool_v3` rows.

**Architecture:** Keep the current `GET /api/qmt-overview` endpoint as the lightweight overview payload and add two focused endpoints for the two new modules. Put the row shaping and aggregation logic in `fetcher/sector_heat.py` so `server.py` stays thin and both endpoints derive from one consistent limit-down item set. Extend `QmtDataPage.tsx` to load the three payloads in parallel, render three sections, and apply only client-side keyword and filter interactions.

**Tech Stack:** Python, Flask, SQLite, React, TypeScript, Vite, unittest

---

## File Map

- `d:\market-radar\fetcher\sector_heat.py`
  - Add monitor shaping helpers over `dt_pool_v3` rows.
  - Derive `is_st`, `tags`, `limit_gap_pct`, monitor summary, industry distribution, and industry dragger rows.

- `d:\market-radar\server.py`
  - Add `GET /api/qmt-limit-down-monitor`.
  - Add `GET /api/qmt-industry-draggers`.
  - Reuse `_read_qmt_runtime_status()`, `_latest_market_breadth_row()`, and `get_dt_pool_v3(None)`.

- `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`
  - Extend the page from one overview section to three sections.
  - Add typed fetches for overview, monitor, and draggers.
  - Add lightweight keyword / ST / industry filters for the monitor table.

- `d:\market-radar\tests\test_qmt_monitor_helpers.py`
  - Add focused unit tests for monitor row shaping and industry aggregation.

- `d:\market-radar\tests\test_qmt_monitor_api.py`
  - Add focused API tests for the two new endpoints.

### Task 1: Add Monitor Helper Functions In `sector_heat.py`

**Files:**
- Modify: `d:\market-radar\fetcher\sector_heat.py`
- Test: `d:\market-radar\tests\test_qmt_monitor_helpers.py`

- [ ] **Step 1: Write the failing helper tests**

Create `d:\market-radar\tests\test_qmt_monitor_helpers.py`:

```python
import unittest

from fetcher import sector_heat


class QmtMonitorHelperTests(unittest.TestCase):
    def test_build_qmt_limit_down_monitor_shapes_rows_and_summary(self) -> None:
        rows = [
            {
                "stock_code": "000016.SZ",
                "stock_name": "*ST康佳A",
                "last_price": 4.15,
                "last_close": 4.37,
                "down_limit": 4.15,
                "sector": "家用电器",
            },
            {
                "stock_code": "603022.SH",
                "stock_name": "新通联",
                "last_price": 11.06,
                "last_close": 12.29,
                "down_limit": 11.06,
                "sector": "包装印刷",
            },
        ]

        payload = sector_heat.build_qmt_limit_down_monitor_payload(rows)

        self.assertEqual(payload["summary"]["total_count"], 2)
        self.assertEqual(payload["summary"]["st_count"], 1)
        self.assertEqual(payload["summary"]["non_st_count"], 1)
        self.assertEqual(payload["summary"]["industry_count"], 2)
        self.assertEqual(payload["industry_distribution"][0]["count"], 1)
        self.assertEqual(payload["items"][0]["is_st"], True)
        self.assertEqual(payload["items"][0]["tags"], ["ST", "跌停"])
        self.assertAlmostEqual(payload["items"][0]["limit_gap_pct"], 0.0, places=6)

    def test_build_qmt_industry_draggers_aggregates_from_same_rows(self) -> None:
        rows = [
            {
                "stock_code": "000016.SZ",
                "stock_name": "*ST康佳A",
                "last_price": 4.15,
                "last_close": 4.37,
                "down_limit": 4.15,
                "sector": "家用电器",
            },
            {
                "stock_code": "000017.SZ",
                "stock_name": "深中华A",
                "last_price": 2.50,
                "last_close": 2.78,
                "down_limit": 2.50,
                "sector": "家用电器",
            },
            {
                "stock_code": "603022.SH",
                "stock_name": "新通联",
                "last_price": 11.06,
                "last_close": 12.29,
                "down_limit": 11.06,
                "sector": "包装印刷",
            },
        ]

        payload = sector_heat.build_qmt_industry_draggers_payload(rows)

        self.assertEqual(payload["summary"]["sector_count"], 2)
        self.assertEqual(payload["summary"]["top_dragger_sector"], "家用电器")
        self.assertEqual(payload["summary"]["top_dragger_limit_down_count"], 2)
        self.assertEqual(payload["items"][0]["sector"], "家用电器")
        self.assertEqual(payload["items"][0]["limit_down_count"], 2)
        self.assertEqual(payload["items"][0]["st_count"], 1)
        self.assertEqual(payload["items"][0]["non_st_count"], 1)
        self.assertEqual(payload["items"][0]["lead_stock_code"], "000016.SZ")
        self.assertAlmostEqual(payload["items"][0]["drag_score"], 2.2, places=6)

    def test_build_qmt_monitor_payload_handles_empty_rows(self) -> None:
        monitor = sector_heat.build_qmt_limit_down_monitor_payload([])
        draggers = sector_heat.build_qmt_industry_draggers_payload([])

        self.assertEqual(monitor["summary"]["total_count"], 0)
        self.assertEqual(monitor["industry_distribution"], [])
        self.assertEqual(monitor["items"], [])
        self.assertEqual(draggers["summary"]["sector_count"], 0)
        self.assertEqual(draggers["summary"]["top_dragger_sector"], None)
        self.assertEqual(draggers["items"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_qmt_monitor_helpers -v`

Expected: FAIL with `AttributeError` because `build_qmt_limit_down_monitor_payload` and `build_qmt_industry_draggers_payload` do not exist yet.

- [ ] **Step 3: Add the minimal helper implementation**

In `d:\market-radar\fetcher\sector_heat.py`, add these helper functions near `_load_local_stock_meta()` and before the fetch functions:

```python
def _is_st_stock_name(stock_name: str) -> bool:
    return "ST" in str(stock_name or "").upper()


def _normalize_monitor_row(row: dict) -> dict:
    stock_name = str(row.get("stock_name") or "").strip()
    sector = str(row.get("sector") or "").strip()
    last_price = row.get("last_price")
    last_close = row.get("last_close")
    down_limit = row.get("down_limit")
    try:
        last_price = float(last_price) if last_price is not None else None
    except (TypeError, ValueError):
        last_price = None
    try:
        last_close = float(last_close) if last_close is not None else None
    except (TypeError, ValueError):
        last_close = None
    try:
        down_limit = float(down_limit) if down_limit is not None else None
    except (TypeError, ValueError):
        down_limit = None

    is_st = _is_st_stock_name(stock_name)
    limit_gap_pct = None
    if last_price is not None and down_limit not in (None, 0):
        limit_gap_pct = (last_price / down_limit - 1) * 100

    tags = ["ST" if is_st else "非ST", "跌停"]
    return {
        "stock_code": str(row.get("stock_code") or "").strip(),
        "stock_name": stock_name,
        "sector": sector,
        "last_price": last_price,
        "last_close": last_close,
        "down_limit": down_limit,
        "limit_gap_pct": limit_gap_pct,
        "is_st": is_st,
        "tags": tags,
    }


def build_qmt_limit_down_monitor_payload(rows: list[dict]) -> dict:
    items = [_normalize_monitor_row(row) for row in rows]
    sector_counts: dict[str, int] = {}
    for item in items:
        sector = item["sector"]
        if not sector:
            continue
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    industry_distribution = [
        {"sector": sector, "count": count}
        for sector, count in sorted(sector_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    st_count = sum(1 for item in items if item["is_st"])
    return {
        "summary": {
            "total_count": len(items),
            "st_count": st_count,
            "non_st_count": len(items) - st_count,
            "industry_count": len(industry_distribution),
        },
        "industry_distribution": industry_distribution,
        "items": items,
    }


def build_qmt_industry_draggers_payload(rows: list[dict]) -> dict:
    items = [_normalize_monitor_row(row) for row in rows]
    grouped: dict[str, list[dict]] = {}
    for item in items:
        sector = item["sector"] or ""
        grouped.setdefault(sector, []).append(item)

    result_items = []
    for sector, group in grouped.items():
        st_count = sum(1 for item in group if item["is_st"])
        limit_down_count = len(group)
        lead = group[0]
        result_items.append({
            "sector": sector,
            "limit_down_count": limit_down_count,
            "st_count": st_count,
            "non_st_count": limit_down_count - st_count,
            "lead_stock_code": lead["stock_code"],
            "lead_stock_name": lead["stock_name"],
            "drag_score": limit_down_count + st_count * 0.2,
        })

    result_items.sort(key=lambda item: (-item["limit_down_count"], -item["st_count"], item["sector"]))
    top = result_items[0] if result_items else None
    return {
        "summary": {
            "sector_count": len(result_items),
            "top_dragger_sector": top["sector"] if top else None,
            "top_dragger_limit_down_count": top["limit_down_count"] if top else 0,
        },
        "items": result_items,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest tests.test_qmt_monitor_helpers -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fetcher/sector_heat.py tests/test_qmt_monitor_helpers.py
git commit -m "feat: add qmt monitor payload helpers"
```

### Task 2: Add Two Focused QMT Monitor API Endpoints

**Files:**
- Modify: `d:\market-radar\server.py`
- Test: `d:\market-radar\tests\test_qmt_monitor_api.py`

- [ ] **Step 1: Write the failing API tests**

Create `d:\market-radar\tests\test_qmt_monitor_api.py`:

```python
import unittest
from unittest import mock

import server


class QmtMonitorApiTests(unittest.TestCase):
    def test_api_qmt_limit_down_monitor_returns_stable_payload(self) -> None:
        client = server.app.test_client()
        rows = [
            {
                "stock_code": "000016.SZ",
                "stock_name": "*ST康佳A",
                "last_price": 4.15,
                "last_close": 4.37,
                "down_limit": 4.15,
                "sector": "家用电器",
            }
        ]
        with mock.patch.object(server, "_read_qmt_runtime_status", return_value={
            "enabled": True,
            "connected": True,
            "version": "1.0.0",
        }), mock.patch.object(server, "_latest_market_breadth_row", return_value={
            "fetch_time": "2026-06-24 14:35:00",
            "source": "QMT",
            "market": "沪深A股",
        }), mock.patch.object(server, "get_dt_pool_v3", return_value=rows, create=True):
            response = client.get("/api/qmt-limit-down-monitor")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["summary"]["total_count"], 1)
        self.assertEqual(payload["summary"]["st_count"], 1)
        self.assertEqual(payload["items"][0]["tags"], ["ST", "跌停"])

    def test_api_qmt_industry_draggers_returns_aggregated_payload(self) -> None:
        client = server.app.test_client()
        rows = [
            {
                "stock_code": "000016.SZ",
                "stock_name": "*ST康佳A",
                "last_price": 4.15,
                "last_close": 4.37,
                "down_limit": 4.15,
                "sector": "家用电器",
            },
            {
                "stock_code": "000017.SZ",
                "stock_name": "深中华A",
                "last_price": 2.50,
                "last_close": 2.78,
                "down_limit": 2.50,
                "sector": "家用电器",
            },
        ]
        with mock.patch.object(server, "_read_qmt_runtime_status", return_value={
            "enabled": True,
            "connected": True,
            "version": "1.0.0",
        }), mock.patch.object(server, "_latest_market_breadth_row", return_value={
            "fetch_time": "2026-06-24 14:35:00",
            "source": "QMT",
            "market": "沪深A股",
        }), mock.patch.object(server, "get_dt_pool_v3", return_value=rows, create=True):
            response = client.get("/api/qmt-industry-draggers")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["summary"]["sector_count"], 1)
        self.assertEqual(payload["summary"]["top_dragger_sector"], "家用电器")
        self.assertEqual(payload["items"][0]["drag_score"], 2.2)

    def test_api_qmt_monitor_endpoints_survive_empty_rows(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(server, "_read_qmt_runtime_status", return_value={
            "enabled": True,
            "connected": False,
            "version": "1.0.0",
        }), mock.patch.object(server, "_latest_market_breadth_row", return_value=None), mock.patch.object(
            server, "get_dt_pool_v3", return_value=[], create=True
        ):
            monitor_response = client.get("/api/qmt-limit-down-monitor")
            dragger_response = client.get("/api/qmt-industry-draggers")

        self.assertEqual(monitor_response.status_code, 200)
        self.assertEqual(dragger_response.status_code, 200)
        self.assertEqual(monitor_response.get_json()["data"]["items"], [])
        self.assertEqual(dragger_response.get_json()["data"]["items"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest tests.test_qmt_monitor_api -v`

Expected: FAIL with missing routes `/api/qmt-limit-down-monitor` and `/api/qmt-industry-draggers`.

- [ ] **Step 3: Add the minimal routes**

Update the `server.py` imports:

```python
from fetcher.sector_heat import (
    build_qmt_industry_draggers_payload,
    build_qmt_limit_down_monitor_payload,
)
```

Add the two routes below `/api/qmt-overview`:

```python
@app.route("/api/qmt-limit-down-monitor")
def api_qmt_limit_down_monitor():
    try:
        status = _read_qmt_runtime_status()
        breadth = _latest_market_breadth_row()
        rows = get_dt_pool_v3(None)
        payload = build_qmt_limit_down_monitor_payload(rows)
        payload["status"] = status
        payload["snapshot"] = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "QMT",
            "market": breadth["market"] if breadth else None,
        }
        return _ok(payload)
    except Exception as exc:
        return _err(exc)


@app.route("/api/qmt-industry-draggers")
def api_qmt_industry_draggers():
    try:
        status = _read_qmt_runtime_status()
        breadth = _latest_market_breadth_row()
        rows = get_dt_pool_v3(None)
        payload = build_qmt_industry_draggers_payload(rows)
        payload["status"] = status
        payload["snapshot"] = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "QMT",
            "market": breadth["market"] if breadth else None,
        }
        return _ok(payload)
    except Exception as exc:
        return _err(exc)
```

- [ ] **Step 4: Run the helper and API tests to verify they pass together**

Run: `python -m unittest tests.test_qmt_monitor_helpers tests.test_qmt_monitor_api -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_qmt_monitor_api.py tests/test_qmt_monitor_helpers.py fetcher/sector_heat.py
git commit -m "feat: add qmt monitor api endpoints"
```

### Task 3: Extend `QmtDataPage.tsx` With Two New Sections

**Files:**
- Modify: `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`

- [ ] **Step 1: Extend the page types and state**

In `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`, add the new interfaces above `export function QmtDataPage()`:

```tsx
interface QmtLimitDownMonitor {
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
    total_count: number;
    st_count: number;
    non_st_count: number;
    industry_count: number;
  };
  industry_distribution: Array<{
    sector: string;
    count: number;
  }>;
  items: Array<{
    stock_code: string;
    stock_name: string;
    sector: string;
    last_price: number | null;
    last_close: number | null;
    down_limit: number | null;
    limit_gap_pct: number | null;
    is_st: boolean;
    tags: string[];
  }>;
}

interface QmtIndustryDraggers {
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
    top_dragger_sector: string | null;
    top_dragger_limit_down_count: number;
  };
  items: Array<{
    sector: string;
    limit_down_count: number;
    st_count: number;
    non_st_count: number;
    lead_stock_code: string;
    lead_stock_name: string;
    drag_score: number;
  }>;
}
```

Add new state inside the component:

```tsx
const [monitorData, setMonitorData] = useState<QmtLimitDownMonitor | null>(null);
const [draggerData, setDraggerData] = useState<QmtIndustryDraggers | null>(null);
const [keyword, setKeyword] = useState<string>('');
const [stOnly, setStOnly] = useState<boolean>(false);
const [selectedSector, setSelectedSector] = useState<string>('all');
```

- [ ] **Step 2: Replace the single fetch with a three-endpoint loader**

Replace the existing `fetchOverview` helper and loading code with:

```tsx
const fetchAll = async (): Promise<[
  QmtOverview,
  QmtLimitDownMonitor,
  QmtIndustryDraggers
]> =>
  Promise.all([
    apiFetch<QmtOverview>('/api/qmt-overview'),
    apiFetch<QmtLimitDownMonitor>('/api/qmt-limit-down-monitor'),
    apiFetch<QmtIndustryDraggers>('/api/qmt-industry-draggers'),
  ]);
```

Update both the initial load and manual refresh to set all three payloads:

```tsx
const next = await fetchAll();
if (!cancelled) {
  setData(next[0]);
  setMonitorData(next[1]);
  setDraggerData(next[2]);
  setError('');
}
```

Inside `handleRefresh()`:

```tsx
const next = await fetchAll();
setData(next[0]);
setMonitorData(next[1]);
setDraggerData(next[2]);
```

- [ ] **Step 3: Add memoized filtered rows and sector options**

Add these memos below `const focusList = data?.focus_list ?? [];`:

```tsx
const sectorOptions = useMemo((): string[] => {
  const sectors = new Set(
    (monitorData?.items ?? [])
      .map((row) => row.sector)
      .filter((sector) => Boolean(sector)),
  );
  return ['all', ...Array.from(sectors).sort((a, b) => a.localeCompare(b, 'zh-CN'))];
}, [monitorData]);

const filteredMonitorRows = useMemo(() => {
  const normalizedKeyword = keyword.trim().toLowerCase();
  return (monitorData?.items ?? []).filter((row) => {
    if (stOnly && !row.is_st) {
      return false;
    }
    if (selectedSector !== 'all' && row.sector !== selectedSector) {
      return false;
    }
    if (!normalizedKeyword) {
      return true;
    }
    return (
      row.stock_code.toLowerCase().includes(normalizedKeyword) ||
      row.stock_name.toLowerCase().includes(normalizedKeyword)
    );
  });
}, [keyword, monitorData, selectedSector, stOnly]);
```

- [ ] **Step 4: Render the new `跌停监控中心` and `行业拖累榜` sections**

Append these two sections below the existing QMT 跌停明细 card:

```tsx
<div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)] space-y-4">
  <div className="flex items-start justify-between gap-4">
    <div>
      <p className="text-sm font-semibold text-gray-900">跌停监控中心</p>
      <p className="text-xs text-gray-400 mt-1">从 `dt_pool_v3` 派生的当前跌停监控视图。</p>
    </div>
    <span className="text-xs text-gray-400">{monitorData?.summary.total_count ?? 0} 条</span>
  </div>

  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
    {[
      { label: '跌停总数', value: `${monitorData?.summary.total_count ?? 0}` },
      { label: 'ST跌停数', value: `${monitorData?.summary.st_count ?? 0}` },
      { label: '非ST跌停数', value: `${monitorData?.summary.non_st_count ?? 0}` },
      { label: '涉及行业数', value: `${monitorData?.summary.industry_count ?? 0}` },
    ].map((item) => (
      <div key={item.label} className="rounded-xl border border-gray-100 p-4">
        <div className="text-xs text-gray-400">{item.label}</div>
        <div className="mt-2 text-2xl font-semibold text-gray-900">{item.value}</div>
      </div>
    ))}
  </div>

  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
    <input
      value={keyword}
      onChange={(event) => setKeyword(event.target.value)}
      placeholder="按代码或名称搜索"
      className="px-3 py-2 text-sm border border-gray-200 rounded-lg"
    />
    <select
      value={selectedSector}
      onChange={(event) => setSelectedSector(event.target.value)}
      className="px-3 py-2 text-sm border border-gray-200 rounded-lg"
    >
      {sectorOptions.map((sector) => (
        <option key={sector} value={sector}>
          {sector === 'all' ? '全部行业' : sector}
        </option>
      ))}
    </select>
    <label className="inline-flex items-center gap-2 px-3 py-2 text-sm border border-gray-200 rounded-lg">
      <input
        type="checkbox"
        checked={stOnly}
        onChange={(event) => setStOnly(event.target.checked)}
      />
      仅看 ST
    </label>
  </div>

  <div className="grid grid-cols-7 px-4 py-2 text-xs text-gray-400 bg-gray-50 border border-gray-100 rounded-t-xl">
    <span>代码</span>
    <span>名称</span>
    <span className="text-center">行业</span>
    <span className="text-center">最新价</span>
    <span className="text-center">昨收</span>
    <span className="text-center">跌停价</span>
    <span className="text-center">标签</span>
  </div>
  {filteredMonitorRows.length === 0 ? (
    <div className="px-4 py-8 text-sm text-center text-gray-400 border-x border-b border-gray-100 rounded-b-xl">
      暂无符合条件的跌停监控数据
    </div>
  ) : (
    <div className="divide-y divide-gray-50 border-x border-b border-gray-100 rounded-b-xl">
      {filteredMonitorRows.map((row) => (
        <div key={row.stock_code} className="grid grid-cols-7 px-4 py-3 text-xs text-gray-700 hover:bg-gray-50">
          <span>{row.stock_code}</span>
          <span>{row.stock_name || '--'}</span>
          <span className="text-center">{row.sector || '--'}</span>
          <span className="text-center">{fmtPrice(row.last_price)}</span>
          <span className="text-center">{fmtPrice(row.last_close)}</span>
          <span className="text-center text-teal-600 font-medium">{fmtPrice(row.down_limit)}</span>
          <span className="text-center">{row.tags.join(' / ')}</span>
        </div>
      ))}
    </div>
  )}
</div>

<div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)] space-y-4">
  <div className="flex items-start justify-between gap-4">
    <div>
      <p className="text-sm font-semibold text-gray-900">行业拖累榜</p>
      <p className="text-xs text-gray-400 mt-1">按当前跌停集合统计的行业拖累强度。</p>
    </div>
    <span className="text-xs text-gray-400">{draggerData?.summary.sector_count ?? 0} 个行业</span>
  </div>

  <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
    {[
      { label: '拖累行业数', value: `${draggerData?.summary.sector_count ?? 0}` },
      { label: '最弱行业', value: draggerData?.summary.top_dragger_sector ?? '--' },
      { label: '最大行业跌停数', value: `${draggerData?.summary.top_dragger_limit_down_count ?? 0}` },
    ].map((item) => (
      <div key={item.label} className="rounded-xl border border-gray-100 p-4">
        <div className="text-xs text-gray-400">{item.label}</div>
        <div className="mt-2 text-2xl font-semibold text-gray-900">{item.value}</div>
      </div>
    ))}
  </div>

  <div className="grid grid-cols-6 px-4 py-2 text-xs text-gray-400 bg-gray-50 border border-gray-100 rounded-t-xl">
    <span>行业</span>
    <span className="text-center">跌停数</span>
    <span className="text-center">ST数</span>
    <span className="text-center">非ST数</span>
    <span className="text-center">代表个股</span>
    <span className="text-center">拖累分</span>
  </div>
  {(draggerData?.items.length ?? 0) === 0 ? (
    <div className="px-4 py-8 text-sm text-center text-gray-400 border-x border-b border-gray-100 rounded-b-xl">
      暂无行业拖累数据
    </div>
  ) : (
    <div className="divide-y divide-gray-50 border-x border-b border-gray-100 rounded-b-xl">
      {draggerData!.items.map((row) => (
        <div key={row.sector || row.lead_stock_code} className="grid grid-cols-6 px-4 py-3 text-xs text-gray-700 hover:bg-gray-50">
          <span>{row.sector || '--'}</span>
          <span className="text-center">{row.limit_down_count}</span>
          <span className="text-center">{row.st_count}</span>
          <span className="text-center">{row.non_st_count}</span>
          <span className="text-center">{row.lead_stock_name || row.lead_stock_code || '--'}</span>
          <span className="text-center">{row.drag_score.toFixed(1)}</span>
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

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/pages/QmtDataPage.tsx
git commit -m "feat: add qmt monitor sections"
```

### Task 4: Run Final Validation And Diagnostics

**Files:**
- Test: `d:\market-radar\tests\test_qmt_monitor_helpers.py`
- Test: `d:\market-radar\tests\test_qmt_monitor_api.py`
- Check: `d:\market-radar\fetcher\sector_heat.py`
- Check: `d:\market-radar\server.py`
- Check: `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`

- [ ] **Step 1: Run focused backend tests**

Run: `python -m unittest tests.test_qmt_monitor_helpers tests.test_qmt_monitor_api tests.test_dt_pool_v3 -v`

Expected: PASS

- [ ] **Step 2: Run Python syntax verification**

Run: `python -m py_compile fetcher/sector_heat.py server.py tests/test_qmt_monitor_helpers.py tests/test_qmt_monitor_api.py`

Expected: no output

- [ ] **Step 3: Run frontend build**

Run: `npm run build`

Working directory: `d:\market-radar\dashboard`

Expected: PASS

- [ ] **Step 4: Check diagnostics**

Check these files:

- `file:///d:/market-radar/fetcher/sector_heat.py`
- `file:///d:/market-radar/server.py`
- `file:///d:/market-radar/dashboard/src/pages/QmtDataPage.tsx`

Expected: no new diagnostics in touched files.

- [ ] **Step 5: Manual runtime verification**

Start the app and verify:

- `QMT数据` 页面仍能打开
- 顶部总览区仍显示 QMT 运行状态
- `跌停监控中心` 正常显示 4 个 KPI 卡片
- `行业拖累榜` 正常显示 3 个 KPI 卡片
- 搜索框、行业筛选、`仅看 ST` 复选框生效
- `跌停监控中心` 的 `跌停总数` 等于 `/api/qmt-limit-down-monitor` 的 `summary.total_count`
- `行业拖累榜` 第一行行业等于 `/api/qmt-industry-draggers` 的 `summary.top_dragger_sector`
- QMT 未连接时两个新模块也不会崩溃，只显示空状态

- [ ] **Step 6: Commit**

```bash
git add fetcher/sector_heat.py server.py dashboard/src/pages/QmtDataPage.tsx tests/test_qmt_monitor_helpers.py tests/test_qmt_monitor_api.py
git commit -m "feat: add qmt monitor suite"
```
