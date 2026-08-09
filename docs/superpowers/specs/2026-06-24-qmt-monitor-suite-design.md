# QMT Monitor Suite Design

**Goal**

Add two focused QMT-driven monitoring features on top of the existing `QMT数据` page:

- `跌停监控中心`
- `行业拖累榜`

The first version should turn the existing QMT limit-down pipeline into a more useful intraday monitoring surface without introducing new data dependencies or heavy realtime infrastructure.

**Why**

The project already has the core building blocks:

- a dedicated QMT page
- a stable QMT runtime overview endpoint
- an independent `dt_pool_v3` snapshot computed from raw QMT ticks
- local stock metadata available under `QUANT_DATA_ROOT`

What is still missing is a product layer that answers two practical trading-day questions quickly:

1. Which stocks are currently locked at limit-down, and what kind of risk structure do they represent?
2. Which industries are currently contributing the most downside pressure?

These two questions share the same realtime input and local metadata enrichment path, so they should be designed together.

## Recommended Approach

Use **Scheme B**:

1. Keep `GET /api/qmt-overview` as the lightweight overview endpoint
2. Add one focused backend endpoint for `跌停监控中心`
3. Add one focused backend endpoint for `行业拖累榜`
4. Extend the existing `QMT数据` page into three sections:
   - QMT overview
   - limit-down monitor
   - industry draggers

This keeps endpoint boundaries clear and avoids overloading the current overview payload with feature-specific aggregation.

## Scope

First version includes:

- one new limit-down monitor endpoint
- one new industry draggers endpoint
- frontend sections on the existing `QMT数据` page
- basic summary cards for both features
- limit-down table with light filtering
- industry aggregation table sorted by downside contribution

First version does not include:

- open-limit / re-seal event tracking
- minute-by-minute trend charts
- full-market realtime industry scan outside the current limit-down set
- websocket streaming
- new third-party dependencies

## Product Structure

The existing `QMT数据` page remains the top-level entry point.

The page becomes a three-part view:

### 1. QMT Overview

Keep the current section that shows:

- QMT enable / connection status
- xtquant version
- latest snapshot timestamp
- market breadth KPIs

This section remains a health-check and snapshot summary surface.

### 2. 跌停监控中心

This section is the first focused monitoring module.

It should answer:

- how many current limit-down stocks there are
- how many are `ST`
- how many are non-`ST`
- how widely the limit-down pressure is distributed across industries

Suggested UI:

- KPI cards:
  - `跌停总数`
  - `ST跌停数`
  - `非ST跌停数`
  - `涉及行业数`
- basic filters:
  - stock code / stock name keyword
  - only `ST`
  - industry selector
- table columns:
  - stock code
  - stock name
  - industry
  - latest price
  - last close
  - down-limit price
  - tags

### 3. 行业拖累榜

This section is the second focused monitoring module.

It should answer:

- which industries currently contribute the most limit-down pressure
- whether the pressure is driven by many names or a smaller `ST` cluster
- which stock is the lead representative of each weak industry bucket

Suggested UI:

- KPI cards:
  - `拖累行业数`
  - `最弱行业`
  - `最大行业跌停数`
- table columns:
  - industry
  - limit-down count
  - `ST` count
  - non-`ST` count
  - lead stock
  - drag score

## Data Sources

### Realtime Input

Continue to use the existing `dt_pool_v3` generation path:

- QMT connection from `fetcher.xtquant_breadth.connect`
- full-market tick snapshot from `xtdata.get_full_tick`
- limit-down detection from `compute_down_limit`

This preserves the current rule:

- a stock enters `dt_pool_v3` when `last_price <= down_limit`

### Local Metadata

Continue to use local trading data under `QUANT_DATA_ROOT` to enrich missing metadata:

- stock name
- `industry_l1`

The current helper inside `fetcher/sector_heat.py` proves this path works and improves `ST` recognition accuracy. The first version may keep the existing helper in place, but implementation should move toward a reusable local stock metadata function.

### Industry Classification

For first version consistency, use:

- `industry_l1` only

Do not mix `industry_l1` and `industry_l2` in the initial tables.

## Backend API Design

### 1. `GET /api/qmt-limit-down-monitor`

Purpose:

- provide a frontend-ready payload for `跌停监控中心`
- keep the frontend render-oriented rather than calculation-oriented

Suggested response:

```json
{
  "status": {
    "enabled": true,
    "connected": true,
    "version": "xtquant"
  },
  "snapshot": {
    "updated_at": "2026-06-24 14:35:00",
    "source": "QMT",
    "market": "沪深A股"
  },
  "summary": {
    "total_count": 72,
    "st_count": 18,
    "non_st_count": 54,
    "industry_count": 21
  },
  "industry_distribution": [
    {
      "sector": "计算机",
      "count": 9
    }
  ],
  "items": [
    {
      "stock_code": "000016.SZ",
      "stock_name": "*ST康佳A",
      "sector": "家用电器",
      "last_price": 4.15,
      "last_close": 4.37,
      "down_limit": 4.15,
      "limit_gap_pct": 0.0,
      "is_st": true,
      "tags": ["ST", "跌停"]
    }
  ]
}
```

Rules:

- if QMT is disconnected, do not raise a 500
- return a stable shape with empty arrays and zero or null summary fields where appropriate
- `is_st` must be computed in the backend, not guessed by the frontend
- `tags` are presentation-friendly labels derived in the backend

### 2. `GET /api/qmt-industry-draggers`

Purpose:

- provide a frontend-ready payload for `行业拖累榜`
- aggregate the current limit-down monitor set by industry

Suggested response:

```json
{
  "status": {
    "enabled": true,
    "connected": true,
    "version": "xtquant"
  },
  "snapshot": {
    "updated_at": "2026-06-24 14:35:00",
    "source": "QMT",
    "market": "沪深A股"
  },
  "summary": {
    "sector_count": 21,
    "top_dragger_sector": "计算机",
    "top_dragger_limit_down_count": 9
  },
  "items": [
    {
      "sector": "计算机",
      "limit_down_count": 9,
      "st_count": 2,
      "non_st_count": 7,
      "lead_stock_code": "000004.SZ",
      "lead_stock_name": "国华退",
      "drag_score": 9.4
    }
  ]
}
```

Rules:

- aggregate from the same current limit-down item set used by `qmt-limit-down-monitor`
- do not independently query or recalculate a second inconsistent stock universe
- keep the first version formula simple and explicit

## Calculation Rules

### `is_st`

First version rule:

- `is_st = "ST" in stock_name.upper()`

This is intentionally aligned with the current project behavior and the corrected local metadata fallback path.

### `limit_gap_pct`

For each row:

```text
limit_gap_pct = (last_price / down_limit - 1) * 100
```

Notes:

- current limit-down members should typically show `0`
- keep the field because it becomes useful once later versions include "near limit-down" or "opened from limit-down" logic

### `drag_score`

First version should use a simple and explainable formula:

```text
drag_score = limit_down_count + st_count * 0.2
```

Rationale:

- `limit_down_count` remains the primary ranking factor
- `ST` concentration gets a small additional weight
- the formula is transparent and easy to revise later

### Industry Aggregation

Group the current monitor item set by `sector`.

Per industry compute:

- `limit_down_count`
- `st_count`
- `non_st_count`
- `lead_stock_code`
- `lead_stock_name`
- `drag_score`

For first version:

- `lead_stock` may simply be the first row in the grouped set after the default stock ordering
- this is acceptable because the page is diagnostic, not a ranking authority for individual stocks

## Error Handling and Empty States

The page must remain renderable in all cases.

### Case 1. QMT disabled

Show:

- disabled status
- empty monitor tables
- guidance that the user can enable QMT in `设置`

### Case 2. QMT enabled but disconnected

Show:

- warning status
- empty or previously persisted-safe structures
- guidance to confirm miniQMT client startup and login

### Case 3. QMT connected but current limit-down set empty

Show:

- normal empty state
- no error banner

This should not be treated as a failure.

### Case 4. Local metadata incomplete

Behavior:

- allow `stock_name` or `sector` to be empty
- render `--` in the frontend
- do not block the entire page because some enrichment fields are unavailable

## Frontend Changes

### `dashboard/src/pages/QmtDataPage.tsx`

Extend the page into three sections:

1. overview
2. limit-down monitor
3. industry draggers

Suggested client behavior:

- fetch overview on mount
- fetch monitor payload on mount
- fetch dragger payload on mount
- refresh all three every 60 seconds
- support manual refresh

The page may use shared loading and refresh state, but each section should tolerate partial data failure without blanking the whole page.

### Rendering Principles

- keep the current visual style and card system
- avoid introducing complex table virtualization or charting in first version
- present filters as lightweight controls rather than a full search framework

## Backend Changes

### `server.py`

Add:

- `/api/qmt-limit-down-monitor`
- `/api/qmt-industry-draggers`

Implementation should reuse:

- `_read_qmt_runtime_status()`
- `_latest_market_breadth_row()`
- `get_dt_pool_v3(None)`

The new endpoints should not duplicate connection or breadth logic inconsistently.

### `fetcher/sector_heat.py` or a focused helper module

Add a reusable shaping layer over the current `dt_pool_v3` rows.

This layer should:

- normalize row fields
- derive `is_st`
- derive `tags`
- derive `limit_gap_pct`
- aggregate industry summaries

This is preferable to doing the aggregation inline inside Flask route functions.

### `quant/loader.py`

Optional first-version improvement:

- extract a reusable local metadata helper so `code -> stock_name / industry_l1` is not only available through a fetcher-local private function

This is not mandatory to ship v1, but it is the preferred direction.

## Validation

### Functional Validation

- `QMT数据` page still loads normally
- overview section still renders
- limit-down monitor section renders
- industry draggers section renders
- manual refresh updates all sections

### Data Validation

- `summary.total_count` equals the number of current monitor items
- `summary.st_count + summary.non_st_count == summary.total_count`
- `industry_distribution` totals sum to `summary.total_count`
- industry table row count equals `summary.sector_count`
- the top row of `industry draggers` matches the maximum industry limit-down count

### Manual Spot Check

Pick representative samples covering:

- one `ST` stock
- one non-`ST` stock
- one industry with multiple limit-down names

Verify:

- stock name
- industry
- `is_st`
- `last_price`
- `last_close`
- `down_limit`
- aggregation count for the chosen industry

### Quality Checks

After implementation:

- run focused backend tests for both new endpoints
- run focused tests for derived monitor calculations if added
- run frontend diagnostics on touched files
- run relevant existing `dt_pool_v3` tests

## Risks and Trade-Offs

- the first version reflects the current `dt_pool_v3` snapshot, not an intraday event history
- some local metadata gaps may remain for edge-case symbols
- industry dragging is measured from the current limit-down set only, not full-market breadth

These trade-offs are acceptable because the first version is meant to produce a usable monitoring surface with the data paths that are already proven in this project.

## Files To Change

- `d:\market-radar\server.py`
- `d:\market-radar\fetcher\sector_heat.py`
- `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`
- optionally `d:\market-radar\quant\loader.py`
- focused tests under `d:\market-radar\tests\`

## Non-Goals

First version does not attempt to:

- track first limit-down time
- track opened-from-limit-down or re-sealed events
- compute full-market industry weakness outside the current limit-down universe
- add charts or streaming transport
- replace the existing market page or existing QMT overview endpoint
