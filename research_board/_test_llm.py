"""
单独测试 codex exec 调用是否正常工作。
运行：/home/runist/miniconda3/envs/qtrade/bin/python research_board/_test_llm.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_env = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
if os.path.exists(_env):
    for line in open(_env):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from research_board.llm_runner import _call_llm, _extract_json

# ── Test 1: 基础连通性 ───────────────────────────────────────
print("\n===== Test 1: 基础连通性 =====")
try:
    result = _call_llm('只回复这个JSON，不要其他内容：{"status": "ok", "msg": "连接成功"}', timeout=60)
    print(f"原始输出:\n{result[:300]}")
    parsed = _extract_json(result)
    print(f"解析结果: {parsed}")
    print("✓ 连通性 OK" if parsed.get("status") == "ok" else "⚠ 连通但 JSON 解析异常")
except Exception as e:
    print(f"✗ 失败: {e}")
    sys.exit(1)

# ── Test 2: 模拟研报分析（小样本）───────────────────────────
print("\n===== Test 2: 模拟研报分析 =====")
FAKE_REPORT = """
【光模块行业深度报告】国泰君安 2025-03-15 评级：增持 目标价：85元

核心观点：
1. 1.6T光模块进入量产爬坡期，AI服务器单台用量从24个增至72个，价值量提升3倍
2. 中继器市场份额：中际旭创50%、新易盛20%、天孚通信12%
3. DSP芯片成本占比从800G的15%提升至1.6T的22%，博通垄断供应
4. 预计2025年光模块行业规模达600亿元，同比增长45%
5. 核心风险：硅光技术路线替代传统方案，时间窗口3-5年

目标公司：中际旭创（300308）目标价85元，对应25年PE 28倍
"""

PROMPT = """你是A股投资研究员，分析以下研报，提取竞争格局信息。

研报内容：
{report}

只输出如下JSON，不要其他文字：
```json
{{
  "dimension": "竞争格局与核心标的",
  "key_findings": [
    {{"point": "核心观点", "detail": "详细说明含数据", "confidence": "high"}}
  ],
  "stock_mentions": [
    {{"name": "公司名", "code": "代码", "context": "角色", "rating": "评级", "target_price": "目标价"}}
  ],
  "summary": "100字以内结论"
}}
```""".format(report=FAKE_REPORT)

try:
    result = _call_llm(PROMPT, timeout=120)
    print(f"原始输出（前500字）:\n{result[:500]}")
    parsed = _extract_json(result)
    print(f"\n解析结果:")
    import json
    print(json.dumps(parsed, ensure_ascii=False, indent=2)[:800])
    print("✓ 研报分析 OK")
except Exception as e:
    print(f"✗ 失败: {e}")

print("\n===== LLM 测试完成 =====")
