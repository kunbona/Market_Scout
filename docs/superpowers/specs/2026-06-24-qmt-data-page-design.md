# QMT Data Page Design

**Goal**

Add a new top-level dashboard page `QMT数据` that gives a focused, trustworthy overview of data coming from miniQMT.

The first version is intentionally small:

- QMT enable / connection status
- latest available QMT snapshot time
- core KPI cards
- one focused list built from the existing QMT limit-down experiment

**Why**

The project already uses QMT in multiple places, but the user cannot inspect the QMT data path from one dedicated page:

- QMT status currently lives inside the settings page
- `market_breadth` is stored and used, but not exposed as a dedicated QMT view
- `dt_pool_v3` already proves that QMT can provide an independent realtime跌停口径

This page exists to answer a practical question quickly: "What QMT data do we have right now, and does it look healthy?"

## Recommended Approach

Use a lightweight reuse-first design:

1. Add one new top-level sidebar entry: `QMT数据`
2. Add one dedicated page component on the frontend
3. Add one backend aggregation endpoint for the page
4. Reuse existing QMT status detection, `market_breadth` data, and `dt_pool_v3`

This avoids heavy new realtime logic in the first version and keeps the page aligned with the current project structure.

## Scope

First version includes:

- top-level sidebar page entry
- dedicated `QMT数据` page
- one backend endpoint for page consumption
- QMT status panel
- latest snapshot information
- core KPI cards
- focused table based on `dt_pool_v3`

First version does not include:

- raw full-market tick tables
- per-stock search across all QMT symbols
- new heavy polling or continuous backend streaming
- replacing existing market page content
- new storage tables in the first version

## User Experience

The page is a new top-level destination, not a sub-tab under `市场数据`.

Rationale:

- the user explicitly wants "专门再增加一栏"
- QMT is both a data source and a health-check surface
- a top-level entry is easier to discover and expand later

The page layout should have three sections:

### 1. Status Summary

Show:

- whether QMT is enabled
- whether miniQMT is connected
- xtquant version if available
- a short message for current state
- a link or cue that users can go to `设置` for configuration changes

### 2. KPI Overview

Show a compact KPI row with:

- `QMT跌停数`
- `最新快照时间`
- `上涨家数`
- `下跌家数`
- `平盘家数`
- `全市场成交额`

The KPI area should emphasize "latest available snapshot" semantics rather than pretending to be tick-by-tick live.

### 3. Focus List

Show the current `dt_pool_v3` rows as the first focused QMT table.

Suggested columns:

- stock code
- stock name
- latest price
- last close
- down-limit price
- sector

This makes the page immediately useful while staying within already verified data paths.

## Data Sources

### QMT Status

Reuse the same QMT status logic currently exposed via `/api/config`:

- `qmt_enabled`
- `qmt_connected`
- `qmt_version`

The new overview endpoint may call the same helper logic directly or reuse the same server-side logic path, but it should not duplicate inconsistent rules.

### Snapshot Metrics

Reuse the latest `market_breadth` snapshot for:

- snapshot timestamp
- `up_count`
- `down_count`
- `flat_count`
- `turnover`

The page should present these as "latest sampled QMT breadth metrics".

### Focus List Metrics

Reuse `dt_pool_v3` for:

- focus list rows
- limit-down count

`QMT跌停数` must equal the current number of rows returned for `dt_pool_v3`.

## Backend API

Add one aggregation endpoint:

- `GET /api/qmt-overview`

Suggested response shape:

```json
{
  "status": {
    "enabled": true,
    "connected": true,
    "version": "x.y.z"
  },
  "snapshot": {
    "updated_at": "2026-06-24 14:35:00",
    "source": "QMT"
  },
  "kpis": {
    "limit_down_count": 12,
    "up_count": 1234,
    "down_count": 3456,
    "flat_count": 78,
    "turnover": 1234567890
  },
  "focus_list": [
    {
      "stock_code": "sz000001",
      "stock_name": "示例",
      "last_price": 10.5,
      "last_close": 11.67,
      "down_limit": 10.5,
      "sector": ""
    }
  ]
}
```

Rules:

- never raise a 500 just because QMT is disconnected
- return a stable shape with empty or null data where appropriate
- make the frontend responsible only for rendering, not multi-endpoint stitching

## Empty State and Error Handling

The page must stay renderable in all cases.

### Case 1. QMT disabled

Show:

- disabled status badge
- explanatory text telling the user to enable QMT in `设置`
- empty KPI values and empty table

### Case 2. QMT enabled but not connected

Show:

- warning status badge
- explanatory text asking the user to confirm miniQMT is running and logged in
- keep any persisted snapshot data optional, but do not claim realtime availability

### Case 3. QMT connected but no latest breadth snapshot

Show:

- connected status badge
- "暂无最新快照" style message
- allow `dt_pool_v3` table to render if available

### Case 4. Focus list empty

Show:

- normal empty table state
- do not treat this as an error

## Frontend Changes

### `dashboard/src/components/Sidebar.tsx`

- add one menu item: `qmt`
- label: `QMT数据`
- keep the current top-level navigation interaction unchanged

### `dashboard/src/App.tsx`

- extend `TabId` with `qmt`
- render a new `QmtDataPage`
- keep `marketTab` unchanged; do not fold QMT into market sub-tabs

### `dashboard/src/pages/QmtDataPage.tsx`

Create a dedicated page component that:

- fetches `/api/qmt-overview` on mount
- refreshes every 60 seconds
- supports a lightweight manual refresh action
- renders status, KPIs, and the focus list

The component should remain presentation-oriented. Aggregation and fallback logic belong in the backend endpoint.

## Backend Changes

### `server.py`

Add `/api/qmt-overview`.

The handler should:

1. determine QMT enable / connection / version status
2. fetch the latest breadth snapshot data
3. fetch current `dt_pool_v3` rows
4. assemble a stable response payload

If the latest breadth snapshot is unavailable, the response should still include:

- status
- `dt_pool_v3` list
- count derived from `dt_pool_v3`

with breadth fields set to `null` or equivalent empty values.

## Validation

### Functional Validation

- sidebar shows `QMT数据`
- clicking it opens the new page
- page renders correctly when QMT is enabled and connected
- page renders correctly when QMT is disabled
- page renders correctly when QMT is enabled but disconnected

### Data Validation

- `QMT跌停数` equals `len(dt_pool_v3)`
- focus table row count equals the same `dt_pool_v3` result set
- snapshot metrics match the latest `market_breadth` row

### Manual Spot Check

Pick 1 to 3 representative `dt_pool_v3` rows and manually verify:

- code
- latest price
- last close
- down-limit price
- `latest price <= down-limit price`

This is enough for the first version because the page is a presentation layer over already validated calculation logic.

### Quality Checks

After implementation:

- run focused backend tests for the new endpoint
- run frontend diagnostics for touched files
- run existing relevant tests around `dt_pool_v3` if impacted

## Risks and Trade-Offs

- `stock_name` and `sector` in `dt_pool_v3` may still be incomplete in some cases
- the page reflects persisted snapshot data, not continuous tick streaming
- if `market_breadth` is stale, the page may be structurally correct but operationally outdated

These are acceptable for the first version because the goal is clarity and trust, not a full realtime terminal.

## Files To Change

- `d:\market-radar\dashboard\src\components\Sidebar.tsx`
- `d:\market-radar\dashboard\src\App.tsx`
- `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`
- `d:\market-radar\server.py`
- optionally focused tests under `d:\market-radar\tests\`

## Non-Goals

First version does not attempt to:

- expose raw full-market QMT tick dumps
- introduce websocket or streaming transport
- redesign the existing market page
- solve all QMT enrichment gaps in one pass
