---
name: mra-intraday
description: 盘中快速播报 — 量化对比早盘判断与盘中实时数据，输出100字以内的盘感摘要
---

# 盘中快速播报

盘中数据不完整（龙虎榜未出、收盘量价未定），不做深度分析。**三个数字说清楚现在是什么状态。**

---

## 数据读取

```bash
cat /tmp/mra-{RUN_ID}/emotion.json
cat /tmp/mra-{RUN_ID}/news.json
python agent/query.py zt_pool
python agent/query.py volume_breakout
python agent/query.py context
```

只取 `context` 中的最新 agent_summary 里的 `main_theme` 和早盘的 `market_status`，不做完整分析。

---

## Step 1：盘中情绪检查（三个量化问题）

**问题1：情绪变化方向**

用当前实时数据 vs 早盘 `emotion.json` 做对比：
- 涨停家数变化：现在多少 vs 早盘多少？
- 炸板率变化：当前炸板率 vs 早盘水平？

**关键阈值**：
- 炸板率突破30% → 情绪出现分歧，提示
- 炸板率突破50% → 退潮确认，立刻体现在摘要

**问题2：主线是否在验证**

对比早盘判断的主线板块，现在：
- 主线板块涨停数是增加还是减少？
- 龙头股是否仍在封板（量能是否萎缩至5日均量50%以下）？

**关键信号**：`炸板率 > 40%` AND `龙头量能 < 5日均量50%` → 次日转折概率82%（东方财富案例统计），立刻在摘要中写明。

**问题3：有没有新催化剂**

新闻分析师的 `news.json` 中有没有 `is_new: true` 且 `catalyst_strength: 强` 的新催化剂？
- 有 → `new_catalyst: true`，在摘要中一句话说明是什么
- 没有 → `new_catalyst: false`

---

## 输出

调用 write_result 保存结果：

```bash
python agent/write_result.py --run-type intraday --result '<JSON>'
```

```json
{
  "run_type": "intraday",
  "run_time": "2026-06-01 10:03:00",
  "market_status": {
    "mode": "正常|谨慎|不操作",
    "emotion_score": "冰点|启动|发酵|高潮|分歧|退潮",
    "reason": "一句话，必须引用具体数字：涨停数、炸板率、龙头封板状态"
  },
  "intraday_pulse": "100字以内。格式：[情绪变化方向+数字] + [主线状态] + [新催化剂或无]。例：'炸板率从早盘8%升至23%，主线低空经济涨停从12只降至9只，龙头仍封板但量能开始萎缩；无新催化剂，建议谨慎。'",
  "theme_status": "验证中|退潮|无明显主线",
  "new_catalyst": false
}
```

`intraday_pulse` 要有具体数字，不写定性废话。禁止出现"市场有所走弱"这种无法核实的表述，改为"炸板率从X%升至Y%"。
