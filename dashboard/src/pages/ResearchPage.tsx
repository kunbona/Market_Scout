import { useState, useEffect } from 'react';
import { apiFetch } from '../lib/api';
import { ResearchList } from '../components/ResearchList';
import { FilterTabs } from '../components/FilterTabs';
import { TabHeader } from '../components/TabHeader';

interface RawResearchItem {
  id: number | string;
  title: string;
  stock_code?: string;
  stock_name?: string;
  org_name?: string;
  publish_date?: string;
  rating?: string;
  aim_price?: string | number;
  report_url?: string;
  qtype: number;
}

interface ResearchItem {
  id: string;
  title: string;
  category: string;
  date: string;
  summary: string;
  institution?: string;
  rating?: string;
  link?: string;
}

const QTYPE_LABEL: Record<number, string> = {
  0: '个股研究',
  1: '行业研究',
  2: '宏观研究',
  3: '策略研究',
};

function mapResearchItem(item: RawResearchItem): ResearchItem {
  return {
    id: String(item.id),
    title: item.title,
    category: QTYPE_LABEL[item.qtype] ?? '其他',
    date: item.publish_date ?? '',
    summary: item.stock_name
      ? `${item.stock_name}${item.aim_price ? ' 目标价:' + item.aim_price : ''}`
      : '',
    institution: item.org_name ?? '',
    rating: item.rating ?? '',
    link: item.report_url
      ? `/api/research/pdf?url=${encodeURIComponent(item.report_url)}`
      : undefined,
  };
}

const TABS = [
  { id: 'all', label: '全部' },
  { id: '0', label: '个股研究' },
  { id: '1', label: '行业研究' },
  { id: '2', label: '宏观研究' },
  { id: '3', label: '策略研究' },
];

function SkeletonCard() {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 animate-pulse">
      <div className="flex gap-2 mb-3">
        <div className="h-5 bg-gray-200 rounded w-16" />
        <div className="h-5 bg-gray-200 rounded w-10" />
      </div>
      <div className="h-4 bg-gray-200 rounded w-full mb-1" />
      <div className="h-4 bg-gray-100 rounded w-4/5 mb-3" />
      <div className="h-3 bg-gray-100 rounded w-2/3 mb-4" />
      <div className="flex justify-between">
        <div className="h-3 bg-gray-100 rounded w-20" />
        <div className="h-3 bg-gray-100 rounded w-20" />
      </div>
    </div>
  );
}

export function ResearchPage() {
  const [activeFilter, setActiveFilter] = useState('all');
  const [items, setItems] = useState<ResearchItem[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    setItems([]);

    const fetchResearch = async () => {
      try {
        if (activeFilter === 'all') {
          const results = await Promise.allSettled(
            [0, 1, 2, 3].map((qtype) =>
              apiFetch<RawResearchItem[]>(`/api/research?qtype=${qtype}&limit=200`)
            )
          );
          const merged: RawResearchItem[] = [];
          for (const r of results) {
            if (r.status === 'fulfilled') merged.push(...r.value);
          }
          // Deduplicate by id in case the same report appears under multiple qtypes
          const seen = new Set<string>();
          const deduped = merged.filter((item) => {
            const key = String(item.id);
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
          });
          setItems(deduped.map(mapResearchItem));
        } else {
          const data = await apiFetch<RawResearchItem[]>(
            `/api/research?qtype=${activeFilter}&limit=200`
          );
          setItems(data.map(mapResearchItem));
        }
      } catch {
        setItems([]);
      } finally {
        setLoading(false);
      }
    };

    fetchResearch();
  }, [activeFilter]);

  const tabsWithCount = TABS.map((t) =>
    t.id === activeFilter ? { ...t, count: items.length } : t
  );

  return (
    <div>
      <TabHeader
        title="📑 研究报告"
        subtitle="券商研报与行业分析"
        count={loading ? undefined : items.length}
      />
      <FilterTabs
        tabs={tabsWithCount}
        activeTab={activeFilter}
        onTabChange={setActiveFilter}
      />
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : (
        <ResearchList items={items} />
      )}
    </div>
  );
}
