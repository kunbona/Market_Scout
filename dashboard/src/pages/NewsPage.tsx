import { useState, useEffect, useCallback } from 'react';
import { relativeTime } from '../lib/time';
import { NewsCard } from '../components/NewsCard';
import { FilterTabs } from '../components/FilterTabs';
import { TabHeader } from '../components/TabHeader';
import { Pagination } from '../components/Pagination';

interface RawNewsItem {
  id: number | string;
  title: string;
  content?: string;
  pub_time: string;
  source: string;
  link?: string;
}

interface NewsItem {
  id: string;
  title: string;
  summary: string;
  source: string;
  time: string;
  link?: string;
}

interface PagedResponse {
  items: RawNewsItem[];
  total: number;
  page: number;
  page_size: number;
}

const SOURCES = ['财联社', '金十数据', '格隆汇', '东方财富', '同花顺', '第一财经', '华尔街见闻'] as const;
type Source = typeof SOURCES[number];

const TABS = [
  { id: 'all', label: '全部' },
  ...SOURCES.map(s => ({ id: s, label: s })),
];

function resolveLink(source: string, link?: string): string | undefined {
  if (!link) return undefined;

  // 财联社：api3.cls.cn/share/article/123456 → www.cls.cn/detail/123456
  if (source === '财联社') {
    const m = link.match(/\/article\/(\d+)/);
    if (m) return `https://www.cls.cn/detail/${m[1]}`;
  }

  return link;
}

function mapItem(item: RawNewsItem): NewsItem {
  return {
    id: String(item.id),
    title: item.title,
    summary: (item.content ?? '').slice(0, 150),
    source: item.source,
    time: relativeTime(item.pub_time),
    link: resolveLink(item.source, item.link),
  };
}

function SkeletonCard() {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 animate-pulse">
      <div className="h-4 bg-gray-200 rounded w-3/4 mb-2" />
      <div className="h-3 bg-gray-100 rounded w-full mb-1" />
      <div className="h-3 bg-gray-100 rounded w-2/3 mb-4" />
      <div className="flex justify-between">
        <div className="h-3 bg-gray-100 rounded w-16" />
        <div className="h-3 bg-gray-100 rounded w-16" />
      </div>
    </div>
  );
}

export function NewsPage({ defaultPageSize = 30 }: { defaultPageSize?: number }) {
  const [activeFilter, setActiveFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [pageSize] = useState(defaultPageSize);
  const [items, setItems] = useState<NewsItem[]>([]);
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
      if (filter === 'all') {
        // 全部模式：每源取 ps 条，合并按时间倒序后取第 p 页
        // 这样保证"全部"视图里看到的是跨所有来源的最新内容
        const results = await Promise.allSettled(
          SOURCES.map(src =>
            fetch(`/api/news?source=${encodeURIComponent(src)}&page=1&page_size=${ps}`)
              .then(r => r.json())
              .then(j => j.success ? j.data as PagedResponse : null)
              .catch(() => null)
          )
        );
        const allItems: RawNewsItem[] = [];
        let totalSum = 0;
        for (const r of results) {
          if (r.status === 'fulfilled' && r.value) {
            allItems.push(...r.value.items);
            totalSum += r.value.total;
          }
        }
        allItems.sort((a, b) =>
          new Date(b.pub_time.replace(' ', 'T')).getTime() -
          new Date(a.pub_time.replace(' ', 'T')).getTime()
        );
        // 前端分页：从合并结果里切出当前页
        const start = (p - 1) * ps;
        setItems(allItems.slice(start, start + ps).map(mapItem));
        setTotal(totalSum);
      } else {
        const res = await fetch(
          `/api/news?source=${encodeURIComponent(filter as Source)}&page=${p}&page_size=${ps}`
        );
        const json = await res.json();
        if (json.success) {
          const data = json.data as PagedResponse;
          setItems(data.items.map(mapItem));
          setTotal(data.total);
        }
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
        title="📰 资讯速递"
        subtitle="实时财经新闻"
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
          {items.map(item => <NewsCard key={item.id} news={item} />)}
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
