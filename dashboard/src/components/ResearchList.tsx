import { ExternalLink, Clock } from 'lucide-react';

interface ResearchItem {
  id: string;
  title: string;
  category: string;
  type?: string;
  date: string;
  summary: string;
  institution?: string;
  rating?: string;
  link?: string;
}

export function ResearchList({ items }: { items: ResearchItem[] }) {
  const categoryColors: Record<string, string> = {
    '个股研究': 'bg-blue-100 text-blue-700',
    '行业研究': 'bg-green-100 text-green-700',
    '宏观研究': 'bg-purple-100 text-purple-700',
    '策略研究': 'bg-orange-100 text-orange-700',
  };

  const ratingColors: Record<string, string> = {
    '买入': 'bg-red-50 text-red-600',
    '增持': 'bg-orange-50 text-orange-600',
    '持有': 'bg-gray-50 text-gray-500',
    '推荐': 'bg-green-50 text-green-600',
    '减持': 'bg-yellow-50 text-yellow-600',
    '卖出': 'bg-gray-50 text-gray-400',
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {items.map((item) => {
        const Tag = item.link ? 'a' : 'div';
        const linkProps = item.link
          ? { href: item.link, target: '_blank', rel: 'noopener noreferrer' }
          : {};
        return (
        <Tag
          key={item.id}
          {...linkProps}
          className="block bg-white rounded-xl border border-gray-100 p-4 group card-hover"
        >
          <div className="flex items-start justify-between gap-3 mb-2">
            <div className="flex items-center gap-2">
              <span className={`inline-block px-2 py-0.5 text-xs rounded ${categoryColors[item.category] || 'bg-gray-100 text-gray-600'}`}>
                {item.category}
              </span>
              {item.rating && (
                <span className={`inline-block px-2 py-0.5 text-xs rounded font-medium ${ratingColors[item.rating] || 'bg-gray-50 text-gray-500'}`}>
                  {item.rating}
                </span>
              )}
            </div>
            {item.link
              ? <ExternalLink className="w-3 h-3 text-gray-400 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
              : <ExternalLink className="w-3 h-3 text-gray-200 flex-shrink-0" />
            }
          </div>

          <h3 className="text-sm text-gray-900 mb-2 group-hover:text-blue-600 transition-colors line-clamp-2">
            {item.title}
          </h3>

          <p className="text-xs text-gray-600 mb-3 line-clamp-2">{item.summary}</p>

          <div className="flex items-center justify-between text-xs">
            <span className="text-gray-500">{item.institution || '研究机构'}</span>
            <span className="text-gray-400 flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {item.date}
            </span>
          </div>
        </Tag>
        );
      })}
    </div>
  );
}
