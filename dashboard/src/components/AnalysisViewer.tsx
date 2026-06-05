import { useRef, useState, useEffect, useCallback } from 'react';
import { BarChart2, Loader2 } from 'lucide-react';

export interface AnalysisTab {
  name: string;
  html: string;
}

interface AnalysisViewerProps {
  tabs?: AnalysisTab[];
  htmlContent?: string;
  frameHeight?: number | '100%';
  title?: string;
}

const RESIZE_SCRIPT = `
<script>
(function () {
  function resizeAll() {
    if (window.echarts) {
      try { echarts.getInstanceByDom && document.querySelectorAll('[_echarts_instance_]').forEach(function(el){
        var inst = echarts.getInstanceByDom(el);
        if (inst) inst.resize();
      }); } catch(e) {}
    }
  }
  window.addEventListener('load', function() { setTimeout(resizeAll, 200); });
  window.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'tab-shown') { setTimeout(resizeAll, 50); }
  });
})();
<\/script>`;

const THEME_WRAPPER_CSS = `
:root{--bg:#ffffff;--bg-card:#faf9ff;--primary:#7c3aed;--primary-light:#a78bfa;
--primary-muted:#ede9fe;--text-primary:#1e1b4b;--text-secondary:#4c1d95;
--text-muted:#6b7280;--border:#ddd6fe;--border-light:#ede9fe;
--success:#16a34a;--warning:#d97706;--danger:#dc2626;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text-primary);
font-family:'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;
padding:20px 24px;line-height:1.6}
.section-title{font-size:12px;font-weight:700;color:var(--primary);
text-transform:uppercase;letter-spacing:.08em;margin:20px 0 10px;
display:flex;align-items:center;gap:8px}
.section-title::before{content:'';display:inline-block;width:3px;height:13px;
background:var(--primary);border-radius:2px}
.card{background:var(--bg-card);border:1px solid var(--border);
border-radius:10px;padding:14px 18px;margin-bottom:16px}
.tag{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;margin-right:6px}
.tag-bull{background:rgba(22,163,74,.1);color:var(--success);border:1px solid rgba(22,163,74,.25)}
.tag-bear{background:rgba(220,38,38,.1);color:var(--danger);border:1px solid rgba(220,38,38,.25)}
.tag-info{background:var(--primary-muted);color:var(--primary);border:1px solid var(--border)}
.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.chart-box{background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:14px}
.chart-label{font-size:11px;color:var(--text-muted);margin-bottom:8px;font-weight:500}
.kv-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-bottom:16px}
.kv-card{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:10px 14px}
.kv-label{font-size:11px;color:var(--text-muted)}
.kv-value{font-size:20px;font-weight:700;color:var(--primary);margin-top:2px}
.kv-sub{font-size:10px;color:var(--text-muted);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:6px 8px;border-bottom:2px solid var(--border);color:var(--primary);font-size:11px}
td{padding:8px;border-bottom:1px solid var(--border-light);color:var(--text-secondary)}
`;

function wrapPlainText(html: string): string {
  const trimmed = html.trimStart();
  if (trimmed.toLowerCase().startsWith('<!')) return html;

  const hasHtmlTags = /<[a-z][^>]*>/i.test(trimmed);
  if (hasHtmlTags) {
    return `<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8">
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"><\/script>
<style>${THEME_WRAPPER_CSS}</style></head>
<body>${html}</body></html>`;
  }

  return `<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8">
<style>body{font-family:'PingFang SC',sans-serif;padding:20px 24px;color:#1e1b4b;line-height:1.7;background:#fff;}
pre{white-space:pre-wrap;word-break:break-all;font-size:13px;}</style></head>
<body><pre>${html.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</pre></body></html>`;
}

const OVERRIDE_CSS = `
<style>
html, body { height: auto !important; overflow-y: visible !important; }
*:not(canvas):not(svg) { max-height: none !important; }
*:not(canvas):not(svg)[style*="position:sticky"],
*:not(canvas):not(svg)[style*="position: sticky"],
*:not(canvas):not(svg)[style*="position:fixed"],
*:not(canvas):not(svg)[style*="position: fixed"] { display: none !important; }
</style>
<script>
(function(){
  function removeFixedOverlays(){
    document.querySelectorAll('*').forEach(function(el){
      if(el.tagName==='CANVAS'||el.tagName==='SVG') return;
      var cs=window.getComputedStyle(el);
      var pos=cs.position;
      if(pos!=='fixed'&&pos!=='sticky') return;
      var bg=cs.background||'';
      if(bg.indexOf('gradient')>=0){ el.style.display='none'; return; }
      var zi=parseInt(cs.zIndex)||0;
      var id=(el.id||'').toLowerCase();
      var cls=(el.className||'').toLowerCase();
      if(zi>50 && id.indexOf('chart')<0 && cls.indexOf('chart')<0){
        el.style.display='none';
      }
    });
  }
  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',removeFixedOverlays);
  } else { removeFixedOverlays(); }
})();
<\/script>`;

function injectScript(html: string): string {
  const normalized = wrapPlainText(html);
  const withCss = normalized.includes('</head>')
    ? normalized.replace('</head>', OVERRIDE_CSS + '</head>')
    : normalized;
  if (withCss.includes('</body>')) return withCss.replace('</body>', RESIZE_SCRIPT + '</body>');
  return withCss + RESIZE_SCRIPT;
}

function IframePanel({
  html,
  title,
  height,
  active,
}: {
  html: string;
  title: string;
  height: number | '100%';
  active: boolean;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoaded(false);
    setLoading(true);
  }, [html]);

  useEffect(() => {
    if (active && loaded && iframeRef.current?.contentWindow) {
      iframeRef.current.contentWindow.postMessage({ type: 'tab-shown' }, '*');
    }
  }, [active, loaded]);

  const handleLoad = useCallback(() => {
    setLoading(false);
    setLoaded(true);
    setTimeout(() => {
      iframeRef.current?.contentWindow?.postMessage({ type: 'tab-shown' }, '*');
    }, 100);
  }, []);

  return (
    <div className="relative" style={{ height }}>
      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/80 backdrop-blur-sm rounded-b-xl">
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="w-6 h-6 text-indigo-500 animate-spin" />
            <p className="text-xs text-gray-500">图表渲染中…</p>
          </div>
        </div>
      )}
      <iframe
        ref={iframeRef}
        srcDoc={injectScript(html)}
        sandbox="allow-scripts"
        title={title}
        onLoad={handleLoad}
        style={{
          width: '100%',
          height: '100%',
          border: 'none',
          display: 'block',
          opacity: loaded ? 1 : 0,
          transition: 'opacity 0.25s ease',
        }}
      />
    </div>
  );
}

export function AnalysisViewer({
  tabs,
  htmlContent,
  frameHeight = 600,
  title = '分析结果',
}: AnalysisViewerProps) {
  const normalised: AnalysisTab[] = tabs && tabs.length > 0
    ? tabs
    : htmlContent
      ? [{ name: title, html: htmlContent }]
      : [];

  const [activeIdx, setActiveIdx] = useState(0);

  useEffect(() => { setActiveIdx(0); }, [normalised.length]);

  if (normalised.length === 0) {
    return (
      <div
        className="flex flex-col items-center justify-center rounded-xl border border-dashed border-gray-300 bg-gray-50"
        style={{ minHeight: frameHeight }}
      >
        <BarChart2 className="w-10 h-10 text-gray-300 mb-3" />
        <p className="text-sm text-gray-400">暂无图表分析内容</p>
        <p className="text-xs text-gray-300 mt-1">LLM 输出后将在此处渲染</p>
      </div>
    );
  }

  const singleTab = normalised.length === 1;
  const fullHeight = frameHeight === '100%';

  return (
    <div
      className="w-full rounded-xl border border-gray-200 shadow-sm overflow-hidden flex flex-col"
      style={fullHeight ? { height: '100%' } : undefined}
    >
      {!singleTab && (
        <div className="flex items-center gap-0 border-b border-gray-200 bg-gray-50 overflow-x-auto overflow-y-hidden flex-shrink-0 scrollbar-none [&::-webkit-scrollbar]:hidden">
          {normalised.map((tab, i) => (
            <button
              key={i}
              onClick={() => setActiveIdx(i)}
              className={`flex-shrink-0 px-4 py-2.5 text-xs font-medium whitespace-nowrap transition-colors border-b-2 -mb-px ${
                i === activeIdx
                  ? 'border-violet-500 text-violet-700 bg-white'
                  : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-100'
              }`}
            >
              {tab.name}
            </button>
          ))}
        </div>
      )}

      <div
        className={fullHeight ? 'flex-1 min-h-0 flex flex-col' : undefined}
        style={fullHeight ? { position: 'relative' } : undefined}
      >
        {normalised.map((tab, i) => (
          <div
            key={i}
            style={{
              display: i === activeIdx ? (fullHeight ? 'flex' : 'block') : 'none',
              flexDirection: fullHeight ? 'column' : undefined,
              height: fullHeight ? '100%' : undefined,
              flex: fullHeight ? '1 1 0' : undefined,
              minHeight: fullHeight ? 0 : undefined,
            }}
          >
            <div style={{ flex: fullHeight ? '1 1 0' : undefined, minHeight: fullHeight ? 0 : undefined, height: fullHeight ? undefined : (frameHeight as number) }}>
              <IframePanel
                html={tab.html}
                title={tab.name}
                height={fullHeight ? '100%' : (frameHeight as number)}
                active={i === activeIdx}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
