# QMT Industry Stats Design

**Goal**

Add a new `行业统计榜` section to the existing `QMT数据` page.

The new section should aggregate the full A-share universe by `industry_l1` and show a compact industry-level dashboard driven by:

- QMT realtime prices
- local historical trading data under `QUANT_DATA_ROOT`

This section is not a replacement for `行业拖累榜`.

It is a broader industry cross-section view that should coexist with the current downside-focused monitor.

## Why

The current QMT page already answers two realtime questions well:

- what QMT is connected to
- which stocks are currently at limit-down
- which industries are currently dragged by the limit-down set

What is still missing is a full-market industry summary that combines:

- breadth inside each industry
- short-term trend participation
- strong-move participation
- limit-up / limit-down counts
- yesterday's local main capital flow

This new section should help answer a different practical question:

> Which industries are broadly strong, broadly weak, or structurally active right now?

That is a separate product surface from `行业拖累榜`, so it should be designed as a separate aggregation endpoint and a separate UI block.

## Confirmed Product Decisions

The following decisions are already confirmed:

- keep the existing `行业拖累榜`
- add a new standalone `行业统计榜`
- use the full industry universe, not only the current limit-down set
- define `大肉数量` as `current_change_pct >= 5%`

## Recommended Approach

Use a dedicated backend endpoint and a dedicated frontend section:

1. keep the current three QMT endpoints unchanged
2. add one new backend endpoint for industry-wide aggregation
3. add one new section on `QMT数据` page below the current industry dragger block
4. compute industry stats in the backend so the frontend remains render-oriented

This keeps responsibilities clean:

- `qmt-overview` remains the page summary endpoint
- `qmt-limit-down-monitor` remains the stock-level downside monitor
- `qmt-industry-draggers` remains the downside aggregation view
- the new endpoint becomes the full-market industry statistics view

## Scope

First version includes:

- one new backend endpoint for QMT industry stats
- one new frontend section on `QMT数据`
- full-industry aggregation using all locally known A-share stocks
- industry-level metrics:
  - stock count
  - above-MA10 ratio
  - `大肉数量`
  - limit-up count
  - limit-down count
  - yesterday main capital net inflow

First version does not include:

- multi-day industry trend charts
- historical ranking changes
- sector drill-down modal
- sorting controls in the UI
- minute-level K-line / time-series queries
- new third-party dependencies

## Product Structure

The `QMT数据` page becomes a four-part view:

1. QMT overview
2. 跌停监控中心
3. 行业拖累榜
4. 行业统计榜

### 行业统计榜

This section should answer:

- how many stocks each industry contains
- how many industry members are above the 10-day moving average
- how many industry members are showing strong intraday performance
- how many members are currently at limit-up or limit-down
- whether yesterday's local main capital flow was positive or negative

Suggested UI:

- KPI cards:
  - `行业数`
  - `MA10强势行业`
  - `最大大肉行业`
  - `主力净流入第一行业`
- table columns:
  - 行业
  - 个股数
  - 10日线上占比
  - 大肉数
  - 涨停数
  - 跌停数
  - 昨日主力净流入

Default sort:

- `昨日主力净流入` descending

Rationale:

- this makes the table a stable cross-sectional overview rather than another emotion-only ranking
- users can still visually read `大肉数`, `涨停数`, and `跌停数` side by side

## Data Sources

### Stock Universe

Use the full QMT A-share list:

- `list_a_shares()`

This should remain the stock universe root so the section stays aligned with the existing QMT page.

### Realtime Fields

Use QMT realtime snapshot fields from `get_full_tick_snapshot()`:

- `last_price`
- `last_close`

These support:

- current change percentage
- realtime limit-up / limit-down checks
- realtime MA10-above checks when combined with local MA10 baseline

### Local Historical Fields

Use local trading data under `QUANT_DATA_ROOT` for:

- `industry_l1`
- yesterday main capital buy / sell
- recent daily closes required to compute MA10 baseline if not already precomputed

The local dataset is also the authoritative source for stable industry classification.

## Backend API Design

### New Endpoint

Add:

- `GET /api/qmt-industry-stats`

Purpose:

- provide a frontend-ready industry cross-section payload
- avoid pushing heavy stock-level aggregation logic into the frontend

Suggested response:

```json
{
  "status": {
    "enabled": true,
    "connected": true,
    "version": "xtquant"
  },
  "snapshot": {
    "updated_at": "2026-06-27 14:35:00",
    "source": "xtquant",
    "market": "TOTAL"
  },
  "summary": {
    "sector_count": 31,
    "strong_ma10_sector_count": 9,
    "top_meat_sector": "通信",
    "top_main_inflow_sector": "电子"
  },
  "items": [
    {
      "sector": "通信",
      "stock_count": 128,
      "above_ma10_count": 74,
      "above_ma10_ratio": 0.5781,
      "meat_count": 9,
      "limit_up_count": 3,
      "limit_down_count": 7,
      "yesterday_main_inflow": 284000000.0
    }
  ]
}
```

Rules:

- keep a stable response shape when QMT is disconnected
- return empty `items` instead of 500 when data is unavailable
- all ratios and counts are derived in the backend
- `above_ma10_ratio` should be returned as a numeric ratio, not a formatted string
- `yesterday_main_inflow` should remain in raw yuan in the API and be formatted in the frontend

## Calculation Rules

### 1. Industry

Use:

- `industry_l1`

Do not mix `industry_l2` or `industry_l3` in first version.

Rows with empty industry should be dropped from industry aggregation.

This is important because an empty bucket would destroy the usefulness of the table.

### 2. Stock Count

For each industry:

```text
stock_count = number of valid stocks mapped to this industry
```

Valid stock means:

- stock appears in the QMT A-share universe
- local metadata provides a non-empty `industry_l1`
- realtime snapshot has usable `last_price` and `last_close`

### 3. Above-MA10 Ratio

For each stock:

- compute or read MA10 from the most recent 10 daily closes in local data
- mark `above_ma10 = last_price > ma10`

For each industry:

```text
above_ma10_count = sum(above_ma10)
above_ma10_ratio = above_ma10_count / stock_count
```

Notes:

- use strict `>` rather than `>=`
- if a stock does not have enough local history to compute MA10, exclude it from the industry stats input for first version

### 4. 大肉数量

For each stock:

```text
current_change_pct = (last_price / last_close - 1) * 100
meat = current_change_pct >= 5
```

For each industry:

```text
meat_count = sum(meat)
```

This rule is already confirmed.

### 5. 涨停数

Reuse the project's existing limit-up rule from the same local price-rule family used for limit-down.

For each stock:

- compute `up_limit` from local rules
- mark `limit_up = last_price >= up_limit`

For each industry:

```text
limit_up_count = sum(limit_up)
```

### 6. 跌停数

Reuse the existing limit-down rule already used by `dt_pool_v3`.

For each stock:

- compute `down_limit`
- mark `limit_down = last_price <= down_limit`

For each industry:

```text
limit_down_count = sum(limit_down)
```

### 7. Yesterday Main Capital Net Inflow

For each stock, use local data from the previous trading row:

```text
yesterday_main_inflow = inst_buy - inst_sell
```

For each industry:

```text
yesterday_main_inflow = sum(all member yesterday_main_inflow)
```

Notes:

- this amount comes from local data only
- do not mix realtime QMT amount fields into this metric
- if yesterday local capital fields are missing, treat the stock contribution as `0`

## Implementation Shape

### Backend Helper Layer

Add a focused helper in `fetcher/qmt_monitors.py` or a nearby helper module to:

- normalize stock-level realtime + local fields into a reusable stock snapshot
- group snapshots by `industry_l1`
- derive the industry payload

Suggested structure:

- one stock-level builder
- one industry-level aggregator
- one final payload builder

This keeps the Flask route thin and testable.

### Flask Route

In `server.py`:

- add `GET /api/qmt-industry-stats`
- reuse `_read_qmt_runtime_status()`
- reuse breadth snapshot helpers for `snapshot.updated_at`
- return empty `items` if QMT is not connected

### Frontend

In `dashboard/src/pages/QmtDataPage.tsx`:

- add `QmtIndustryStats` type
- include the new endpoint in `fetchAll()`
- add a new section below `行业拖累榜`
- render KPI cards and one table
- format:
  - `above_ma10_ratio` as percentage
  - `yesterday_main_inflow` as `亿`

## Error Handling and Empty States

### Case 1. QMT disabled or disconnected

Return:

- valid `status`
- valid `snapshot`
- empty `items`
- zeroed or null summary fields

Frontend should show:

- normal empty-state card
- not a crash

### Case 2. Local metadata missing for some symbols

Behavior:

- skip only the broken symbols
- do not fail the full industry aggregation
- optionally log the count of skipped symbols for debugging

### Case 3. Missing local historical rows

Behavior:

- if MA10 cannot be computed, exclude that stock from the industry input in first version
- if yesterday capital flow is unavailable, use `0` for that stock

This keeps the table stable and avoids fabricating trend signals from incomplete history.

## Validation

### Functional Validation

- `QMT数据` page still loads normally
- new `行业统计榜` section renders
- refresh loads all four QMT sections together
- the old `行业拖累榜` remains unchanged

### Data Validation

For each industry row:

- `above_ma10_count <= stock_count`
- `0 <= above_ma10_ratio <= 1`
- `meat_count <= stock_count`
- `limit_up_count <= stock_count`
- `limit_down_count <= stock_count`

At summary level:

- `sector_count == len(items)`
- `top_main_inflow_sector` matches the top row when sorted by `yesterday_main_inflow`

### Manual Spot Check

Pick 2 to 3 industries and manually verify:

- stock_count
- one representative stock's current change percentage
- one representative stock's MA10 judgment
- yesterday main capital net inflow direction
- limit-up / limit-down counts

Important checks:

- ST stocks
- blank sector rows
- missing local files
- industries with only a small number of stocks

## Risks and Trade-Offs

- full-universe aggregation is heavier than the current limit-down-only views
- local data completeness directly affects industry classification quality
- yesterday capital flow is a local-data metric, not a realtime QMT metric
- first version does not support client-side sorting or filtering

These are acceptable trade-offs because the first version should optimize for a stable, auditable industry cross-section with minimal new moving parts.

## Files To Change

- `d:\market-radar\server.py`
- `d:\market-radar\fetcher\qmt_monitors.py`
- `d:\market-radar\fetcher\qmt_data_api.py`
- optionally `d:\market-radar\quant\security_meta.py`
- `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`
- focused tests under `d:\market-radar\tests\`

## Non-Goals

First version does not attempt to:

- rank industries by custom composite score
- add front-end sort / filter controls
- build historical industry leaderboards
- replace the existing dragger table
- stream updates via websocket
