"""
research_board — 双层分析 Pipeline

架构：
  1. Claude（supervisor）读取研报摘要，拆解细分模块列表 + 动态扩展维度
  2. Kimi（executor，每维度独立）深研每个模块，输出白紫主题 ECharts HTML
  3. Claude 评审每个 HTML tab，不通过给修改意见，Kimi 重改（最多 MAX_REVIEW_ROUNDS 轮）
  4. Claude 生成研究背景 tab（主题科普 / 研报数据来源 / 分析框架说明）
  5. Claude 生成总览 tab（产业全景 / 核心指标 / BOM 成本面积图 / 产业里程碑）
  6. 所有 tab 存入 rb_result.summary_json = {"tabs": [{"name": ..., "html": ...}]}

Claude 走 ANTHROPIC_AUTH_TOKEN（OpenRouter 代理），model = ANTHROPIC_MODEL 或 claude-sonnet-4-6。
Kimi 走 codex exec --profile research（已有 llm_runner._call_llm）。
"""

import json
import logging
import os
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from research_board.rb_fetcher import get_reports_text_batches
from research_board.rb_storage import (
    get_project,
    get_rb_reports,
    update_project_status,
    upsert_rb_analysis,
    upsert_rb_result,
)
from research_board.llm_runner import _call_llm as kimi_call, _extract_json

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────────────────

MAX_REVIEW_ROUNDS = 2   # Claude 对 Kimi HTML 的最多重审次数
MAX_KIMI_WORKERS  = 3   # 各维度并发 Kimi 数
CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "anthropic/claude-sonnet-4.6")

# ── 主题 Token（单一来源，前端 THEME_TOKENS 保持同步） ────────────────────────

THEME_CSS = """
:root {
  --bg: #ffffff;
  --bg-card: #faf9ff;
  --bg-card-alt: #f5f3ff;
  --primary: #7c3aed;
  --primary-light: #a78bfa;
  --primary-muted: #ede9fe;
  --accent: #6d28d9;
  --text-primary: #1e1b4b;
  --text-secondary: #4c1d95;
  --text-muted: #6b7280;
  --border: #ddd6fe;
  --border-light: #ede9fe;
  --success: #16a34a;
  --warning: #d97706;
  --danger: #dc2626;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text-primary);
  font-family: 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  padding: 20px 24px;
  line-height: 1.6;
}
.section-title {
  font-size: 12px; font-weight: 700; color: var(--primary);
  text-transform: uppercase; letter-spacing: .08em;
  margin: 20px 0 10px; display: flex; align-items: center; gap: 8px;
}
.section-title::before {
  content: ''; display: inline-block;
  width: 3px; height: 13px; background: var(--primary); border-radius: 2px;
}
.card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 18px; margin-bottom: 16px;
}
.tag { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; margin-right: 6px; }
.tag-bull  { background: rgba(22,163,74,.1);  color: var(--success); border: 1px solid rgba(22,163,74,.25); }
.tag-bear  { background: rgba(220,38,38,.1);  color: var(--danger);  border: 1px solid rgba(220,38,38,.25); }
.tag-info  { background: var(--primary-muted); color: var(--primary); border: 1px solid var(--border); }
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.chart-box  { background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
.chart-label { font-size: 11px; color: var(--text-muted); margin-bottom: 8px; font-weight: 500; }
.kv-grid { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
.kv-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; flex: 1 1 180px; min-width: 0; }
.kv-label { font-size: 11px; color: var(--text-muted); }
.kv-value { font-size: 20px; font-weight: 700; color: var(--primary); margin-top: 2px; }
.kv-sub   { font-size: 10px; color: var(--text-muted); margin-top: 2px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th { text-align: left; padding: 6px 8px; border-bottom: 2px solid var(--border); color: var(--primary); font-size: 11px; }
td { padding: 8px; border-bottom: 1px solid var(--border-light); color: var(--text-secondary); }
@media(max-width:600px) { .chart-grid { grid-template-columns: 1fr; } }
"""

ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"

REVIEW_CRITERIA = """
评审标准（全部通过才算合格）：
1. 内容深度：有具体数字、比例、时间节点，不是泛泛而谈
2. 数据可信：所有数字必须来自研报原文，不得编造；若某年份/字段数据研报中不存在，必须省略该数据点或标注"数据缺失"，不得填写 null 或 0
3. 可视化质量：ECharts 图表有实际数据，不是占位符
4. 主题适配：使用白底紫色主题（--primary: #7c3aed），不使用深色背景
5. 无语法错误：HTML/JS 代码可正常运行；HTML 内容不得被截断，所有表格的 tbody 必须有完整数据行
6. 图表初始化完整：页面中每个 <div id="chart..."> 容器必须有对应的 echarts.init() 调用；若发现图表容器数量 > echarts.init() 调用数量，判定为不合格
7. 无高度截断：body 标签、任何包裹容器（.page-wrapper / .main-wrap / .container 等）均不得设置 height 或 max-height 固定值；页面高度必须由内容自然撑开
8. 无装饰性遮罩：不得添加 position:sticky/fixed 的渐变遮罩层（如底部 linear-gradient 淡出效果）；不得添加 position:fixed 的侧边导航浮层——这些元素在 iframe 中会遮挡内容
9. KPI卡片布局：.kv-grid 必须用 display:flex + flex-wrap:wrap，.kv-card 必须有 flex:1 1 180px；禁止 display:grid 固定列数；禁止任何卡片独占整行（grid-column:1/-1 或 width:100%）
"""

# ── 快速 HTML 预检查（Python 侧，无需 Claude） ────────────────────────────────

import re as _re


def _quick_html_check(html: str) -> list[str]:
    """Fast pre-review: returns list of critical issues found."""
    issues = []
    # Check HTML is a complete document (not truncated mid-content)
    if '<!doctype' not in html.lower() and '<html' not in html.lower():
        issues.append("HTML 输出不完整（缺少 <!DOCTYPE html> 和 <html> 标签，内容被截断）；请重新生成完整 HTML 文档")
        return issues  # no point checking further on a fragment
    # Count chart divs vs echarts.init calls
    chart_divs = len(_re.findall(r'<div[^>]+id=["\'][^"\']*chart[^"\']*["\']', html, _re.IGNORECASE))
    init_calls = len(_re.findall(r'echarts\.init\s*\(', html))
    if chart_divs > 0 and init_calls == 0:
        issues.append(f"发现 {chart_divs} 个图表容器但没有 echarts.init() 调用，图表将全部显示为空白")
    elif chart_divs > init_calls + 1:
        issues.append(f"图表容器 {chart_divs} 个但 echarts.init() 只有 {init_calls} 次，部分图表将显示为空白")
    # Check DOCTYPE
    if '<!doctype' not in html.lower():
        issues.append("缺少 <!DOCTYPE html> 声明")
    # Check dark background
    if '#0d1117' in html or 'background: #1' in html or 'background:#1' in html:
        issues.append("使用了深色背景，应改为白色背景")
    # Check clipping: max-height or fixed height on body / wrapper elements
    body_height = _re.search(r'body\s*\{[^}]*\bheight\s*:\s*\d+px', html, _re.IGNORECASE | _re.DOTALL)
    if body_height:
        issues.append("body 设置了固定 height，会截断内容；应移除 body 的 height，让内容自然撑开")
    wrapper_clip = _re.findall(
        r'(?:page-wrapper|main-wrap|container|wrapper)[^{]*\{[^}]*max-height\s*:[^;]+;[^}]*overflow(?:-y)?\s*:\s*(?:auto|scroll)',
        html, _re.IGNORECASE | _re.DOTALL,
    )
    if wrapper_clip:
        issues.append("包裹容器设置了 max-height + overflow:auto，会导致内容被截断；应移除 max-height，让内容自然撑开")
    # Check for decorative overlay/fixed nav that clips content in iframe
    fade_overlay = _re.search(
        r'position\s*:\s*(?:sticky|fixed)[^}]*linear-gradient[^}]*}',
        html, _re.IGNORECASE | _re.DOTALL,
    )
    if fade_overlay:
        issues.append("存在 position:sticky/fixed 的渐变遮罩层，在 iframe 中会遮挡正文内容；应删除该装饰元素")
    fixed_nav = _re.search(
        r'position\s*:\s*fixed[^}]*(?:right|left)\s*:\s*\d+[^}]*z-index[^}]*}',
        html, _re.IGNORECASE | _re.DOTALL,
    )
    if fixed_nav:
        issues.append("存在 position:fixed 的侧边/浮层导航，在 iframe 中无意义且遮挡内容；应删除该元素")
    # Check null in series data (indicates missing data filled with null)
    null_data = _re.search(r'data\s*:\s*\[[^\]]*\bnull\b', html)
    if null_data:
        issues.append("图表 series data 中含有 null 值，会导致折线图断层；数据缺失时应省略该年份或标注'暂无'")
    # Check KPI grid: display:grid instead of flex
    kv_grid_css = _re.search(r'\.kv-grid\s*\{[^}]*display\s*:\s*grid', html, _re.DOTALL)
    if kv_grid_css:
        issues.append(".kv-grid 使用了 display:grid，应改为 display:flex; flex-wrap:wrap 以确保卡片填满横向空间")
    # Check kv-card lacking flex property
    kv_card_css = _re.search(r'\.kv-card\s*\{([^}]+)\}', html, _re.DOTALL)
    if kv_card_css and 'flex:' not in kv_card_css.group(1) and 'flex :' not in kv_card_css.group(1):
        issues.append(".kv-card 缺少 flex:1 1 180px 属性，卡片无法均匀填满整行")
    # Check any kv-card spanning full row
    full_span = _re.search(r'(?:grid-column\s*:\s*1\s*/\s*-1|width\s*:\s*100%)', html)
    if full_span:
        issues.append("存在卡片独占整行（grid-column:1/-1 或 width:100%），应让所有卡片均等分配宽度")
    # Check for Kimi reasoning/debug text leaking into HTML body
    # Strip script/style blocks first, then check for reasoning patterns in remaining content
    _html_no_script = _re.sub(r'<(?:script|style)[^>]*>[\s\S]*?</(?:script|style)>', '', html, flags=_re.IGNORECASE)
    reasoning_leak = _re.search(
        r'(?:'
        r'\*\*chart-'        # markdown **chart-xxx** debug labels in body
        r'|option\s*=\s*\{'  # raw JS option object outside script tags
        r'|等等[，,。]'       # "等等，" self-correction
        r'|不[，,]我'         # "不，我应该" self-correction
        r'|我应该'            # "我应该" reasoning
        r'|我想展示'          # reasoning
        r'|这不是我想要'      # self-correction
        r')',
        _html_no_script,
    )
    if reasoning_leak:
        issues.append("Kimi 将推理/调试过程（如 **chart-xxx** markdown标签或 option={...} 代码块）混入了 HTML body；必须删除所有非内容文字，body 中只保留用户可见的分析内容")
    # Check truncated table body (tbody exists but has no <td> rows, or has <td> with empty content)
    tbody_empty = _re.search(r'<tbody>\s*</tbody>', html, _re.IGNORECASE)
    if tbody_empty:
        issues.append("表格 tbody 为空，内容未生成；请根据研报数据补全表格行")
    # Check tbody that opens a <td> but content is missing (truncated output)
    tbody_truncated = _re.search(r'<tbody>[\s\S]{0,200}<td>\s*<strong>\s*$', html, _re.IGNORECASE)
    if tbody_truncated:
        issues.append("表格 tbody 内容不完整（HTML 被截断）；请完整输出所有数据行直到 </tbody></table>")
    return issues


# ── Claude API 调用 ───────────────────────────────────────────────────────────

def _claude_call(system: str, user: str, max_tokens: int = 4096) -> str:
    """调用 Claude（通过 Anthropic SDK，走 OpenRouter 代理）。"""
    try:
        import anthropic as ant
    except ImportError:
        raise RuntimeError("pip install anthropic")

    api_key = (
        os.environ.get("ANTHROPIC_AUTH_TOKEN")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")  # fallback: 仅 OPENROUTER_API_KEY 时也能工作
        or ""
    )
    # 若 ANTHROPIC_AUTH_TOKEN 未设置但有 OPENROUTER_API_KEY，自动指向 OpenRouter
    default_base = (
        "https://openrouter.ai/api"
        if (not os.environ.get("ANTHROPIC_AUTH_TOKEN") and not os.environ.get("ANTHROPIC_API_KEY")
            and os.environ.get("OPENROUTER_API_KEY"))
        else "https://api.anthropic.com"
    )
    base_url = os.environ.get("ANTHROPIC_BASE_URL", default_base)

    client = ant.Anthropic(api_key=api_key, base_url=base_url)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text


# ── Phase 1：Claude 拆解细分模块 & 动态维度 ────────────────────────────────

_DECOMPOSE_SYSTEM = """你是顶级A股投资研究团队的首席研究员（Claude supervisor）。
你的职责是：阅读研报摘要，拆解出赛道的细分模块，并为每个模块确定最值得深研的分析维度。
输出 JSON，不要其他文字。"""

_DECOMPOSE_USER_TPL = """项目名称：{project_name}
用户指定的分析维度（基础）：{user_dimensions}

以下是该赛道的研报摘要（元数据 + 部分正文）：
{report_summary}

---
请分析研报内容，输出如下 JSON：
{{
  "sub_modules": ["模块1", "模块2", "模块3", ...],   // 3-8 个细分模块，从研报实际内容提炼
  "extra_dimensions": ["新维度1", "新维度2"],          // 研报中高频出现但用户未指定的维度（0-3 个）
  "final_dimensions": ["最终维度1", "最终维度2", ...], // 合并用户维度 + extra_dimensions 去重后的列表
  "rationale": "为什么选这些模块和维度（一段话）"
}}
只输出 JSON。"""


def decompose_project(project_name: str, user_dimensions: list[str],
                      report_batches: list[str]) -> dict:
    """Claude 拆解细分模块 + 动态扩展维度。"""
    # 用前 2 批摘要（避免超长）
    summary = "\n\n---\n\n".join(report_batches[:2])[:20000]
    user_msg = _DECOMPOSE_USER_TPL.format(
        project_name=project_name,
        user_dimensions=json.dumps(user_dimensions, ensure_ascii=False),
        report_summary=summary,
    )
    raw = _claude_call(_DECOMPOSE_SYSTEM, user_msg, max_tokens=1024)
    result = _extract_json(raw)
    if not isinstance(result, dict) or "final_dimensions" not in result:
        # fallback: 使用用户维度
        return {"sub_modules": [], "final_dimensions": user_dimensions, "extra_dimensions": []}
    return result


# ── Phase 2：Kimi 生成每个维度的 HTML tab ─────────────────────────────────

_KIMI_HTML_TPL = """你是专业的A股投资研究员，使用 ECharts + HTML 生成研究分析页面。

## 项目：{project_name}
## 当前分析维度：{dimension}
## 细分模块参考：{sub_modules}

## 研报原文（第{batch_index}/{total_batches}批）
{report_text}

---
## 任务
为"{dimension}"这个维度，生成一个完整的 HTML 分析页面（单个 tab 的内容）。

## 严格要求
1. **只输出 HTML 代码**，从 `<!DOCTYPE html>` 开始到 `</html>` 结束，不要前后加任何说明文字、代码注释、思考过程或调试日志
12. **严禁将思考/调试内容混入 HTML**：你的推理过程、图表调试日志（如 `option = {{...}}`）、自我修正（如"等等，""不，我应该"）、任何非内容文字——都**绝对不能出现在 HTML 的 body 中**；HTML body 只包含对用户可见的分析内容
2. 使用以下 CSS 变量（白底紫色主题，不得改成深色背景）：
{theme_css}
3. 引入 ECharts：`<script src="{echarts_cdn}"></script>`
4. 图表必须有真实数据（来自研报），不得使用占位符
5. 每种图表类型选择最能说明问题的：成本构成 → 面积/饼图；竞争格局 → 雷达/表格；估值 → 柱状+折线；替代风险 → 表格
6. **body 标签和任何包裹容器**均不得设置固定 height 或 max-height；页面高度由内容自然撑开，iframe 会自动滚动
7. **不得添加**：① position:sticky/fixed 的渐变淡出遮罩（如底部 linear-gradient 效果）；② position:fixed 的侧边导航浮层。这些元素在 iframe 中无意义且会遮挡内容
8. 图表 series data 中**不得出现 `null`**；若某年份数据研报中不存在，直接从 xAxis 和 data 数组中省略该年份，不要用 null 占位
9. 所有表格的 tbody **必须有完整数据行**；若研报数据不足，减少行数但不得留空 tbody
10. 加 `window.addEventListener('message', ...)` 监听 `tab-shown` 消息后执行 `chart.resize()`
11. **KPI 卡片布局原则**（重要）：
    - `.kv-grid` 必须使用 `display: flex; flex-wrap: wrap; gap: 10px;`，**禁止使用 `display: grid`**
    - `.kv-card` 必须设置 `flex: 1 1 180px; min-width: 0;`，让所有卡片均匀拉伸填满整行横向空间
    - **禁止**给任何 `.kv-card` 设置 `grid-column: 1/-1`、`width: 100%` 或其他独占整行的属性
    - KPI 卡片数量应与页面宽度匹配：横向空间充足时优先横向排列，**不要为了凑数量而让卡片纵向堆叠**
    - 每张卡片的文字内容不宜过长，label ≤ 8字，value ≤ 10字，sub ≤ 18字，避免卡片内换行
"""

def _build_kimi_prompt(project_name: str, dimension: str, sub_modules: list,
                       report_batch: str, batch_idx: int, total_batches: int) -> str:
    return _KIMI_HTML_TPL.format(
        project_name=project_name,
        dimension=dimension,
        sub_modules="、".join(sub_modules) if sub_modules else "（从研报提炼）",
        batch_index=batch_idx,
        total_batches=total_batches,
        report_text=report_batch,
        theme_css=THEME_CSS,
        echarts_cdn=ECHARTS_CDN,
    )


def _extract_html(text: str) -> str:
    """从 LLM 输出中提取 HTML 块，剥离所有 markdown 代码块标记。"""
    import re

    # 1. 优先匹配 ```html ... ``` (含或不含闭合 ```)
    m = re.search(r"```html\s*([\s\S]*?)(?:```|$)", text, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        if "<!doctype" in candidate.lower() or "<html" in candidate.lower():
            return candidate

    # 2. 裸 ``` ... ``` 中含 DOCTYPE
    m2 = re.search(r"```\s*(<!DOCTYPE[\s\S]*?)(?:```|$)", text, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()

    # 3. 直接在原文找 DOCTYPE ... </html>
    idx = text.lower().find("<!doctype")
    if idx >= 0:
        end = text.lower().rfind("</html>")
        if end > idx:
            return text[idx:end + 7].strip()

    # 4. 兜底：找 <html ... </html>
    idx2 = text.lower().find("<html")
    if idx2 >= 0:
        end2 = text.lower().rfind("</html>")
        if end2 > idx2:
            return text[idx2:end2 + 7].strip()

    logger.warning(f"[_extract_html] 未找到 HTML 结构，原文长度={len(text)}，前200字: {text[:200]!r}")
    return text.strip()


# ── Phase 3：Claude 评审 HTML ──────────────────────────────────────────────

_REVIEW_SYSTEM = """你是投研看板质量评审官（Claude supervisor）。
你的职责是审查 Kimi 生成的 HTML 分析页面，判断是否符合标准。
输出 JSON，不要其他文字。"""

_REVIEW_USER_TPL = """## 待评审的 HTML（维度：{dimension}）

{html_content}

---
## 评审标准
{criteria}

## 输出格式
{{
  "passed": true 或 false,
  "score": 0-100,
  "issues": ["问题1", "问题2"],          // passed=false 时列出具体问题
  "suggestions": "给 Kimi 的改进指令"   // passed=false 时提供，直接可粘贴进 prompt
}}
只输出 JSON。"""


def _claude_review(dimension: str, html: str) -> dict:
    """Claude 评审单个 HTML tab，返回评审结果。"""
    user_msg = _REVIEW_USER_TPL.format(
        dimension=dimension,
        html_content=html[:8000],  # 限长避免超 token
        criteria=REVIEW_CRITERIA,
    )
    raw = _claude_call(_REVIEW_SYSTEM, user_msg, max_tokens=512)
    result = _extract_json(raw)
    if not isinstance(result, dict):
        return {"passed": True, "score": 70, "issues": [], "suggestions": ""}
    return result


_REWRITE_TPL = """你是专业的A股投资研究员。

上一版 HTML 存在以下问题：
{issues}

改进建议：
{suggestions}

请修改后重新输出完整 HTML（维度：{dimension}，项目：{project_name}）。
主题要求同上（白底紫色：--primary: #7c3aed），不得改为深色背景。

**必须遵守**：
- 每个 <div id="chart..."> 容器都必须有对应的 echarts.init() 初始化代码，否则图表显示为空白
- 所有表格的 tbody 必须有完整数据行，不得为空也不得截断
- 图表 series data 中不得出现 null，数据缺失时从 xAxis 和 data 数组中省略该年份
- body 和包裹容器不得设置固定 height 或 max-height，页面高度由内容自然撑开
- 不得添加 position:sticky/fixed 的渐变遮罩层或侧边导航浮层
- KPI卡片：.kv-grid 用 `display:flex;flex-wrap:wrap;gap:10px`，.kv-card 用 `flex:1 1 180px;min-width:0`，禁止固定列数 grid 或独占整行
**只输出 HTML 代码，不加任何说明。**

原始研报内容（供参考）：
{report_text}
"""


def generate_tab_with_review(
    project_name: str,
    dimension: str,
    sub_modules: list,
    report_batches: list[str],
    progress_cb=None,
) -> str:
    """
    生成单个维度的 HTML tab，包含 Kimi 生成 + Claude 评审 + Kimi 修改。
    返回最终 HTML 字符串。
    """
    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    # 合并所有批次（限长 25000 chars，避免 Kimi 超时）
    combined = "\n\n---\n\n".join(report_batches)[:25000]
    total = len(report_batches)

    # 初始生成
    _log(f"  Kimi 正在分析【{dimension}】（研报文本 {len(combined)} 字）…")
    kimi_prompt = _build_kimi_prompt(project_name, dimension, sub_modules,
                                      combined, 1, total)
    raw = kimi_call(kimi_prompt)
    html = _extract_html(raw)

    # Claude 评审 + 最多 MAX_REVIEW_ROUNDS 轮修改
    for round_no in range(1, MAX_REVIEW_ROUNDS + 1):
        # Python 侧快速预检，发现致命问题时跳过 Claude 直接触发 Kimi 重写
        quick_issues = _quick_html_check(html)
        if quick_issues:
            _log(f"  快速预检【{dimension}】发现 {len(quick_issues)} 个问题，跳过 Claude 直接重写…")
            issues = quick_issues
            suggestions = "请逐一修正上述问题后重新生成完整 HTML。"
        else:
            _log(f"  Claude 评审【{dimension}】第 {round_no} 轮…")
            review = _claude_review(dimension, html)
            score = review.get('score', '?')
            passed = review.get('passed')
            _log(f"  评审结果：{'通过' if passed else '需修改'} （得分 {score}/100）")

            if review.get("passed", True):
                break

            issues = review.get("issues", [])
            suggestions = review.get("suggestions", "")
            if not issues and not suggestions:
                break

        _log(f"  Kimi 根据评审意见修改【{dimension}】（问题：{'; '.join(issues[:2])}）")
        rewrite_prompt = _REWRITE_TPL.format(
            dimension=dimension,
            project_name=project_name,
            issues="\n".join(f"- {x}" for x in issues),
            suggestions=suggestions,
            report_text=combined[:20000],
        )
        raw2 = kimi_call(rewrite_prompt)
        new_html = _extract_html(raw2)
        if new_html and len(new_html) > 200:
            html = new_html

    return html


# ── Phase 4：Claude 生成研究背景 tab ─────────────────────────────────────────
# 架构：Claude 只生成纯文字内容（主题科普 + 分析框架），Python 硬编码研报表格。
# 原因：让 LLM 渲染表格会导致行数被截断（token 耗尽）或 echarts.init 缺失。
# 表格直接由 Python 用真实数据生成，永远正确、永远完整。

_INTRO_TEXT_SYSTEM = """你是专业的A股投研助手。用中文输出纯文本内容，不要 HTML 标签，不要 markdown 符号（**/#/- 等）。"""

_INTRO_TEXT_TPL = """研究项目：{project_name}
关键词：{keywords}

---
请输出以下两段内容（纯文字，不加任何格式符号）：

【主题科普】
用3-5段通俗语言解释"{project_name}"是什么：定义、核心原理/工艺、在产业链中的位置（上下游）、为什么对投资者重要。
面向有金融背景但不懂技术细节的读者。每段50-80字。

【分析框架】
本次分析覆盖 {dim_count} 个维度，请逐条说明每个维度聚焦什么核心问题（一行一条，格式："维度名：一句话说明"）：
{dimensions_list}
"""

# Rating badge color mapping
_RATING_COLOR = {
    "买入": "#16a34a", "强烈推荐": "#16a34a", "推荐": "#16a34a",
    "增持": "#2563eb", "优于大市": "#2563eb", "跑赢行业": "#2563eb",
    "持有": "#d97706", "中性": "#d97706", "观望": "#d97706",
    "减持": "#dc2626", "卖出": "#dc2626",
}


def _render_report_table(reports: list[dict]) -> str:
    """Python 硬编码渲染研报表格，不依赖 LLM，永远完整。"""
    rows = []
    for i, r in enumerate(reports, 1):
        title = (r.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        org = (r.get("org_name") or "—").replace("<", "&lt;")
        researcher = (r.get("researcher") or "—").replace("<", "&lt;")
        pub_date = r.get("publish_date") or "—"
        rating = (r.get("rating") or "").strip()
        rating_color = _RATING_COLOR.get(rating, "#6b7280")
        rating_html = (
            f'<span style="display:inline-block;padding:1px 7px;border-radius:10px;'
            f'background:{rating_color}22;color:{rating_color};'
            f'border:1px solid {rating_color}55;font-size:10px;font-weight:700;">'
            f'{rating or "—"}</span>'
        ) if rating else "—"
        rows.append(f"""      <tr>
        <td style="color:var(--text-muted);font-size:11px;text-align:center;">{i:02d}</td>
        <td><span style="background:var(--primary-muted);color:var(--primary);padding:1px 7px;border-radius:10px;font-size:11px;font-weight:600;">{org}</span></td>
        <td style="font-size:12px;color:var(--text-secondary);">{researcher}</td>
        <td style="font-size:12px;color:var(--text-muted);">{pub_date}</td>
        <td>{rating_html}</td>
        <td style="font-size:12px;color:var(--text-primary);">{title}</td>
      </tr>""")
    return "\n".join(rows)


def _render_dimension_list(dimensions: list[str], framework_text: str) -> str:
    """将 Claude 输出的维度说明文字渲染为 HTML 列表项。"""
    # Parse "维度名：说明" lines from framework_text
    import re as _re2
    parsed = {}
    for line in framework_text.splitlines():
        line = line.strip().lstrip("•·-–— 　")
        m = _re2.match(r"^(.+?)[：:](.+)$", line)
        if m:
            parsed[m.group(1).strip()] = m.group(2).strip()

    items = []
    for dim in dimensions:
        desc = parsed.get(dim, "")
        # fuzzy match: find key that contains or is contained by dim
        if not desc:
            for k, v in parsed.items():
                if k in dim or dim in k:
                    desc = v
                    break
        items.append(
            f'<div style="display:flex;gap:10px;align-items:baseline;margin-bottom:8px;">'
            f'<span style="flex-shrink:0;background:var(--primary);color:#fff;'
            f'padding:1px 8px;border-radius:10px;font-size:10px;font-weight:700;">{dim}</span>'
            f'<span style="font-size:12px;color:var(--text-secondary);">{desc}</span>'
            f'</div>'
        )
    return "\n".join(items)


def generate_intro_tab(
    project_name: str,
    keywords: list[str],
    dimensions: list[str],
    reports: list[dict],
    progress_cb=None,
) -> str:
    """
    研究背景 tab 生成：
    - Claude 只生成纯文字（主题科普 + 分析框架说明）
    - Python 硬编码渲染研报表格（不依赖 LLM，永远完整）
    - 无 ECharts 图表（研究背景不需要）
    """
    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    _log("Claude 正在生成研究背景文字内容…")

    dimensions_list = "\n".join(f"- {d}" for d in dimensions)
    user_msg = _INTRO_TEXT_TPL.format(
        project_name=project_name,
        keywords="、".join(keywords) if keywords else project_name,
        dim_count=len(dimensions),
        dimensions_list=dimensions_list,
    )
    raw_text = _claude_call(_INTRO_TEXT_SYSTEM, user_msg, max_tokens=3000)

    # Split into 主题科普 and 分析框架 sections
    import re as _re3
    primer_text = ""
    framework_text = ""
    m_primer = _re3.search(r"【主题科普】([\s\S]*?)(?:【分析框架】|$)", raw_text)
    m_framework = _re3.search(r"【分析框架】([\s\S]*?)$", raw_text)
    if m_primer:
        primer_text = m_primer.group(1).strip()
    if m_framework:
        framework_text = m_framework.group(1).strip()

    # Fallback: treat entire text as primer if parsing fails
    if not primer_text:
        primer_text = raw_text.strip()

    # Render primer paragraphs
    paras = [p.strip() for p in _re3.split(r"\n{2,}", primer_text) if p.strip()]
    primer_html = "\n".join(
        f'<p style="margin-bottom:12px;line-height:1.8;color:var(--text-secondary);font-size:13px;">{p}</p>'
        for p in paras
    )

    # Render report table (Python, always complete)
    report_table_rows = _render_report_table(reports)

    # Render dimension framework
    dim_html = _render_dimension_list(dimensions, framework_text)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{project_name} — 研究背景</title>
<style>
{THEME_CSS}
.section-num {{
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 50%;
  background: var(--primary); color: #fff;
  font-size: 12px; font-weight: 700; margin-right: 8px; flex-shrink: 0;
}}
.section-header {{
  display: flex; align-items: center; font-size: 15px; font-weight: 700;
  color: var(--text-primary); margin: 24px 0 6px;
}}
.section-sub {{ font-size: 11px; color: var(--text-muted); margin-bottom: 14px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
th {{ text-align: left; padding: 8px 10px; border-bottom: 2px solid var(--border);
  color: var(--primary); font-size: 11px; font-weight: 700;
  background: var(--primary-muted); }}
td {{ padding: 9px 10px; border-bottom: 1px solid var(--border-light);
  vertical-align: middle; }}
tr:hover td {{ background: var(--border-light); }}
.meta-bar {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 20px;
  padding: 10px 14px; background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 8px; font-size: 11px; color: var(--text-muted); }}
.meta-bar strong {{ color: var(--text-primary); }}
/* Pipeline flow diagram */
.pipeline {{ display: flex; align-items: stretch; gap: 0; flex-wrap: wrap; margin-bottom: 16px; }}
.pipe-step {{
  flex: 1 1 120px; display: flex; flex-direction: column; align-items: center;
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px 10px; text-align: center; position: relative; min-width: 0;
}}
.pipe-step + .pipe-step {{ margin-left: 0; }}
.pipe-arrow {{
  display: flex; align-items: center; padding: 0 4px; color: var(--primary-light);
  font-size: 18px; flex-shrink: 0; align-self: center;
}}
.pipe-icon {{
  width: 32px; height: 32px; border-radius: 50%;
  background: var(--primary-muted); display: flex; align-items: center;
  justify-content: center; font-size: 16px; margin-bottom: 6px;
}}
.pipe-title {{ font-size: 11px; font-weight: 700; color: var(--text-primary); margin-bottom: 3px; }}
.pipe-desc {{ font-size: 10px; color: var(--text-muted); line-height: 1.4; }}
.pipe-badge {{
  position: absolute; top: -8px; left: 50%; transform: translateX(-50%);
  background: var(--primary); color: #fff; font-size: 9px; font-weight: 700;
  padding: 1px 7px; border-radius: 10px; white-space: nowrap;
}}
@media(max-width:600px) {{ .pipeline {{ flex-direction: column; }} .pipe-arrow {{ transform: rotate(90deg); align-self: flex-start; margin-left: 48px; }} }}
</style>
</head>
<body>

<div class="meta-bar">
  <span>项目：<strong>{project_name}</strong></span>
  <span>关键词：<strong>{'、'.join(keywords) if keywords else project_name}</strong></span>
  <span>研报数量：<strong>{len(reports)} 篇</strong></span>
  <span>分析维度：<strong>{len(dimensions)} 个</strong></span>
  <span>生成时间：<strong>{generated_at}</strong></span>
</div>

<div class="section-header"><span class="section-num">1</span>主题科普</div>
<p class="section-sub">帮助快速建立对研究标的的认知框架</p>
<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:16px 20px;margin-bottom:16px;">
{primer_html}
</div>

<div class="section-header"><span class="section-num">2</span>研报数据来源</div>
<p class="section-sub">本次分析共收录 {len(reports)} 篇专业机构研报</p>
<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:10px;overflow:hidden;margin-bottom:16px;">
  <table>
    <thead>
      <tr>
        <th style="width:36px;">#</th>
        <th>机构</th>
        <th>分析师</th>
        <th>发布日期</th>
        <th>评级</th>
        <th>报告标题</th>
      </tr>
    </thead>
    <tbody>
{report_table_rows}
    </tbody>
  </table>
</div>

<div class="section-header"><span class="section-num">3</span>分析框架说明</div>
<p class="section-sub">本次分析覆盖 {len(dimensions)} 个维度，各维度聚焦问题如下</p>
<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:16px;">
{dim_html if dim_html else '<p style="color:var(--text-muted);font-size:12px;">维度说明生成失败</p>'}
</div>

<div class="section-header"><span class="section-num">4</span>分析流程</div>
<p class="section-sub">双层 AI 协作 Pipeline：Claude 规划 + Kimi 深研 + Claude 评审</p>
<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:10px;padding:16px 18px;margin-bottom:16px;">
  <div class="pipeline">
    <div class="pipe-step">
      <span class="pipe-badge">输入</span>
      <div class="pipe-icon">📄</div>
      <div class="pipe-title">研报原文</div>
      <div class="pipe-desc">{len(reports)} 篇机构研报<br>PDF 全文提取</div>
    </div>
    <div class="pipe-arrow">›</div>
    <div class="pipe-step">
      <span class="pipe-badge">Phase 1</span>
      <div class="pipe-icon">🧭</div>
      <div class="pipe-title">Claude 拆解</div>
      <div class="pipe-desc">识别细分模块<br>确定 {len(dimensions)} 个分析维度</div>
    </div>
    <div class="pipe-arrow">›</div>
    <div class="pipe-step">
      <span class="pipe-badge">Phase 2</span>
      <div class="pipe-icon">⚡</div>
      <div class="pipe-title">Kimi 深研</div>
      <div class="pipe-desc">每维度独立生成<br>ECharts 可视化页面</div>
    </div>
    <div class="pipe-arrow">›</div>
    <div class="pipe-step">
      <span class="pipe-badge">Phase 3</span>
      <div class="pipe-icon">🔍</div>
      <div class="pipe-title">Claude 评审</div>
      <div class="pipe-desc">质量检查 + 修改建议<br>最多 2 轮迭代</div>
    </div>
    <div class="pipe-arrow">›</div>
    <div class="pipe-step">
      <span class="pipe-badge">输出</span>
      <div class="pipe-icon">📊</div>
      <div class="pipe-title">分析看板</div>
      <div class="pipe-desc">{len(dimensions)} 个维度 Tab<br>+ 产业全景总览</div>
    </div>
  </div>
</div>

</body>
</html>"""

    return html


# ── Phase 5：Claude 生成总览 tab ───────────────────────────────────────────

_OVERVIEW_SYSTEM = """你是顶级A股投研团队首席研究员，负责撰写投研看板的总览页。
只输出完整 HTML 代码（<!DOCTYPE html> 到 </html>），不加任何说明。"""

_OVERVIEW_USER_TPL = """## 项目：{project_name}
## 已完成的各维度分析摘要：
{dimension_summaries}

## 研报基本信息：
- 研报数量：{report_count} 篇
- 分析维度：{dimensions_str}
- 生成时间：{generated_at}

---
生成"产业全景"总览 HTML tab，包含：
1. 核心指标卡片（3-4 个，如国产化率、市场规模、CAGR、龙头数量）
2. BOM 成本构成面积/矩形树图（用实际数据）
3. 最大卡脖子 / 最不可替代 / 追赶最快（各1-2句结论）
4. 产业里程碑时间轴（2024-2030，列关键节点）
5. 多空结论摘要（各2-3句）

主题要求（白底紫色）：
{theme_css}
引入 ECharts：<script src="{echarts_cdn}"></script>
监听 tab-shown 消息执行 chart.resize()。
高度 550-650px，overflow:auto。
只输出 HTML 代码。"""


def generate_overview_tab(
    project_name: str,
    dimensions: list[str],
    dim_htmls: dict[str, str],
    report_count: int,
    progress_cb=None,
) -> str:
    """Claude 生成总览 tab。"""
    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    _log("Claude 正在生成产业全景总览…")

    # 从各维度 HTML 提取文字摘要（取前 500 字作为上下文）
    summaries = []
    for dim, html in dim_htmls.items():
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()[:500]
        summaries.append(f"【{dim}】{text}")

    user_msg = _OVERVIEW_USER_TPL.format(
        project_name=project_name,
        dimension_summaries="\n\n".join(summaries),
        report_count=report_count,
        dimensions_str="、".join(dimensions),
        generated_at=datetime.now().strftime("%Y-%m-%d"),
        theme_css=THEME_CSS,
        echarts_cdn=ECHARTS_CDN,
    )

    raw = _claude_call(_OVERVIEW_SYSTEM, user_msg, max_tokens=6000)
    html = _extract_html(raw)
    return html if html and len(html) > 200 else "<p>总览生成失败</p>"


# ── 进度状态 ──────────────────────────────────────────────────────────────────

_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()


def get_progress(project_id: int) -> dict:
    with _progress_lock:
        return dict(_progress.get(project_id, {
            "status": "idle", "phase": "", "message": "",
            "done_dimensions": [], "total_dimensions": 0,
        }))


def _set_progress(project_id: int, **kwargs):
    with _progress_lock:
        if project_id not in _progress:
            _progress[project_id] = {}
        _progress[project_id].update(kwargs)


# ── 主 pipeline ───────────────────────────────────────────────────────────────

def run_analysis(project_id: int) -> None:
    """完整分析 pipeline，在后台线程调用。"""
    project = get_project(project_id)
    if not project:
        logger.error(f"[analyzer] project {project_id} not found")
        return

    project_name: str = project["name"]
    user_dimensions: list[str] = project["dimensions"]

    def _log(msg: str):
        logger.info(msg)
        _set_progress(project_id, message=msg)

    try:
        update_project_status(project_id, "analyzing")
        _set_progress(project_id, status="analyzing", phase="准备数据",
                      total_dimensions=0, done_dimensions=[])

        # Phase 0: 获取研报批次
        _log("准备研报文字批次…")
        batches = get_reports_text_batches(project_id)
        report_count = len(get_rb_reports(project_id))

        if not batches:
            _log("没有可分析的研报全文，请先点击「抓取全文」下载 PDF 内容")
            update_project_status(project_id, "error")
            _set_progress(project_id, status="error", message="没有研报全文可分析，请先抓取")
            return

        # Phase 1: Claude 拆解模块 + 扩展维度（失败时 fallback 到用户维度）
        _set_progress(project_id, phase="Claude 拆解细分模块")
        _log("Claude 正在拆解细分模块，扩展分析维度…")
        try:
            decomp = decompose_project(project_name, user_dimensions, batches)
        except Exception as dc_e:
            _log(f"模块拆解失败，使用默认维度继续（{dc_e}）")
            decomp = {"sub_modules": [], "final_dimensions": user_dimensions, "extra_dimensions": []}
        sub_modules: list[str] = decomp.get("sub_modules", [])
        final_dimensions: list[str] = decomp.get("final_dimensions", user_dimensions)

        if sub_modules:
            _log(f"识别到细分模块：{'、'.join(sub_modules)}")
        _log(f"将分析 {len(final_dimensions)} 个维度：{'、'.join(final_dimensions)}")

        _set_progress(project_id, total_dimensions=len(final_dimensions) + 2)  # +2 研究背景+总览

        # Phase 2 & 3: 各维度 Kimi 生成 + Claude 评审（并发）
        _set_progress(project_id, phase="Kimi 深研 + Claude 评审")
        dim_htmls: dict[str, str] = {}

        def _do_dim(dim: str) -> tuple[str, str]:
            html = generate_tab_with_review(
                project_name, dim, sub_modules, batches,
                progress_cb=_log,
            )
            return dim, html

        with ThreadPoolExecutor(max_workers=MAX_KIMI_WORKERS) as executor:
            futures = {executor.submit(_do_dim, d): d for d in final_dimensions}
            for future in as_completed(futures):
                try:
                    dim, html = future.result()
                    dim_htmls[dim] = html
                    upsert_rb_analysis(project_id, dim, 0, json.dumps({"html": html[:500]}, ensure_ascii=False), "done")
                    with _progress_lock:
                        done = _progress.get(project_id, {}).get("done_dimensions", [])
                        _progress[project_id]["done_dimensions"] = done + [dim]
                    _log(f"【{dim}】分析完成")
                except Exception as e:
                    dim = futures[future]
                    _log(f"【{dim}】分析失败：{e}")
                    upsert_rb_analysis(project_id, dim, 0, "{}", "failed", str(e))
                    dim_htmls[dim] = f"<p style='color:#dc2626;padding:20px'>【{dim}】分析失败：{e}</p>"

        # Phase 4: Claude 生成总览 tab（独立 try/except，总览失败不影响维度 tabs）
        _set_progress(project_id, phase="Claude 生成总览")
        try:
            overview_html = generate_overview_tab(
                project_name, final_dimensions, dim_htmls, report_count,
                progress_cb=_log,
            )
        except Exception as ov_e:
            _log(f"产业全景总览生成失败（维度分析不受影响）：{ov_e}")
            overview_html = f"<p style='color:#d97706;padding:20px'>总览生成失败：{ov_e}</p>"

        # Phase 5: Claude 生成研究背景 tab
        _set_progress(project_id, phase="Claude 生成研究背景")
        reports_meta = get_rb_reports(project_id)
        keywords = project.get("keywords", [])
        try:
            intro_html = generate_intro_tab(
                project_name, keywords, final_dimensions, reports_meta,
                progress_cb=_log,
            )
        except Exception as intro_e:
            _log(f"研究背景生成失败（不影响其他 tabs）：{intro_e}")
            intro_html = f"<p style='color:#d97706;padding:20px'>研究背景生成失败：{intro_e}</p>"

        # 组装 tabs：研究背景第一、产业全景第二、各维度依次排列
        tabs = [
            {"name": "研究背景", "html": intro_html},
            {"name": "产业全景", "html": overview_html},
        ]
        for dim in final_dimensions:
            tabs.append({"name": dim, "html": dim_htmls.get(dim, "")})

        result_payload = {
            "tabs": tabs,
            "project_name": project_name,
            "report_count": report_count,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sub_modules": sub_modules,
            "dimensions": final_dimensions,
            "extra_dimensions": decomp.get("extra_dimensions", []),
        }

        upsert_rb_result(project_id, json.dumps(result_payload, ensure_ascii=False))
        update_project_status(project_id, "done", report_count)
        _set_progress(project_id, status="done", phase="完成",
                      message=f"分析完成，共 {len(tabs)} 个分析页面")
        _log(f"分析完成！共生成 {len(tabs)} 个分析页面（{project_name}）")

    except Exception as e:
        logger.exception(f"[analyzer] 项目 {project_id} 分析异常: {e}")
        update_project_status(project_id, "error")
        _set_progress(project_id, status="error", message=f"分析出错：{e}")


def start_analysis_thread(project_id: int) -> threading.Thread:
    t = threading.Thread(
        target=run_analysis,
        args=(project_id,),
        daemon=True,
        name=f"rb-analyzer-{project_id}",
    )
    t.start()
    return t
