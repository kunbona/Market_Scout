import { Clock, BarChart2 } from 'lucide-react';

interface TabHeaderProps {
  title: string;
  subtitle?: string;
  count?: number;
  lastUpdate?: string;
}

export function TabHeader({ title, subtitle, count, lastUpdate }: TabHeaderProps) {
  return (
    <div className="mb-7 pb-5 border-b border-gray-100">
      <h2 className="text-2xl font-bold text-gray-900 tracking-tight mb-1.5">{title}</h2>
      {(subtitle || count !== undefined || lastUpdate) && (
        <div className="flex items-center gap-4 text-xs text-gray-400 flex-wrap">
          {subtitle && <span className="text-gray-500">{subtitle}</span>}
          {count !== undefined && (
            <span className="flex items-center gap-1 bg-gray-100 px-2 py-0.5 rounded-full">
              <BarChart2 className="w-3 h-3" />
              {count} 条
            </span>
          )}
          {lastUpdate && (
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              更新于 {lastUpdate}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
