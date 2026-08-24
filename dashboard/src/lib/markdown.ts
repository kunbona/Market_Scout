// 轻量 markdown 渲染（无依赖）：标题/列表/加粗/引用/链接/行内代码/分隔线
// 供智堡页、关注股池情报弹窗等 AI 分析结果展示复用

function inlineMd(s: string): string {
  return s
    .replace(/\*\*([^*]+)\*\*/g, (_m, c) => `<strong>${c}</strong>`)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_m, t, u) =>
      `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`)
    .replace(/`([^`]+)`/g, (_m, c) => `<code>${c}</code>`);
}

export function renderMarkdown(md: string): string {
  if (!md) return '';
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const lines = md.split('\n');
  const out: string[] = [];
  let inList = false;
  let listType: 'ul' | 'ol' | null = null;
  const closeList = () => {
    if (inList) { out.push(listType === 'ul' ? '</ul>' : '</ol>'); inList = false; listType = null; }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (line.trim().startsWith('```')) { closeList(); continue; }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      closeList();
      const lv = h[1].length;
      const cls = lv === 1 ? 'text-xl font-bold text-gray-900 mt-5 mb-2'
        : lv === 2 ? 'text-lg font-semibold text-gray-900 mt-4 mb-2'
        : lv === 3 ? 'text-base font-semibold text-gray-800 mt-3 mb-1.5'
        : 'text-sm font-semibold text-gray-800 mt-3 mb-1';
      out.push(`<h${lv} class="${cls}">${inlineMd(esc(h[2]))}</h${lv}>`);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { closeList(); out.push('<hr class="border-gray-100 my-4" />'); continue; }
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) {
      if (!inList || listType !== 'ul') { closeList(); out.push('<ul class="list-disc pl-5 my-2 space-y-1">'); inList = true; listType = 'ul'; }
      out.push(`<li class="text-sm text-gray-700 leading-relaxed">${inlineMd(esc(ul[1]))}</li>`);
      continue;
    }
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ol) {
      if (!inList || listType !== 'ol') { closeList(); out.push('<ol class="list-decimal pl-5 my-2 space-y-1">'); inList = true; listType = 'ol'; }
      out.push(`<li class="text-sm text-gray-700 leading-relaxed">${inlineMd(esc(ol[1]))}</li>`);
      continue;
    }
    const q = line.match(/^\s*>\s?(.*)$/);
    if (q) { closeList(); out.push(`<blockquote class="border-l-2 border-gray-200 pl-3 text-gray-500 text-sm italic my-2">${inlineMd(esc(q[1]))}</blockquote>`); continue; }
    if (!line.trim()) { closeList(); continue; }
    closeList();
    out.push(`<p class="text-sm text-gray-700 leading-relaxed my-1.5">${inlineMd(esc(line))}</p>`);
  }
  closeList();
  return out.join('');
}
