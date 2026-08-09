# DT Pool V3 Design

**Goal**

Add a third, truly independent跌停口径 `dt_pool_v3`, computed from QMT full-market realtime ticks instead of Eastmoney/AkShare.

**Why**

`dt_pool` and `dt_pool_v2` both ultimately depend on the same Eastmoney跌停池 source and currently converge to the same result. They are useful for comparison against each other, but they do not give an independent realtime view. `dt_pool_v3` exists to answer a different question: "How many stocks are currently封死跌停 according to raw realtime prices and the project's own limit-down calculation rules?"

## Approach

`dt_pool_v3` uses `xtquant.xtdata.get_full_tick()` over the full A-share universe. For each stock:

1. Read realtime `lastPrice`
2. Read prior close `lastClose`
3. Compute `down_limit` using the same board/ST rules already implemented in `quant/loader.py`
4. Mark the stock as `dt_pool_v3` member when `lastPrice <= down_limit`

This makes `dt_pool_v3` independent from Eastmoney's榜单口径 and aligned with the project's own涨跌停 price rules.

## Scope

Keep existing pipelines unchanged:

- `dt_pool`: existing AkShare / Eastmoney path
- `dt_pool_v2`: direct Eastmoney API path

Add a new parallel pipeline:

- storage table: `dt_pool_v3`
- fetcher: `fetch_dt_pool_v3()`
- api: `/api/dt-pool-v3`
- dashboard KPI: `QMT跌停` beside `今日跌停` and `实验跌停`

## Data Model

First version stores the minimum fields needed to validate the new口径:

- `trade_date`
- `stock_code`
- `stock_name`
- `last_price`
- `last_close`
- `down_limit`
- `sector`

`stock_name` and `sector` may be empty in the first version if QMT tick data does not provide them reliably. The KPI count is the primary success metric.

## Fetch Rules

The new fetcher runs only when QMT is enabled and reachable.

Behavior:

- connect to QMT
- get A-share stock list
- get full ticks
- clear today's `dt_pool_v3`
- compute current limit-down membership
- insert current snapshot rows

If QMT is unavailable:

- do not raise a fatal error
- do not affect `dt_pool` or `dt_pool_v2`
- skip this cycle cleanly

## Snapshot Semantics

`dt_pool_v3` is a realtime snapshot, not a cumulative intraday set.

Each run:

1. clears today's rows
2. writes only the current detected limit-down members

This matches the corrected snapshot behavior already applied to the other pools.

## Files To Change

- `d:\market-radar\db\storage.py`
  - add `dt_pool_v3` table
  - add `insert_dt_pool_v3()`
  - add `clear_dt_pool_v3()`
  - add `get_dt_pool_v3()`

- `d:\market-radar\fetcher\sector_heat.py` or a focused new fetcher module
  - add `fetch_dt_pool_v3()`
  - reuse the existing project limit-price calculation rules

- `d:\market-radar\core\scheduler.py`
  - schedule `fetch_dt_pool_v3()` beside the existing pool fetchers

- `d:\market-radar\server.py`
  - add `/api/dt-pool-v3`

- `d:\market-radar\dashboard\src\pages\MarketRealtimePage.tsx`
  - add one more KPI card for `QMT跌停`

- `d:\market-radar\tests\...`
  - add focused backend tests for storage, fetch filtering, and API route

## Validation

Backend:

- unit test storage round-trip
- unit test snapshot replacement behavior
- unit test limit-down filtering against representative sample ticks
- unit test API route

Runtime:

- trigger one manual `fetch_dt_pool_v3()`
- confirm `dt_pool_v3` row count is populated when QMT is connected
- confirm `/api/dt-pool-v3` matches DB count
- confirm dashboard renders the new KPI

Business validation:

- compare `dt_pool`
- compare `dt_pool_v2`
- compare `dt_pool_v3`

The expected outcome is not "exactly equal to Eastmoney". The expected outcome is that `dt_pool_v3` provides a visibly different and more self-consistent realtime count derived from raw market data.

## Non-Goals

First version does not include:

- intraday "touched limit-down" membership
- mixed fallback between QMT and Eastmoney
- historical replay/backfill for `dt_pool_v3`
- heavy enrichment such as guaranteed sector/name mapping if not directly available
