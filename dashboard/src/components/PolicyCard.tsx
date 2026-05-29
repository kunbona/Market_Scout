import { ExternalLink } from 'lucide-react';

interface PolicyItem {
  id: string;
  title: string;
  content: string;
  department: string;
  date: string;
  category: string;
  link?: string;
}

const SOURCE_COLORS: Record<string, string> = {
  '巨潮公告':   '#0ea5e9',
  '财新':       '#a78bfa',
  '发改委':     '#f97316',
  '证监会':     '#ef4444',
  '上交所问询': '#3b82f6',
  '深交所问询': '#06b6d4',
  '深交所公告': '#22c55e',
};

export function PolicyCard({ policy }: { policy: PolicyItem }) {
  const color = SOURCE_COLORS[policy.category] ?? '#9ca3af';
  const Tag = policy.link ? 'a' : 'div';
  const linkProps = policy.link
    ? { href: policy.link, target: '_blank', rel: 'noopener noreferrer' }
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
          <h3 className="text-sm font-medium text-gray-900 line-clamp-2 leading-snug
                         group-hover:text-blue-600 transition-colors duration-150">
            {policy.title}
          </h3>
        </div>
        {policy.link && (
          <ExternalLink className="w-3.5 h-3.5 text-gray-300 flex-shrink-0 mt-0.5
                                   group-hover:text-blue-400 transition-colors duration-150 opacity-0 group-hover:opacity-100" />
        )}
      </div>

      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold tracking-wide" style={{ color }}>{policy.category}</span>
        <span className="text-xs text-gray-400 font-mono">{policy.date}</span>
      </div>
    </Tag>
  );
}
