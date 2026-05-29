import { useState, useEffect, useCallback } from 'react';
import { PolicyCard } from '../components/PolicyCard';
import { FilterTabs } from '../components/FilterTabs';
import { TabHeader } from '../components/TabHeader';
import { Pagination } from '../components/Pagination';

interface RawPolicyItem {
  id: number | string;
  title: string;
  link?: string;
  pub_time: string;
  source: string;
}

interface PolicyItem {
  id: string;
  title: string;
  content: string;
  department: string;
  date: string;
  category: string;
  link?: string;
}

interface PagedResponse {
  items: RawPolicyItem[];
  total: number;
  page: number;
  page_size: number;
}

const POLICY_SOURCES = [
  '巨潮公告', '财新', '发改委', '证监会',
  '上交所问询', '深交所问询', '深交所公告',
] as const;

const TABS = [
  { id: 'all', label: '全部' },
  ...POLICY_SOURCES.map(s => ({ id: s, label: s })),
];

function mapItem(item: RawPolicyItem): PolicyItem {
  return {
    id: String(item.id),
    title: item.title,
    content: '',
    department: item.source,
    date: item.pub_time,
    category: item.source,
    link: item.link || undefined,
  };
}

function SkeletonCard() {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 animate-pulse">
      <div className="h-3 bg-gray-200 rounded w-20 mb-3" />
      <div className="h-4 bg-gray-200 rounded w-full mb-1" />
      <div className="h-4 bg-gray-100 rounded w-4/5 mb-4" />
      <div className="flex justify-between">
        <div className="h-3 bg-gray-100 rounded w-16" />
        <div className="h-3 bg-gray-100 rounded w-24" />
      </div>
    </div>
  );
}

export function PolicyPage({ defaultPageSize = 30 }: { defaultPageSize?: number }) {
  const [activeFilter, setActiveFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [pageSize] = useState(defaultPageSize);
  const [items, setItems] = useState<PolicyItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);

  const handleFilterChange = (f: string) => {
    setActiveFilter(f);
    setPage(1);
  };

  const fetchPage = useCallback(async (filter: string, p: number, ps: number) => {
    setLoading(true);
    setItems([]);
    try {
      const src = filter === 'all' ? '全部' : filter;
      const res = await fetch(
        `/api/policy?source=${encodeURIComponent(src)}&page=${p}&page_size=${ps}`
      );
      const json = await res.json();
      if (json.success) {
        const data = json.data as PagedResponse;
        setItems(data.items.map(mapItem));
        setTotal(data.total);
      }
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPage(activeFilter, page, pageSize);
  }, [activeFilter, page, pageSize, fetchPage]);

  const tabsWithCount = TABS.map(t => ({
    ...t,
    count: t.id === activeFilter ? total : undefined,
  }));

  return (
    <div>
      <TabHeader
        title="📋 政策动态"
        subtitle="监管政策与公告"
        count={loading ? undefined : total}
      />
      <FilterTabs
        tabs={tabsWithCount}
        activeTab={activeFilter}
        onTabChange={handleFilterChange}
      />
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {items.map(item => <PolicyCard key={item.id} policy={item} />)}
        </div>
      )}
      <Pagination
        page={page}
        total={total}
        pageSize={pageSize}
        onPageChange={setPage}
      />
    </div>
  );
}
