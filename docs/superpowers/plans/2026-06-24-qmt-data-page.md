# QMT Data Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new top-level `QMT数据` page that shows QMT connection health, the latest breadth snapshot, core KPI cards, and the current `dt_pool_v3` focus list.

**Architecture:** Keep the first version reuse-first. Add one backend aggregation endpoint in `server.py` that combines the existing QMT status logic, the latest `market_breadth` row, and the existing `dt_pool_v3` rows into one stable payload. Add one new React page component for rendering, then wire it into the existing sidebar and `App.tsx` tab switch without changing the current market sub-tabs.

**Tech Stack:** Python, Flask, SQLite, React, TypeScript, Vite, unittest

---

### Task 1: Add Backend Aggregation API For QMT Overview

**Files:**
- Modify: `d:\market-radar\server.py`
- Modify: `d:\market-radar\db\storage.py`
- Test: `d:\market-radar\tests\test_qmt_overview_api.py`

- [ ] **Step 1: Write the failing test**

Create `d:\market-radar\tests\test_qmt_overview_api.py`:

```python
import unittest
from unittest import mock

import server


class QmtOverviewApiTests(unittest.TestCase):
    def test_api_qmt_overview_returns_aggregated_payload(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(server, "get_market_breadth_latest", return_value=[
            {
                "fetch_time": "14:35",
                "source": "qmt",
                "market": "A股",
                "up_count": 1234,
                "down_count": 3456,
                "flat_count": 78,
                "total_amount": 1234567890,
            }
        ], create=True), mock.patch.object(
            server,
            "get_dt_pool_v3",
            return_value=[
                {
                    "stock_code": "603022.SH",
                    "stock_name": "新通联",
                    "last_price": 11.06,
                    "last_close": 12.29,
                    "down_limit": 11.06,
                    "sector": "",
                }
            ],
            create=True,
        ), mock.patch.dict(server.os.environ, {"QMT_ENABLED": "true"}, clear=False), mock.patch(
            "fetcher.xtquant_breadth.connect",
            return_value=True,
        ), mock.patch(
            "fetcher.xtquant_breadth.get_version",
            return_value="1.0.0",
        ):
            response = client.get("/api/qmt-overview")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["status"]["enabled"], True)
        self.assertEqual(payload["data"]["status"]["connected"], True)
        self.assertEqual(payload["data"]["kpis"]["limit_down_count"], 1)
        self.assertEqual(payload["data"]["kpis"]["up_count"], 1234)
        self.assertEqual(payload["data"]["focus_list"][0]["stock_code"], "603022.SH")

    def test_api_qmt_overview_survives_disconnected_qmt(self) -> None:
        client = server.app.test_client()
        with mock.patch.object(server, "get_market_breadth_latest", return_value=[], create=True), mock.patch.object(
            server,
            "get_dt_pool_v3",
            return_value=[],
            create=True,
        ), mock.patch.dict(server.os.environ, {"QMT_ENABLED": "true"}, clear=False), mock.patch(
            "fetcher.xtquant_breadth.connect",
            return_value=False,
        ), mock.patch(
            "fetcher.xtquant_breadth.get_version",
            return_value="1.0.0",
        ):
            response = client.get("/api/qmt-overview")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["status"]["connected"], False)
        self.assertEqual(payload["data"]["focus_list"], [])
        self.assertEqual(payload["data"]["snapshot"]["updated_at"], None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_qmt_overview_api -v`
Expected: FAIL with missing `/api/qmt-overview` route or missing `get_market_breadth_latest` import in `server.py`.

- [ ] **Step 3: Write minimal implementation**

Update the `server.py` storage imports to include `get_market_breadth_latest`:

```python
from db.storage import (
    ...
    get_market_breadth_latest,
)
```

Add two small helpers near the existing helper section:

```python
def _read_qmt_runtime_status() -> dict:
    qmt_enabled = os.environ.get("QMT_ENABLED", "false").lower() in ("true", "1", "yes")
    qmt_connected = False
    qmt_version = None
    if qmt_enabled:
        try:
            from fetcher.xtquant_breadth import connect as _qmt_connect, get_version as _qmt_ver
            qmt_connected = _qmt_connect()
            qmt_version = _qmt_ver()
        except Exception:
            pass
    return {
        "enabled": qmt_enabled,
        "connected": qmt_connected,
        "version": qmt_version,
    }


def _latest_market_breadth_row() -> dict | None:
    rows = get_market_breadth_latest(1)
    return rows[-1] if rows else None
```

Add the route:

```python
@app.route("/api/qmt-overview")
def api_qmt_overview():
    try:
        status = _read_qmt_runtime_status()
        breadth = _latest_market_breadth_row()
        focus_list = get_dt_pool_v3(None)

        snapshot = {
            "updated_at": breadth["fetch_time"] if breadth else None,
            "source": breadth["source"] if breadth else "QMT",
            "market": breadth["market"] if breadth else None,
        }
        kpis = {
            "limit_down_count": len(focus_list),
            "up_count": breadth["up_count"] if breadth else None,
            "down_count": breadth["down_count"] if breadth else None,
            "flat_count": breadth["flat_count"] if breadth else None,
            "turnover": breadth["total_amount"] if breadth else None,
        }
        return _ok({
            "status": status,
            "snapshot": snapshot,
            "kpis": kpis,
            "focus_list": focus_list,
        })
    except Exception as exc:
        return _err(exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_qmt_overview_api -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_qmt_overview_api.py
git commit -m "feat: add qmt overview api"
```

### Task 2: Build The `QmtDataPage` Frontend Screen

**Files:**
- Create: `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`
- Modify: `d:\market-radar\dashboard\src\lib\api.ts` (only if a small helper is truly needed; otherwise leave unchanged)

- [ ] **Step 1: Write the page with explicit types**

Create `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`:

```tsx
import { useEffect, useMemo, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { TabHeader } from '../components/TabHeader';
import { apiFetch } from '../lib/api';

interface QmtOverview {
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
  kpis: {
    limit_down_count: number;
    up_count: number | null;
    down_count: number | null;
    flat_count: number | null;
    turnover: number | null;
  };
  focus_list: Array<{
    stock_code: string;
    stock_name: string;
    last_price: number;
    last_close: number;
    down_limit: number;
    sector: string;
  }>;
}

const fmtCount = (v: number | null | undefined) => v == null ? '--' : `${v}`;
const fmtAmountYi = (v: number | null | undefined) => v == null ? '--' : `${(v / 1e8).toFixed(2)}亿`;
const fmtNum = (v: number | null | undefined) => v == null ? '--' : v.toFixed(2);

export function QmtDataPage() {
  const [data, setData] = useState<QmtOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');

  const load = async (silent = false) => {
    if (!silent) setLoading(true);
    setRefreshing(silent);
    setError('');
    try {
      const next = await apiFetch<QmtOverview>('/api/qmt-overview');
      setData(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    load(false);
    const id = window.setInterval(() => void load(true), 60_000);
    return () => window.clearInterval(id);
  }, []);

  const statusTone = useMemo(() => {
    if (!data?.status.enabled) return 'bg-gray-50 text-gray-500 border-gray-200';
    if (!data.status.connected) return 'bg-amber-50 text-amber-700 border-amber-200';
    return 'bg-green-50 text-green-700 border-green-200';
  }, [data]);

  return (
    <>
      <TabHeader title="QMT数据" />
      <div className="space-y-6">
        <div className="bg-white rounded-2xl border border-gray-100 p-6 shadow-[var(--shadow-sm)]">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-sm font-semibold text-gray-900">miniQMT 运行状态</p>
              <p className="text-xs text-gray-400 mt-1">展示当前连接状态与最近一次可用快照。</p>
            </div>
            <button
              onClick={() => void load(true)}
              disabled={refreshing}
              className="inline-flex items-center gap-2 px-3 py-2 text-xs font-medium text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              刷新
            </button>
          </div>

          <div className="mt-4 flex flex-wrap gap-3">
            <span className={`inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border ${statusTone}`}>
              <span className="w-1.5 h-1.5 rounded-full bg-current opacity-70" />
              {!data?.status.enabled ? 'QMT未启用' : data.status.connected ? 'QMT已连接' : 'QMT未连接'}
            </span>
            <span className="text-xs text-gray-500">xtquant 版本：{data?.status.version ?? '--'}</span>
            <span className="text-xs text-gray-500">最近快照：{data?.snapshot.updated_at ?? '--'}</span>
          </div>

          {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
          {!loading && !error && !data?.status.enabled && (
            <p className="mt-3 text-xs text-gray-500">当前未启用 QMT，可前往“设置”页开启 miniQMT 数据源。</p>
          )}
          {!loading && !error && data?.status.enabled && !data.status.connected && (
            <p className="mt-3 text-xs text-amber-600">QMT 已启用但未连接，请确认 miniQMT 客户端已启动并登录。</p>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-4">
          {[
            { label: 'QMT跌停数', value: fmtCount(data?.kpis.limit_down_count) },
            { label: '上涨家数', value: fmtCount(data?.kpis.up_count) },
            { label: '下跌家数', value: fmtCount(data?.kpis.down_count) },
            { label: '平盘家数', value: fmtCount(data?.kpis.flat_count) },
            { label: '全市场成交额', value: fmtAmountYi(data?.kpis.turnover) },
          ].map((item) => (
            <div key={item.label} className="bg-white rounded-xl border border-gray-100 p-4">
              <div className="text-xs text-gray-400">{item.label}</div>
              <div className="mt-2 text-2xl font-semibold text-gray-900">{loading ? '--' : item.value}</div>
            </div>
          ))}
        </div>

        <div className="bg-white rounded-2xl border border-gray-100 overflow-hidden shadow-[var(--shadow-sm)]">
          <div className="px-4 py-3 border-b border-gray-100 flex items-center justify-between">
            <div>
              <p className="text-sm font-semibold text-gray-900">QMT 跌停明细</p>
              <p className="text-xs text-gray-400 mt-1">当前来自 `dt_pool_v3` 的重点列表。</p>
            </div>
            <span className="text-xs text-gray-400">{data?.focus_list.length ?? 0} 条</span>
          </div>

          <div className="grid grid-cols-6 px-4 py-2 text-xs text-gray-400 bg-gray-50 border-b border-gray-100">
            <span>代码</span>
            <span>名称</span>
            <span className="text-center">最新价</span>
            <span className="text-center">昨收</span>
            <span className="text-center">跌停价</span>
            <span className="text-center">板块</span>
          </div>

          {(data?.focus_list.length ?? 0) === 0 ? (
            <div className="px-4 py-10 text-sm text-center text-gray-400">暂无 QMT 跌停明细</div>
          ) : (
            <div className="divide-y divide-gray-50">
              {data!.focus_list.map((row) => (
                <div key={row.stock_code} className="grid grid-cols-6 px-4 py-3 text-xs text-gray-700 hover:bg-gray-50">
                  <span>{row.stock_code}</span>
                  <span>{row.stock_name || '--'}</span>
                  <span className="text-center">{fmtNum(row.last_price)}</span>
                  <span className="text-center">{fmtNum(row.last_close)}</span>
                  <span className="text-center text-teal-600 font-medium">{fmtNum(row.down_limit)}</span>
                  <span className="text-center">{row.sector || '--'}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 2: Run frontend build to verify it fails before wiring**

Run: `npm run build`
Working directory: `d:\market-radar\dashboard`
Expected: FAIL because the new page is not yet imported or referenced in `App.tsx`.

- [ ] **Step 3: Keep implementation minimal**

Do not add a new frontend dependency. Reuse:

```tsx
import { TabHeader } from '../components/TabHeader';
import { apiFetch } from '../lib/api';
```

Keep all data loading inside the page component and leave `api.ts` unchanged unless TypeScript demands a tiny helper.

- [ ] **Step 4: Run TypeScript/build check after the component exists**

Run: `npm run build`
Working directory: `d:\market-radar\dashboard`
Expected: still FAIL until navigation wiring is added in Task 3, but there should be no syntax error inside `QmtDataPage.tsx`.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/pages/QmtDataPage.tsx
git commit -m "feat: add qmt data page component"
```

### Task 3: Wire The New Page Into Sidebar And App Routing

**Files:**
- Modify: `d:\market-radar\dashboard\src\components\Sidebar.tsx`
- Modify: `d:\market-radar\dashboard\src\App.tsx`
- Check: `d:\market-radar\dashboard\src\lib\useSettings.ts`

- [ ] **Step 1: Update the tab union and import**

In `d:\market-radar\dashboard\src\App.tsx`, add:

```tsx
import { QmtDataPage } from './pages/QmtDataPage';

type TabId = 'news' | 'policy' | 'market' | 'qmt' | 'research' | 'ai-analysis' | 'settings';
```

Do not change `defaultTab` in `useSettings.ts` for the first version.

- [ ] **Step 2: Add the new route case**

In `renderPage()` inside `d:\market-radar\dashboard\src\App.tsx`, add:

```tsx
case 'qmt':
  return <QmtDataPage />;
```

Keep the existing `marketTab` logic untouched.

- [ ] **Step 3: Add the sidebar entry**

In `d:\market-radar\dashboard\src\components\Sidebar.tsx`, extend `menuItems`:

```tsx
const menuItems = [
  { id: 'market', icon: TrendingUp, label: '市场数据' },
  { id: 'qmt', icon: BarChart3, label: 'QMT数据' },
  { id: 'news', icon: FileText, label: '财经快讯' },
  { id: 'policy', icon: BarChart3, label: '政策动态' },
  { id: 'research', icon: FileText, label: '研究报告' },
  { id: 'ai-analysis', icon: Sparkles, label: 'AI智能分析' },
  { id: 'settings', icon: Settings, label: '设置' },
];
```

If using `BarChart3` for both `qmt` and `policy` feels visually too close during implementation, switch only the new item to another icon already available from `lucide-react`; do not add a dependency.

- [ ] **Step 4: Run the build to verify it passes**

Run: `npm run build`
Working directory: `d:\market-radar\dashboard`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/App.tsx dashboard/src/components/Sidebar.tsx dashboard/src/pages/QmtDataPage.tsx
git commit -m "feat: wire qmt data page into dashboard"
```

### Task 4: Final Validation For Backend, Frontend, And Diagnostics

**Files:**
- Test: `d:\market-radar\tests\test_qmt_overview_api.py`
- Check: `d:\market-radar\server.py`
- Check: `d:\market-radar\dashboard\src\App.tsx`
- Check: `d:\market-radar\dashboard\src\components\Sidebar.tsx`
- Check: `d:\market-radar\dashboard\src\pages\QmtDataPage.tsx`

- [ ] **Step 1: Run focused backend tests**

Run: `python -m unittest tests.test_qmt_overview_api tests.test_dt_pool_v3 -v`
Expected: PASS

- [ ] **Step 2: Run Python syntax verification**

Run: `python -m py_compile server.py tests/test_qmt_overview_api.py`
Expected: no output

- [ ] **Step 3: Run frontend build**

Run: `npm run build`
Working directory: `d:\market-radar\dashboard`
Expected: PASS

- [ ] **Step 4: Check diagnostics for touched frontend files**

Check:

- `file:///d:/market-radar/dashboard/src/App.tsx`
- `file:///d:/market-radar/dashboard/src/components/Sidebar.tsx`
- `file:///d:/market-radar/dashboard/src/pages/QmtDataPage.tsx`
- `file:///d:/market-radar/server.py`

Expected: no new diagnostics in touched files.

- [ ] **Step 5: Manual runtime verification**

Start the app and verify:

- the sidebar shows `QMT数据`
- clicking `QMT数据` opens the new page
- the page shows one of the three expected states:
  - `QMT未启用`
  - `QMT未连接`
  - `QMT已连接`
- `QMT跌停数` equals the number of rows returned by `/api/qmt-overview`
- the table renders `dt_pool_v3` rows when available
- the page remains renderable even when QMT is disconnected

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_qmt_overview_api.py dashboard/src/App.tsx dashboard/src/components/Sidebar.tsx dashboard/src/pages/QmtDataPage.tsx
git commit -m "feat: add qmt data overview page"
```
