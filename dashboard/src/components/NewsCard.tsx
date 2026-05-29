import { ExternalLink } from 'lucide-react';

interface NewsItem {
  id: string;
  title: string;
  summary: string;
  source: string;
  time: string;
  link?: string;
}

const SOURCE_COLORS: Record<string, string> = {
  '财联社':    '#ef4444',
  '金十数据':  '#f59e0b',
  '格隆汇':   '#06b6d4',
  '东方财富':  '#f97316',
  '同花顺':   '#22c55e',
  '第一财经':  '#3b82f6',
  '华尔街见闻': '#a78bfa',
};

export function NewsCard({ news }: { news: NewsItem }) {
  const color = SOURCE_COLORS[news.source] ?? '#9ca3af';
  const Tag = news.link ? 'a' : 'div';
  const linkProps = news.link
    ? { href: news.link, target: '_blank', rel: 'noopener noreferrer' }
    : {};

  return (
    <Tag
      {...linkProps}
      className="block bg-white rounded-xl border border-gray-100 p-4 group card-hover"
      style={{
        borderLeft: `3px solid ${color}`,
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      <div className="flex items-start gap-3 mb-3">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-medium text-gray-900 mb-1.5 line-clamp-2 leading-snug
                         group-hover:text-blue-600 transition-colors duration-150">
            {news.title}
          </h3>
          {news.summary && (
            <p className="text-xs text-gray-400 line-clamp-2 leading-relaxed">{news.summary}</p>
          )}
        </div>
        {news.link && (
          <ExternalLink className="w-3.5 h-3.5 text-gray-300 flex-shrink-0 mt-0.5
                                   group-hover:text-blue-400 transition-colors duration-150 opacity-0 group-hover:opacity-100" />
        )}
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold tracking-wide" style={{ color }}>{news.source}</span>
        <span className="text-xs text-gray-400 font-mono">{news.time}</span>
      </div>
    </Tag>
  );
}
