import { useRef } from 'react';

interface FilterTab {
  id: string;
  label: string;
  emoji?: string;
  count?: number;
}

interface FilterTabsProps {
  tabs: FilterTab[];
  activeTab: string;
  onTabChange: (tabId: string) => void;
}

export function FilterTabs({ tabs, activeTab, onTabChange }: FilterTabsProps) {
  const tabsRef = useRef<HTMLDivElement>(null);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const tabEls = Array.from(
      tabsRef.current?.querySelectorAll<HTMLButtonElement>('[role=tab]') ?? []
    );
    const next =
      e.key === 'ArrowRight'
        ? (currentIndex + 1) % tabEls.length
        : (currentIndex - 1 + tabEls.length) % tabEls.length;
    tabEls[next]?.focus();
  };

  return (
    <div
      ref={tabsRef}
      role="tablist"
      aria-label="视图切换"
      className="flex items-center gap-2 mb-6 overflow-x-auto pb-2 scrollbar-none"
    >
      {tabs.map((tab, index) => {
        const isActive = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            onClick={() => onTabChange(tab.id)}
            onKeyDown={(e) => handleKeyDown(e, index)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm whitespace-nowrap font-medium select-none
              ${isActive
                ? 'tab-active-glow accent-solid'
                : 'bg-white text-gray-600 border border-gray-200 hover:border-gray-300 hover:text-gray-900 hover:bg-gray-50 shadow-[0_1px_3px_rgba(0,0,0,.06)]'
              }`}
          >
            {tab.emoji && <span className="text-base leading-none">{tab.emoji}</span>}
            <span>{tab.label}</span>
            {tab.count !== undefined && (
              <span className={`px-1.5 py-0.5 rounded-full text-xs font-semibold leading-none ${
                isActive ? 'bg-white/25 text-white' : 'bg-gray-100 text-gray-500'
              }`}>
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
