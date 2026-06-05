"""
research_board — 双层分析 Pipeline

架构：
  Claude supervisor（claude -p）orchestrate 整个 pipeline：
    - Phase 1：读研报摘要，拆解细分模块 + 动态扩展用户维度
    - Phase 2：并行 dispatch N 个 Kimi subagent（via codex exec），每个 subagent
               独立生成一个维度的完整 ECharts HTML，写入临时文件
    - Phase 3：所有维度完成后，Kimi subagent 生成产业全景总览，写入临时文件
    - Phase 4：输出路径 JSON（不含 HTML 内容，不受 token 限制）
  Python 负责：
    - 数据准备（研报文本批次）
    - stream-json 进度读取 + 前端推送
    - 从临时文件读取 HTML → _quick_html_check → clean_html → 存库
    - 研究背景 tab（Claude 生成文字科普，Python 硬编码研报表格）
    - 前端"重新生成单 Tab"（_REGEN_TPL + _quick_html_check）
"""

import hashlib
import json
import logging
import os
import threading
from datetime import datetime

from research_board.rb_fetcher import get_reports_text_batches
from research_board.rb_storage import (
    get_project,
    get_rb_reports,
    get_rb_result,
    update_project_status,
    upsert_rb_analysis,
    upsert_rb_result,
)
from research_board.cli_runner import (
    _call_llm as kimi_call,
    call_claude_text,
    run_claude_pipeline,
    _extract_json,
)

logger = logging.getLogger(__name__)

# ── 主题 Token ────────────────────────────────────────────────────────────────

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

# ── HTML 质量规则（Python 硬检查，用于 run_analysis 文件读取后 + regenerate_single_tab）──

REVIEW_CRITERIA = """
评审标准（全部通过才算合格）：
1. 内容深度：有具体数字、比例、时间节点，不是泛泛而谈
2. 数据可信：所有数字必须来自研报原文，不得编造；若某年份/字段数据研报中不存在，必须省略该数据点或标注"数据缺失"，不得填写 null 或 0；series data 不得为空数组（[]）或全零数组
3. 可视化质量：ECharts 图表 series data 有真实数值，不是占位符；不得用 scatter 散点图表达时间进度/量产推进（应改为带文字标注的自定义 HTML 时间轴，或横向条形图）
4. 主题适配：使用白底紫色主题（--primary: #7c3aed），不使用深色背景
5. 无语法错误：HTML/JS 代码可正常运行；HTML 内容不得被截断，所有表格的 tbody 必须有完整数据行
6. 图表初始化完整：页面中每个 <div id="chart..."> 容器必须有对应的 echarts.init() 调用；若发现图表容器数量 > echarts.init() 调用数量，判定为不合格
7. 无高度截断：body 标签、任何包裹容器（.page-wrapper / .main-wrap / .container 等）均不得设置 height 或 max-height 固定值；页面高度必须由内容自然撑开
8. 无装饰性遮罩：不得添加 position:sticky/fixed 的渐变遮罩层；不得添加 position:fixed 的侧边导航浮层——这些元素在 iframe 中会遮挡内容
9. KPI卡片布局：.kv-grid 必须用 display:flex + flex-wrap:wrap，.kv-card 必须有 flex:1 1 180px；禁止 display:grid 固定列数；禁止任何卡片独占整行（grid-column:1/-1 或 width:100%）
"""

# ── HTML 后处理 ───────────────────────────────────────────────────────────────

import re as _re


def _fix_gantt_data(html: str) -> str:
    """
    修复 ECharts bar-stack 甘特图中 Kimi 常见的数据格式错误。

    ECharts bar 系列在 value-axis 上绘制时，bars 从 x=0 画到 data 值。
    正确甘特格式：占位 data = start_year（实际年份），持续 data = duration。
    堆叠后占位结束位置即为起始年，彩色条从该位置延伸 duration 年。

    Kimi 常见两种错误格式：
    1. offset 格式：占位 data=[3,3,4,2]（start-xMin），彩色 data=[2,3,2,4]（duration）
       → 占位条从 x=0 画到 3，在 xMin=2024 的轴上完全看不见
       → 修复：占位 data 改为 start_year = xMin + offset

    2. 多元素数组格式：data=[{value:[0,0,2024,2]}, ...]（y_idx, y_idx, start, dur）
       或 data=[{value:[2025,2]}, ...]（start, dur）
       → bar 系列不接受数组，条形不渲染
       → 修复：提取 start 和 duration，转换为单值
    """
    # 找到所有 option 对象字面量：
    #   1. chart.setOption({...})  — 内联形式
    #   2. var option = {...}; chart.setOption(option) — 变量形式
    # 两种都用括号计数提取对象，交给 _try_fix_gantt_option 处理
    result = []
    pos = 0
    # 匹配两种起始模式，统一提取对象字面量
    pat_setoption = _re.compile(
        r'(\.setOption\s*\(\s*\{|var\s+\w+\s*=\s*\{)',
        _re.DOTALL
    )

    while pos < len(html):
        m = pat_setoption.search(html, pos)
        if not m:
            result.append(html[pos:])
            break

        # 追加匹配前的文本
        result.append(html[pos:m.start()])

        matched = m.group(0)
        is_setoption = matched.lstrip().startswith('.')

        # 括号计数找到完整对象字面量（找到匹配串中的 '{'）
        brace_start = matched.rfind('{')
        abs_brace = m.start() + brace_start
        depth = 0
        i = abs_brace
        while i < len(html):
            if html[i] == '{':
                depth += 1
            elif html[i] == '}':
                depth -= 1
                if depth == 0:
                    break
            i += 1
        option_text = html[abs_brace:i + 1]

        # 只处理含 bar + xAxis(value,min=年份) 的 option 对象
        fixed = _try_fix_gantt_option(option_text)
        prefix = matched[:brace_start]  # '.setOption(' 或 'var option = '
        result.append(prefix + fixed)
        pos = i + 1

        if is_setoption:
            # 跳过紧接的 ')' 关闭 setOption(...)
            while pos < len(html) and html[pos] in ' \t\n\r':
                pos += 1
            if pos < len(html) and html[pos] == ')':
                result.append(')')
                pos += 1

    return ''.join(result)


def _try_fix_gantt_option(option_text: str) -> str:
    """
    尝试修复单个 setOption 参数文本中的甘特图数据格式。
    无法解析时原样返回。
    """
    # 必须有 bar series 且 xAxis 是 value 类型且有 min 是年份
    if "'bar'" not in option_text and '"bar"' not in option_text:
        return option_text

    xmin_m = _re.search(r'\bmin\s*:\s*(\d{4})\b', option_text)
    if not xmin_m:
        return option_text
    x_min = int(xmin_m.group(1))
    if x_min < 2000 or x_min > 2050:
        return option_text

    # 检测嵌套数组格式：data 里有 {value:[...]} 或 [[...]] 元素
    has_nested = bool(_re.search(
        r'\bdata\s*:\s*\[(?:[^[\]]*\[[^\]]*\][^[\]]*)+\]',
        option_text
    ))

    # 检测 offset 格式：两组 bar series 都是纯数字单值，第一组值均 < 20（offset，不是年份）
    bar_data_blocks = _re.findall(
        r"type\s*:\s*['\"]bar['\"].*?data\s*:\s*(\[[^\[\]]+\])",
        option_text, _re.DOTALL
    )
    is_offset_format = False
    if not has_nested and len(bar_data_blocks) >= 2:
        try:
            first_vals = [float(x) for x in _re.findall(r'[-\d.]+', bar_data_blocks[0])]
            if first_vals and all(v < 50 for v in first_vals):
                is_offset_format = True
        except Exception:
            pass

    if not has_nested and not is_offset_format:
        return option_text

    if has_nested:
        return _fix_nested_array_gantt(option_text, x_min)
    else:
        return _fix_offset_gantt(option_text, x_min)


def _fix_nested_array_gantt(option_text: str, x_min: int) -> str:
    """
    修复 {value:[...]} 或 [[...]] 数组格式的甘特数据。
    识别两种子格式：
      A: [start, end] 两元素 → 占位=start，持续=end-start
      B: [y,y,start,dur] 四元素 → 占位=start，持续=dur
      C: [start, dur] 两元素（start>=2000）→ 占位=start，持续=dur
    """
    def rewrite_data_array(data_str: str, series_idx: int) -> str:
        # 提取每个元素的数值数组
        items = _re.findall(
            r'\{[^{}]*value\s*:\s*\[([^\]]+)\][^{}]*\}|\[([^\[\]]+)\]',
            data_str
        )
        values_list = []
        for v1, v2 in items:
            raw = v1 or v2
            try:
                nums = [float(x) for x in _re.findall(r'[-\d.]+', raw)]
                values_list.append(nums)
            except Exception:
                return data_str  # 无法解析，原样返回

        if not values_list:
            return data_str

        n = len(values_list[0])
        new_vals = []
        for nums in values_list:
            if n == 4:
                # [y, y, start, duration]
                start, dur = nums[2], nums[3]
            elif n == 2:
                if nums[0] >= 2000:
                    # [start_year, duration]
                    start, dur = nums[0], nums[1]
                else:
                    # [start_offset, end] — start_offset < 2000 means relative
                    start, dur = x_min + nums[0], nums[1] - nums[0]
            else:
                return data_str

            if series_idx == 0:
                new_vals.append(str(int(start)))   # 占位 = 起始年
            else:
                new_vals.append(str(int(dur)))     # 持续 = 持续年数

        return '[' + ', '.join(new_vals) + ']'

    # 找到所有 bar series 的 data 块并依次替换
    series_blocks = list(_re.finditer(
        r"(type\s*:\s*['\"]bar['\"].*?data\s*:\s*)(\[(?:[^[\]]*\[[^\]]*\][^[\]]*)+\])",
        option_text, _re.DOTALL
    ))
    if not series_blocks:
        return option_text

    result = option_text
    offset = 0
    for idx, m in enumerate(series_blocks):
        old_data = m.group(2)
        new_data = rewrite_data_array(old_data, idx)
        if new_data != old_data:
            start = m.start(2) + offset
            end = m.end(2) + offset
            result = result[:start] + new_data + result[end:]
            offset += len(new_data) - len(old_data)

    return result


def _fix_offset_gantt(option_text: str, x_min: int) -> str:
    """
    修复 offset 格式：占位 data 是相对 xMin 的偏移值（如 [3,3,4,2]），
    需要加上 x_min 变为实际起始年。彩色 series data（duration）保持不变。
    """
    bar_blocks = list(_re.finditer(
        r"(type\s*:\s*['\"]bar['\"].*?data\s*:\s*)(\[[^\[\]]+\])",
        option_text, _re.DOTALL
    ))
    if len(bar_blocks) < 2:
        return option_text

    # 只修改第一个（占位）series 的 data
    m = bar_blocks[0]
    old_data = m.group(2)
    nums = [float(x) for x in _re.findall(r'[-\d.]+', old_data)]
    if not nums or any(v >= 2000 for v in nums):
        return option_text  # 已经是年份格式，不处理

    new_nums = [str(int(x_min + v)) for v in nums]
    new_data = '[' + ', '.join(new_nums) + ']'
    result = option_text[:m.start(2)] + new_data + option_text[m.end(2):]
    return result


def clean_html(html: str) -> str:
    """统一 HTML 后处理：剥离 markdown 代码块标记，移除导致 iframe 截断的 CSS 属性，修复甘特图数据。"""
    if not html:
        return html
    html = _re.sub(r"^```(?:html)?\s*\n?", "", html.strip(), flags=_re.IGNORECASE)
    html = _re.sub(r"\n?\s*```\s*$", "", html.strip())
    html = html.strip()
    html = _re.sub(r'(body\s*\{[^}]*)height\s*:\s*\d+px\s*;?\s*', r'\1', html, flags=_re.IGNORECASE)
    html = _re.sub(r'\bmax-height\s*:\s*\d+[^;}\n]*;?\s*', '', html, flags=_re.IGNORECASE)
    html = _re.sub(r'\boverflow-y\s*:\s*(auto|scroll)\s*;?\s*', '', html, flags=_re.IGNORECASE)
    html = _re.sub(r'\boverflow\s*:\s*(auto|scroll)\s*;?\s*', '', html, flags=_re.IGNORECASE)
    html = _fix_gantt_data(html)
    return html


def _quick_html_check(html: str) -> list[str]:
    """
    Python 硬检查——所有可以用正则/计数确定性检测的质量问题。
    返回问题列表（空列表 = 通过）。
    """
    issues = []

    if '<!doctype' not in html.lower() and '<html' not in html.lower():
        issues.append("HTML 输出不完整（缺少 <!DOCTYPE html> 和 <html> 标签）；请重新生成完整 HTML 文档")
        return issues

    if not html.rstrip().endswith('</html>'):
        issues.append("HTML 被截断（未以 </html> 结尾）；请输出完整文档直到 </html>")
        return issues

    if '<!doctype' not in html.lower():
        issues.append("缺少 <!DOCTYPE html> 声明")

    chart_divs = len(_re.findall(r'<div[^>]+id=["\'][^"\']*chart[^"\']*["\']', html, _re.IGNORECASE))
    init_calls = len(_re.findall(r'echarts\.init\s*\(', html))
    if chart_divs > 0 and init_calls == 0:
        issues.append(
            f"发现 {chart_divs} 个图表容器（<div id='chart...'>）但没有 echarts.init() 调用，"
            f"图表全部空白；每个容器必须有对应的 echarts.init() + setOption()"
        )
    elif chart_divs > init_calls + 1:
        issues.append(
            f"图表容器 {chart_divs} 个但 echarts.init() 只有 {init_calls} 次，"
            f"缺少 {chart_divs - init_calls} 个图表的初始化代码"
        )

    empty_series = _re.findall(r'\bdata\s*:\s*\[\s*\]', html)
    if empty_series:
        issues.append(
            f"发现 {len(empty_series)} 处 series data 为空数组（data: []），图表将空白；"
            f"必须填入来自研报的真实数值，若确无数据请删除该图表"
        )

    zero_series = _re.findall(r'\bdata\s*:\s*\[(?:\s*0\s*,\s*){2,}\s*0\s*\]', html)
    if zero_series:
        issues.append(
            f"发现 {len(zero_series)} 处 series data 全为 0；"
            f"必须填入来自研报的真实数值"
        )

    null_series = _re.findall(r'\bdata\s*:\s*\[[^\]]*\bnull\b[^\]]*\]', html)
    if null_series:
        issues.append(
            f"发现 {len(null_series)} 处 series data 含 null；"
            f"数据缺失时直接从 xAxis.data 和 series.data 中省略该年份"
        )

    empty_value = _re.search(r'\bvalue\s*:\s*(?:0|null|""|\'\')(?:\s*[,}])', html)
    if empty_value:
        issues.append("饼图/树图存在 value 为 0/null/空字符串的扇区，图表将显示空扇区；请填入真实数值或删除该数据项")

    empty_tbody = _re.search(r'<tbody>\s*</tbody>', html, _re.IGNORECASE)
    if empty_tbody:
        issues.append("存在空 <tbody></tbody>；所有表格必须有完整数据行")

    # tab-shown 消息格式检测：前端发 {type:'tab-shown'} 对象，不是裸字符串
    if _re.search(r"e\.data\s*===\s*['\"]tab-shown['\"]", html):
        issues.append(
            "tab-shown 消息监听写法错误：用了 `e.data === 'tab-shown'`（裸字符串），"
            "前端发的是 `{type:'tab-shown'}` 对象，导致 resize() 永远不触发，图表宽度为0。"
            "必须改为 `e.data && e.data.type === 'tab-shown'`"
        )

    # 甘特图 data 数组检测：bar stack 甘特图的 data 每项必须是单值，不能是 [x,y] 或 [x,y,z] 数组
    # 典型错误：data: [{value:[2025,2028]}, ...] 或 data: [[2025,2028], ...]
    gantt_array_data = _re.findall(
        r'\bdata\s*:\s*\[(?:[^[\]]*\[[^\]]*\][^[\]]*)+\]',
        html
    )
    if gantt_array_data:
        issues.append(
            f"发现 {len(gantt_array_data)} 处 bar series data 含嵌套数组（如 [2025,2028] 或 [0,0,2025,3]），"
            f"ECharts bar stack 甘特图每项 data 必须是单值：占位 data=[起始年，如2027]，持续 data=[持续年数，如3]"
        )

    return issues


# ── Phase 1：Claude 拆解细分模块 & 动态维度 ──────────────────────────────────

_DECOMPOSE_SYSTEM = """你是顶级A股投资研究团队的首席研究员。
你的核心任务：从研报中提炼投资决策所需的分析框架——每个维度必须能回答"投资者应该关心什么、为什么"。
只输出 JSON，不要其他文字。"""

_DECOMPOSE_USER_TPL = """项目名称：{project_name}
用户预设分析维度（必须全部保留，顺序不变）：{user_dimensions}

以下是该赛道的研报摘要（元数据 + 部分正文）：
{report_summary}

---
请完成两步分析，输出如下 JSON：

{{
  "sub_modules": ["模块1", "模块2", ...],
  "consensus": "这几篇研报最强的共识判断是什么（1-2句，要有具体数字或事件）",
  "divergence": "研报之间最主要的分歧或不确定性在哪里（1句）",
  "extra_dimensions": ["新维度1", "新维度2"],
  "final_dimensions": ["最终维度1", ...],
  "dimension_questions": {{
    "维度名": "这个维度的核心决策问题（投资者最想知道的一个问题，能帮助判断买/持/卖，例如：国产替代进度是否达到量产拐点？）",
    ...
  }}
}}

规则：
1. sub_modules：3-8 个细分模块，从研报实际内容提炼
2. extra_dimensions：多篇研报共同高频出现的主题，单篇偶发不加
3. final_dimensions：用户预设维度（原样、顺序在前）+ extra_dimensions，去重
4. dimension_questions：为 final_dimensions 中每个维度写一个核心决策问题——这个问题的答案能直接影响仓位判断，不是泛泛的描述性问题
5. 用户预设维度一个也不能删，名称不能改写
6. 只输出 JSON"""


def decompose_project(project_name: str, user_dimensions: list[str],
                      report_batches: list[str]) -> dict:
    """Claude 拆解细分模块 + 动态扩展维度 + 为每个维度生成核心决策问题。"""
    summary = "\n\n---\n\n".join(report_batches[:2])[:20000]
    user_msg = _DECOMPOSE_USER_TPL.format(
        project_name=project_name,
        user_dimensions=json.dumps(user_dimensions, ensure_ascii=False),
        report_summary=summary,
    )
    raw = call_claude_text(_DECOMPOSE_SYSTEM, user_msg)
    result = _extract_json(raw)
    if not isinstance(result, dict) or "final_dimensions" not in result:
        return {
            "sub_modules": [], "final_dimensions": user_dimensions,
            "extra_dimensions": [], "dimension_questions": {},
        }

    # 保证用户预设维度全部存在且顺序在前
    llm_dims: list[str] = result.get("final_dimensions", [])
    seen = set(llm_dims)
    missing = [d for d in user_dimensions if d not in seen]
    if missing:
        result["final_dimensions"] = user_dimensions + [d for d in llm_dims if d not in set(user_dimensions)]

    if "dimension_questions" not in result:
        result["dimension_questions"] = {}

    return result


# ── Phase 2：Kimi subagent HTML 生成要求 ─────────────────────────────────────
# 这是传给 claude agent 的模板，claude 为每个维度构造 codex 指令时参考。
# 不再有"输出长度控制"——Kimi 写文件，内容越详细越好。

_KIMI_HTML_TPL = """你是专业的A股投资研究员，使用 ECharts + HTML 生成研究分析页面。

## 项目：{project_name}
## 当前分析维度：{dimension}
## 本维度的核心决策问题：{core_question}
## 细分模块参考：{sub_modules}

## 研报原文
{report_text}

---
## 分析任务

**首要目标**：回答上方的核心决策问题。所有图表和文字都是支撑这个答案的证据，最终必须给出明确结论（看多/看空/中性 + 核心依据1-2句）。

### 页面结构（按顺序）

**① 核心判断卡片**（页面顶部，最先渲染）
- 用一个醒目卡片直接给出结论：做多逻辑 vs 做空逻辑 vs 当前判断
- 必须引用研报中的具体数字或事件支撑

**② 数据论证区**
- 把研报中该维度的所有相关数据、公司、时间节点都用进来
- 宁可多也不要泛泛而谈
- 图表选择规则：
  - 成本/占比类 → 饼图或堆叠柱状图
  - 竞争格局/比较类 → 雷达图或对比表格
  - 趋势/预测类 → 折线图或柱线混合图
  - 时间进度/里程碑 → 自定义 HTML `<div>` 时间轴（每节点标注年份+事件），**禁止 scatter 散点图**
  - 甘特图（厂商量产时间段）→ 双 bar+stack，严格按下方示例写法：

```javascript
// ✅ 正确：占位 data = 起始年（实际年份），持续 data = 持续年数，全部单值
// 示例：A公司 2025-2028，B公司 2026-2030，C公司 2027-2030
// ECharts bar 在 value 轴上从 x=0 开始画，所以占位值必须是实际年份才能定位到正确位置
xAxis: {{ type: 'value', min: 2024, max: 2031 }},
yAxis: {{ type: 'category', data: ['C公司','B公司','A公司'] }},
series: [
  {{ name:'占位', type:'bar', stack:'g', itemStyle:{{color:'transparent'}},
    data: [2027, 2026, 2025] }},   // 各公司量产起始年：C=2027，B=2026，A=2025
  {{ name:'量产', type:'bar', stack:'g', itemStyle:{{color:'#7c3aed'}},
    data: [3, 4, 3] }}             // 各公司持续年数：C=3年，B=4年，A=3年
]
// ❌ 禁止：data:[{{value:[2025,2028]}}]、data:[{{value:[1,3,2]}}]、data:[3,2,1]（偏移量写法）
```

**③ 页面末尾写一行注释（机器读取，不显示在页面上）**
格式：`<!-- SUMMARY: <30字内的核心判断，含最关键数字> -->`
例：`<!-- SUMMARY: 国产化率已达47%，2026年龙头毛利率有望回升至35%，看多 -->`

## 格式与质量要求

- **只输出 HTML**，从 `<!DOCTYPE html>` 到 `</html>`，不加说明
- body 中**禁止**出现 JS 代码文本、调试日志、推理过程
- 图表数量不限：有多少值得可视化的数据就放多少
- series data 必须来自研报，不得为空数组 `[]`、全零数组或含 `null`；数据缺失时省略该点
- 饼/树图 value 不得为 0 或 null；所有 tbody 必须有完整数据行

## 主题与依赖
{theme_css}
引入 ECharts：`<script src="{echarts_cdn}"></script>`

**resize 监听（必须原样复制，不得改动）**：
```javascript
window.addEventListener('message', function(e) {{
  if (e.data && e.data.type === 'tab-shown') {{
    charts.forEach(function(c) {{ c.resize(); }});
  }}
}});
```
其中 `charts` 是你在 script 里维护的所有 `echarts.init()` 返回值的数组，每 init 一个就 `charts.push(chart)`。

## 布局约束（iframe 内展示，不能截断）
- body 和任何包裹容器不得设置固定 height / max-height；不得使用 overflow:auto/scroll
- 不得添加 position:sticky/fixed 的渐变遮罩或侧边导航浮层
- `.kv-grid` 用 `display:flex; flex-wrap:wrap; gap:10px`；`.kv-card` 用 `flex:1 1 180px; min-width:0`

生成完整 HTML 后，用 bash 将完整内容写入 {output_path}，只输出"已写入 {output_path}"，不要在对话中输出 HTML。
"""

# ── Phase 3（产业全景）生成要求 ───────────────────────────────────────────────

_OVERVIEW_TPL = """你是专业的A股投资研究员，使用 ECharts + HTML 生成"产业全景"总览分析页。

## 项目：{project_name}
## 各维度核心判断（已从各维度分析中提炼，直接使用，不需要再读 HTML 文件）：
{dimension_summaries}

## 研报基本信息：
- 研报数量：{report_count} 篇
- 覆盖维度：{dimensions_str}
- 生成时间：{generated_at}

---
## 核心任务

产业全景的目标是：**让投资者在60秒内看完，知道该不该投、现在是不是好时机**。

不是把各维度内容再堆一遍，而是**合成**：从上面的维度判断出发，给出整体结论。

### 必须包含的6个部分

**① 一句话投资判断**（页面最顶部，最大字号）
综合所有维度，当前投资窗口是什么：催化剂是什么、主要风险是什么。不回避，直接说。

**② 核心指标卡片**（4-6 个）
选最关键的数字指标（如国产化率、市场规模CAGR、龙头净利润增速预期），数据来自维度摘要。

**③ 多空博弈分析**
- Bull Case（做多逻辑）：2-3 个最强支撑，每条引具体数据
- Bear Case（做空逻辑）：2-3 个最大风险，不得省略
- 两部分等权重呈现，不能只写一方

**④ 产业里程碑时间轴**（2024-2030）
列入各维度发现的催化剂节点，用自定义 HTML `<div>` 时间轴，**禁止 ECharts scatter 散点图**

**⑤ 关键维度横向对比**（表格或雷达图）
各维度的"信号灯"：✅看多 / ⚠️中性 / ❌看空，加一句核心理由

**⑥ BOM 成本构成或竞争格局图**（如果维度摘要里有占比数据）
饼图或矩形树图，用真实数据

## 格式与质量要求
- **只输出 HTML**，从 `<!DOCTYPE html>` 到 `</html>`，不加说明
- series data 不得为空数组或全零，饼/树图 value 不得为 0/null
- 甘特图用双 bar+stack，严格格式：占位 data=[各公司起始年，如 2027]，持续 data=[各公司持续年数，如 3]，**禁止**用偏移量（起始年-xAxis.min）或数组元素

## 主题与依赖
{theme_css}
引入 ECharts：`<script src="{echarts_cdn}"></script>`

**resize 监听（必须原样复制，不得改动）**：
```javascript
window.addEventListener('message', function(e) {{
  if (e.data && e.data.type === 'tab-shown') {{
    charts.forEach(function(c) {{ c.resize(); }});
  }}
}});
```
其中 `charts` 是所有 `echarts.init()` 返回值的数组，每 init 一个就 `charts.push(chart)`。

## 布局约束
- body 和任何包裹容器不得设置固定 height / max-height，不得使用 overflow:auto/scroll
- 不得添加 position:sticky/fixed 的渐变遮罩或侧边导航浮层

生成完整 HTML 后，用 bash 将完整内容写入 {output_path}，只输出"已写入 {output_path}"。
"""

# ── Claude Pipeline 控制 Prompt ───────────────────────────────────────────────

_PIPELINE_SYSTEM = """你是A股投研分析 pipeline 的 supervisor agent（Claude）。

**目标**：产出一套高质量的维度分析 HTML 文件集，每个维度的页面必须能回答该维度的核心决策问题，包含来自研报的真实数据。

**你的工作分两步**：
1. 用一次 codex exec 启动 Kimi 主 agent，Kimi 在 codex 内部并行生成所有维度 HTML 文件
2. Kimi 完成后，用 Task tool 并行 dispatch Claude subagent 验收各维度内容质量

**质量标准**（subagent 验收时用这个标准）：
- 有核心判断卡片：做多/做空/当前结论 + 具体数字支撑
- 页面末尾有 `<!-- SUMMARY: ... -->` 注释（30字内核心判断）
- 有具体数字（不是"X%提升"而是"从12%升至18%"）
- 引用了研报中的具体公司名称或时间节点
- 文件完整（有 <!DOCTYPE html> 和 </html>，大小 > 3KB）
"""

_PIPELINE_USER_TPL = """## 项目：__PROJECT_NAME__
## 分析维度列表（共 __DIM_COUNT__ 个）：
__DIMENSIONS_JSON__

## 细分模块参考：__SUB_MODULES__

## 研报原文（供 Kimi 分析共享）：
__REPORT_TEXT__

## 研报元数据（供研究背景 subagent 渲染表格，不含全文）：
__REPORTS_META_JSON__

---
## 维度 HTML 生成规范（Kimi 的任务模板，传给 codex exec）：
__KIMI_HTML_TPL__

## 产业全景生成规范：
__OVERVIEW_TPL__

## 研究背景生成规范：
__INTRO_TPL__

---
## 执行步骤

### 第一步：构造 codex prompt，启动 Kimi 主 agent

构造一条完整的 prompt 交给 `codex exec`，内容包含：
- 研报原文（完整）
- 所有维度的名称、core_question（核心决策问题）、输出路径 `__TMP_DIR__/<hash>.html`
- 产业全景输出路径 `__TMP_DIR__/overview.html`
- 研究背景输出路径 `__TMP_DIR__/intro.html`
- 上方三份生成规范（维度、产业全景、研究背景）

要求 Kimi 主 agent：
1. 在 codex 内部用 subagent **并行**生成所有维度 HTML + 研究背景 HTML（各 subagent 互相独立，研究背景 subagent 不需要等维度完成）
2. 所有维度完成后，读取各维度文件末尾的 `<!-- SUMMARY: ... -->` 注释，汇总为维度摘要
3. 用汇总的维度摘要生成产业全景 HTML

运行命令（只运行一次）：
`codex exec --profile research --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check`

等待期间，每检测到文件出现（`test -s __TMP_DIR__/<hash>.html`），输出进度：
`[PROGRESS] 完成：<维度名或"研究背景">`

### 第二步：Claude 并行验收内容质量

codex 完成后，用 Task tool 并行 dispatch Claude subagent 验收所有文件（包括研究背景），每个 subagent：
1. 用 Bash 读取对应文件（`cat <path>`）
2. 按上方 system prompt 的质量标准检查
3. 返回：`{{"name":"<名称>","path":"<路径>","ok":true/false,"issue":"<未通过的具体标准，或空>"}}`

### 第三步：输出路径 JSON（立即输出，不重试）

```json
{{"tabs":[{{"name":"研究背景","path":"__TMP_DIR__/intro.html"}},{{"name":"<维度名>","path":"__TMP_DIR__/<hash>.html"}},...,{{"name":"产业全景","path":"__TMP_DIR__/overview.html"}}],"review":[{{"name":"...","ok":true/false,"issue":"..."}}]}}
```

验收不通过的文件 path 仍然填入，由 Python 侧处理，Claude 侧不重试。
"""


# ── Phase 4：研究背景 tab ─────────────────────────────────────────────────────

_INTRO_HTML_SYSTEM = """你是专业的A股投研助手，使用 HTML 生成研究背景页面。
只输出 HTML 代码，从 <!DOCTYPE html> 到 </html>，不加任何说明。"""

_INTRO_HTML_TPL = """你是专业的A股投研助手，使用 HTML 生成研究背景页面。
只输出 HTML 代码，从 <!DOCTYPE html> 到 </html>，不加任何说明。

## 研究项目：{project_name}
## 关键词：{keywords}
## 研报列表（{report_count} 篇，数据来源）：
{reports_json}
## 分析维度（{dim_count} 个）及核心决策问题：
{dimensions_questions}

## 研报摘要（用于科普内容，从中提炼背景知识，不是让你照抄）：
{report_summary}

---
## 任务

为"{project_name}"生成一个完整的**研究背景**页面，帮助投资者快速建立认知框架，理解为什么要关注这个标的。

### 页面结构（按顺序）

**① 一句话战略价值**（页面顶部醒目展示）
概括"{project_name}"对投资者的核心价值，有洞见，不套话，40-60字。

**② 主题科普：产业链位置**
- 上游依赖什么原材料/设备/工艺
- 自身核心技术壁垒是什么
- 下游覆盖哪些应用场景/终端市场
- 用一个直观的量化感知句结尾（如"一部手机含X颗，一辆新能源车需要Y颗以上"）
- 用横向流程图展示上游→{project_name}（★标注）→下游，每个节点有副标题

**③ 当前为什么值得关注**
客观描述当前时间节点（2025-2026年）投资者关注这个标的的核心焦点：
- 有明确催化剂（政策/供需拐点/大客户动向/技术突破）→ 具体列举，引用研报中的数字
- 整体偏谨慎或观察期 → 如实反映，不强行拔高
- 主要关注点来自哪几篇研报（引用机构名）

**④ 数据来源：研报列表**
展示所有研报的表格：序号、机构、分析师、发布日期、评级（颜色标注）、标题
评级颜色：买入/推荐/强烈推荐→绿色，增持/优于大市→蓝色，持有/中性→橙色，减持/卖出→红色

**⑤ 本次分析框架**
列出所有分析维度，每个维度显示：维度名（紫色标签）+ 核心决策问题（来自上方维度问题列表）

## 主题与布局
{theme_css}
- 白底紫色主题（--primary: #7c3aed），不使用深色背景
- body 和任何包裹容器不得设置固定 height / max-height，不得使用 overflow:auto/scroll
- 不得添加 position:sticky/fixed 的遮罩或侧边导航
- .kv-grid 用 display:flex; flex-wrap:wrap；.kv-card 用 flex:1 1 180px; min-width:0

生成完整 HTML 后，用 bash 将完整内容写入 {output_path}，只输出"已写入 {output_path}"，不要在对话中输出 HTML。
"""

def generate_intro_tab(
    project_name: str,
    keywords: list[str],
    dimensions: list[str],
    reports: list[dict],
    dimension_questions: dict | None = None,
    report_batches: list[str] | None = None,
    progress_cb=None,
) -> str:
    """
    研究背景 tab：Claude 直接输出完整 HTML，包含科普内容 + 研报表格 + 分析框架。
    """
    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    _log("Claude 正在生成研究背景页面…")

    # 研报元数据 JSON（不含全文，供 Claude 渲染表格）
    reports_simple = [
        {
            "org": r.get("org_name") or "—",
            "researcher": r.get("researcher") or "—",
            "date": r.get("publish_date") or "—",
            "rating": (r.get("rating") or "").strip() or "—",
            "title": r.get("title") or "—",
        }
        for r in reports
    ]

    # 维度 + 核心决策问题
    dq = dimension_questions or {}
    dimensions_questions_text = "\n".join(
        f"- {d}：{dq.get(d, '（核心投资逻辑）')}"
        for d in dimensions
    )

    # 研报摘要（仅前2批，用于科普背景，不传全文避免 token 超限）
    summary = ""
    if report_batches:
        summary = "\n\n---\n\n".join(report_batches[:2])[:12000]

    user_msg = (
        _INTRO_HTML_TPL
        .replace("{output_path}", "__INTRO_OUTPATH__")
        .format(
            project_name=project_name,
            keywords="、".join(keywords) if keywords else project_name,
            report_count=len(reports),
            reports_json=json.dumps(reports_simple, ensure_ascii=False, indent=2),
            dim_count=len(dimensions),
            dimensions_questions=dimensions_questions_text,
            report_summary=summary or "（暂无研报摘要）",
            theme_css=THEME_CSS,
        )
        .replace("__INTRO_OUTPATH__", "")
    )

    raw = call_claude_text(_INTRO_HTML_SYSTEM, user_msg, timeout=180)

    # 提取 HTML
    html = ""
    for marker in ["<!DOCTYPE", "<!doctype", "<html"]:
        idx = raw.lower().find(marker.lower())
        if idx >= 0:
            end = raw.lower().rfind("</html>")
            if end > idx:
                html = raw[idx:end + 7].strip()
                break
    if not html:
        html = raw.strip()

    return clean_html(html) if html else f"<p style='color:#d97706;padding:20px'>研究背景生成失败</p>"


# ── 单 Tab 重新生成（前端"重新生成"按钮） ────────────────────────────────────

_REGEN_TPL = """你是专业的A股投资研究员。请根据以下用户指令，基于现有版本重新输出完整 HTML。

## 用户指令（最高优先级）
{instruction}

## 需要保持的原则
- 维度：{dimension}，项目：{project_name}
- 保持白底紫色主题（--primary: #7c3aed）
- 保留原有所有有价值的图表数据和文字内容，只按用户指令进行修改
- 所有 ECharts 图表的 series data 必须有真实数据，图表容器数量必须等于 echarts.init() 调用数量
- 所有表格 tbody 必须有完整数据行，不得留空 tbody
- body 和任何包裹容器均不得设置固定 height 或 max-height；页面高度由内容自然撑开
- 不得使用 overflow:auto/scroll；不得添加 position:sticky/fixed 的遮罩或侧边导航
- .kv-grid 用 display:flex; flex-wrap:wrap；.kv-card 用 flex:1 1 180px; min-width:0
- 甘特图必须用双 bar+stack：占位 series data 每项为单值（起始年-xAxis.min），持续 series data 每项为单值（结束年-起始年），禁止 data 用 [x,y] 或 [x,y,z] 数组
- 只输出 HTML 代码（<!DOCTYPE html> 到 </html>），不加任何说明

## 现有版本（供参考，按指令修改）
{current_html}

## 研报原文参考
{report_text}
"""


def regenerate_single_tab(
    project_id: int,
    tab_name: str,
    instruction: str,
    progress_cb=None,
) -> str:
    """前端"重新生成"按钮：针对单个 Tab 重新生成 HTML，写回 summary_json。"""
    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    result = get_rb_result(project_id)
    if not result:
        raise ValueError(f"项目 {project_id} 尚无分析结果，请先完整运行分析")

    try:
        summary = json.loads(result.get("summary_json", "{}"))
    except Exception:
        summary = {}

    tabs: list[dict] = summary.get("tabs", [])
    current_html = ""
    tab_index = -1
    for i, tab in enumerate(tabs):
        if tab.get("name") == tab_name:
            current_html = tab.get("html", "")
            tab_index = i
            break

    if tab_index == -1:
        raise ValueError(f"未找到 tab '{tab_name}'，现有 tabs：{[t.get('name') for t in tabs]}")

    project = get_project(project_id)
    if not project:
        raise ValueError(f"项目 {project_id} 不存在")

    project_name: str = project["name"]
    batches = get_reports_text_batches(project_id)
    if not batches:
        raise ValueError("没有研报全文可供参考，请先抓取研报 PDF")

    combined = "\n\n---\n\n".join(batches)

    _log(f"Kimi 正在根据指令重写【{tab_name}】…")
    regen_prompt = _REGEN_TPL.format(
        instruction=instruction[:2000],
        dimension=tab_name,
        project_name=project_name,
        current_html=current_html,
        report_text=combined,
    )
    raw = kimi_call(regen_prompt)

    # 提取 HTML
    html = ""
    for marker in ["<!DOCTYPE", "<!doctype", "<html"]:
        idx = raw.find(marker) if marker[0] != "<" else raw.lower().find(marker)
        if idx >= 0:
            end = raw.lower().rfind("</html>")
            if end > idx:
                html = raw[idx:end + 7].strip()
                break
    if not html:
        html = raw.strip()

    # 硬检查（最多 1 次修复）
    issues = _quick_html_check(html)
    if issues:
        _log(f"硬检查发现 {len(issues)} 个问题，触发 Kimi 修复…")
        fix_prompt = (
            f"以下 HTML 分析页面有问题，请**定点修复**后重新输出完整 HTML。\n\n"
            f"## 问题列表\n" + "\n".join(f"- {x}" for x in issues) + "\n\n"
            f"## 硬性要求\n维度：{tab_name}，只修复上述问题，其余内容一字不改。\n"
            f"只输出 HTML 代码（<!DOCTYPE html> 到 </html>），不加任何说明。\n\n"
            f"## 当前 HTML\n{html}"
        )
        raw2 = kimi_call(fix_prompt)
        for marker in ["<!DOCTYPE", "<!doctype", "<html"]:
            idx = raw2.lower().find(marker.lower())
            if idx >= 0:
                end = raw2.lower().rfind("</html>")
                if end > idx:
                    html = raw2[idx:end + 7].strip()
                    break

    final_html = clean_html(html)

    tabs[tab_index]["html"] = final_html
    summary["tabs"] = tabs
    upsert_rb_result(project_id, json.dumps(summary, ensure_ascii=False))
    _log(f"【{tab_name}】重新生成完成，已写回数据库")

    return final_html


# ── 进度状态 ──────────────────────────────────────────────────────────────────

_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()


_PIPELINE_STEPS = [
    "准备数据",
    "Claude 拆解维度",
    "Kimi 并行分析",
    "整理结果",
    "完成",
]


def get_progress(project_id: int) -> dict:
    with _progress_lock:
        return dict(_progress.get(project_id, {
            "status": "idle", "phase": "", "message": "",
            "done_dimensions": [], "total_dimensions": 0,
            "step": 0, "total_steps": len(_PIPELINE_STEPS),
            "steps": _PIPELINE_STEPS,
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

    import tempfile
    _project_root = os.path.dirname(os.path.dirname(__file__))
    _tmp_base = os.path.join(_project_root, "tmp")
    os.makedirs(_tmp_base, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix=f"rb_{project_id}_", dir=_tmp_base)

    try:
        update_project_status(project_id, "analyzing")
        _set_progress(project_id, status="analyzing", phase=_PIPELINE_STEPS[0],
                      step=1, total_steps=len(_PIPELINE_STEPS), steps=_PIPELINE_STEPS,
                      total_dimensions=0, done_dimensions=[])

        # Phase 0: 获取研报批次
        _log("准备研报文字批次…")
        batches = get_reports_text_batches(project_id)
        report_count = len(get_rb_reports(project_id))

        if not batches:
            update_project_status(project_id, "error")
            _set_progress(project_id, status="error", message="没有研报全文可分析，请先抓取")
            return

        # Phase 1: Claude 拆解模块 + 扩展维度
        _set_progress(project_id, phase=_PIPELINE_STEPS[1], step=2)
        _log("Claude 正在拆解细分模块，扩展分析维度…")
        try:
            decomp = decompose_project(project_name, user_dimensions, batches)
        except Exception as dc_e:
            _log(f"模块拆解失败，使用默认维度继续（{dc_e}）")
            decomp = {"sub_modules": [], "final_dimensions": user_dimensions, "extra_dimensions": []}

        sub_modules: list[str] = decomp.get("sub_modules", [])
        final_dimensions: list[str] = decomp.get("final_dimensions", user_dimensions)
        dimension_questions: dict = decomp.get("dimension_questions", {})

        if sub_modules:
            _log(f"识别到细分模块：{'、'.join(sub_modules)}")
        _log(f"将分析 {len(final_dimensions)} 个维度：{'、'.join(final_dimensions)}")
        _set_progress(project_id, total_dimensions=len(final_dimensions) + 2)

        # Phase 2-3: 单个 claude agent 并行 dispatch Kimi subagent
        _set_progress(project_id, phase=_PIPELINE_STEPS[2], step=3)
        _log(f"启动 Claude agent，并行生成 {len(final_dimensions)} 个维度 + 产业全景…")

        combined = "\n\n---\n\n".join(batches)[:30000]

        # dims_info 带上每个维度的 core_question，供 Kimi subagent 各自聚焦
        dims_info = [
            {
                "name": d,
                "hash": hashlib.md5(d.encode()).hexdigest()[:8],
                "core_question": dimension_questions.get(d, f"{d}的核心投资逻辑是什么？"),
            }
            for d in final_dimensions
        ]

        # 渲染各模板（固定字段 pre-render，动态占位符保留给 Claude/Kimi 填）
        # 用 __PLACEHOLDER__ 手法保留 {dimension}/{core_question}/{output_path}
        kimi_tpl_rendered = (
            _KIMI_HTML_TPL
            .replace("{dimension}", "__DIM__")
            .replace("{core_question}", "__COREQ__")
            .replace("{output_path}", "__OUTPATH__")
            .format(
                project_name=project_name,
                sub_modules="、".join(sub_modules) if sub_modules else "（从研报提炼）",
                report_text="（完整研报原文已在上方提供，此处省略）",
                theme_css=THEME_CSS,
                echarts_cdn=ECHARTS_CDN,
            )
            .replace("__DIM__", "{dimension}")
            .replace("__COREQ__", "{core_question}")
            .replace("__OUTPATH__", "{output_path}")
        )
        overview_tpl_rendered = _OVERVIEW_TPL.format(
            project_name=project_name,
            dimension_summaries="（由 Kimi 主 agent 从各维度文件末尾 <!-- SUMMARY: ... --> 注释中提取，汇总后填入此处）",
            report_count=report_count,
            dimensions_str="、".join(final_dimensions),
            generated_at=datetime.now().strftime("%Y-%m-%d"),
            theme_css=THEME_CSS,
            echarts_cdn=ECHARTS_CDN,
            output_path=f"{tmp_dir}/overview.html",
        )

        # 研究背景模板：研报摘要仅前2批，不传全文
        reports_meta = get_rb_reports(project_id)
        keywords = project.get("keywords", [])
        reports_simple = [
            {
                "org": r.get("org_name") or "—",
                "researcher": r.get("researcher") or "—",
                "date": r.get("publish_date") or "—",
                "rating": (r.get("rating") or "").strip() or "—",
                "title": r.get("title") or "—",
            }
            for r in reports_meta
        ]
        intro_summary = "\n\n---\n\n".join(batches[:2])[:12000]
        dims_questions_text = "\n".join(
            f"- {d}：{dimension_questions.get(d, '（核心投资逻辑）')}"
            for d in final_dimensions
        )
        # {project_name} 在模板里出现多次（流程图节点等），用 __PROJNAME__ 保护
        intro_tpl_rendered = (
            _INTRO_HTML_TPL
            .replace("{project_name}", "__PROJNAME__")
            .replace("{output_path}", "__INTRO_OUTPATH__")
            .format(
                keywords="、".join(keywords) if keywords else project_name,
                report_count=len(reports_meta),
                reports_json=json.dumps(reports_simple, ensure_ascii=False, indent=2),
                dim_count=len(final_dimensions),
                dimensions_questions=dims_questions_text,
                report_summary=intro_summary or "（暂无研报摘要）",
                theme_css=THEME_CSS,
            )
            .replace("__PROJNAME__", project_name)
            .replace("__INTRO_OUTPATH__", f"{tmp_dir}/intro.html")
        )

        pipeline_user = (
            _PIPELINE_USER_TPL
            .replace("__PROJECT_NAME__", project_name)
            .replace("__DIM_COUNT__", str(len(final_dimensions)))
            .replace("__DIMENSIONS_JSON__", json.dumps(dims_info, ensure_ascii=False, indent=2))
            .replace("__SUB_MODULES__", "、".join(sub_modules) if sub_modules else "（从研报提炼）")
            .replace("__REPORT_TEXT__", combined)
            .replace("__REPORTS_META_JSON__", json.dumps(reports_simple, ensure_ascii=False, indent=2))
            .replace("__KIMI_HTML_TPL__", kimi_tpl_rendered)
            .replace("__OVERVIEW_TPL__", overview_tpl_rendered)
            .replace("__INTRO_TPL__", intro_tpl_rendered)
            .replace("__TMP_DIR__", tmp_dir)
        )

        def _pipeline_progress(msg: str):
            logger.info(f"[pipeline] {msg[:200]}")
            m = _re.match(r"\[PROGRESS\]\s*完成[：:]\s*(.+)", msg.strip())
            if m:
                dim_done = m.group(1).strip()
                with _progress_lock:
                    proj_p = _progress.setdefault(project_id, {})
                    done = proj_p.get("done_dimensions", [])
                    if dim_done not in done:
                        proj_p["done_dimensions"] = done + [dim_done]
                _set_progress(project_id, message=f"完成：{dim_done}")
            elif len(msg) <= 120 and not msg.strip().startswith("<"):
                _set_progress(project_id, message=msg.strip())

        pipeline_result = run_claude_pipeline(
            f"{_PIPELINE_SYSTEM}\n\n{pipeline_user}",
            progress_cb=_pipeline_progress,
            timeout=3600,
        )

        # Phase 4: 从临时文件读取 HTML，Python 侧硬检查
        _set_progress(project_id, phase=_PIPELINE_STEPS[3], step=4)
        raw_tabs: list[dict] = pipeline_result.get("tabs", [])
        dim_htmls: dict[str, str] = {}
        dim_summaries: dict[str, str] = {}  # 从 <!-- SUMMARY: ... --> 提取的核心判断
        overview_html = "<p style='color:#d97706;padding:20px'>产业全景待生成</p>"
        intro_html = "<p style='color:#d97706;padding:20px'>研究背景待生成</p>"

        def _extract_summary_comment(html: str) -> str:
            m = _re.search(r"<!--\s*SUMMARY:\s*([\s\S]+?)\s*-->", html)
            return m.group(1).strip() if m else ""

        for tab in raw_tabs:
            name = tab.get("name", "")
            path = tab.get("path", "")
            html = ""

            if path and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        html = f.read()
                except Exception as e:
                    logger.warning(f"[analyzer] 读取 {path} 失败: {e}")

            if html:
                summary_comment = _extract_summary_comment(html)
                if summary_comment:
                    dim_summaries[name] = summary_comment
                    logger.info(f"[analyzer] {name} SUMMARY: {summary_comment}")
                issues = _quick_html_check(html)
                if issues:
                    logger.warning(f"[analyzer] {name} 硬检查问题（已记录，不阻断）: {issues}")
                html = clean_html(html)

            if not html or len(html) < 500:
                html = f"<p style='color:#dc2626;padding:20px'>【{name}】生成失败</p>"

            if name == "研究背景":
                intro_html = html
            elif name == "产业全景":
                overview_html = html
            elif name in final_dimensions:
                dim_htmls[name] = html
                upsert_rb_analysis(project_id, name, 0,
                                   json.dumps({"html": html}, ensure_ascii=False), "done")
                with _progress_lock:
                    proj_p = _progress.setdefault(project_id, {})
                    done = proj_p.get("done_dimensions", [])
                    if name not in done:
                        proj_p["done_dimensions"] = done + [name]

        # 组装最终 tabs
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
            "dimension_questions": dimension_questions,
            "dimension_summaries": dim_summaries,
        }

        upsert_rb_result(project_id, json.dumps(result_payload, ensure_ascii=False))
        update_project_status(project_id, "done", report_count)
        _set_progress(project_id, status="done", phase=_PIPELINE_STEPS[4], step=5,
                      message=f"分析完成，共 {len(tabs)} 个分析页面")

    except Exception as e:
        logger.exception(f"[analyzer] 项目 {project_id} 分析异常: {e}")
        update_project_status(project_id, "error")
        _set_progress(project_id, status="error", message=f"分析出错：{e}")
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.info(f"[analyzer] 已清理临时目录 {tmp_dir}")


def start_analysis_thread(project_id: int) -> threading.Thread:
    t = threading.Thread(
        target=run_analysis, args=(project_id,),
        daemon=True, name=f"rb-analyzer-{project_id}",
    )
    t.start()
    return t
