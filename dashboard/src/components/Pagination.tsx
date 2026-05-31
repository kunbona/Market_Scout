import { ChevronLeft, ChevronRight, CornerDownRight } from 'lucide-react';
import { useState, useRef } from 'react';

interface PaginationProps {
  page: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}

export function Pagination({ page, total, pageSize, onPageChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const [jumpInput, setJumpInput] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const pages: (number | '...')[] = [];
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= page - 2 && i <= page + 2)) {
      pages.push(i);
    } else if (pages[pages.length - 1] !== '...') {
      pages.push('...');
    }
  }

  const handlePageChange = (p: number) => {
    onPageChange(p);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleJump = () => {
    const n = parseInt(jumpInput, 10);
    if (!isNaN(n) && n >= 1 && n <= totalPages) {
      handlePageChange(n);
      setJumpInput('');
      inputRef.current?.blur();
    }
  };

  return (
    <div className="flex items-center justify-between mt-6 pt-4 border-t border-gray-100 gap-4 flex-wrap">
      {/* 左：总条数 */}
      <span className="text-xs text-gray-400 whitespace-nowrap">
        共 {total} 条，每页 {pageSize} 条
        {totalPages > 1 && `，第 ${page}/${totalPages} 页`}
      </span>

      {totalPages > 1 && (
        <div className="flex items-center gap-2 flex-wrap">
          {/* 页码导航 */}
          <div className="flex items-center gap-1">
            <button
              onClick={() => handlePageChange(page - 1)}
              disabled={page === 1}
              aria-label="上一页"
              className="page-btn p-1.5 rounded-lg border border-gray-200 text-gray-500 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <ChevronLeft className="w-4 h-4" aria-hidden="true" />
            </button>

            {pages.map((p, i) =>
              p === '...'
                ? <span key={`e-${i}`} className="px-1.5 text-gray-400 text-sm select-none" aria-hidden="true">…</span>
                : <button
                    key={p}
                    onClick={() => handlePageChange(p as number)}
                    aria-current={p === page ? 'page' : undefined}
                    aria-label={p !== page ? `第 ${p} 页` : undefined}
                    className={`page-btn min-w-[32px] h-8 px-2 rounded-lg text-sm font-medium ${
                      p === page
                        ? 'tab-active-glow accent-solid'
                        : 'border border-gray-200 text-gray-600'
                    }`}
                  >{p}</button>
            )}

            <button
              onClick={() => handlePageChange(page + 1)}
              disabled={page === totalPages}
              aria-label="下一页"
              className="page-btn p-1.5 rounded-lg border border-gray-200 text-gray-500 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <ChevronRight className="w-4 h-4" aria-hidden="true" />
            </button>
          </div>

          {/* 跳转输入 */}
          <div className="flex items-center gap-1.5 text-xs text-gray-400">
            <span className="whitespace-nowrap">跳至</span>
            <input
              ref={inputRef}
              id="page-jump-input"
              aria-label="跳转到页码"
              type="number"
              min={1}
              max={totalPages}
              value={jumpInput}
              onChange={e => setJumpInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleJump()}
              placeholder={String(page)}
              className="w-14 h-8 px-2 text-sm text-center border border-gray-200 rounded-lg focus:outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 text-gray-700 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            />
            <span className="whitespace-nowrap text-gray-400">/ {totalPages} 页</span>
            <button
              onClick={handleJump}
              disabled={!jumpInput || isNaN(parseInt(jumpInput, 10))}
              className="flex items-center gap-0.5 h-8 px-2.5 rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-50 hover:border-gray-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed text-xs"
            >
              <CornerDownRight className="w-3.5 h-3.5" aria-hidden="true" />
              跳转
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
