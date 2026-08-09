"""
全局企业微信推送 service (market-radar 通用, 所有 AI 分析共用)

历史: 之前 fetcher/cycle_notifier.py 只给 cycle 推送用, 限制太多。
      现在升级为通用 notifier, 所有 AI 智能分析 (mra-chief / mra-emotion /
      mra-news / cycle / 任何未来报告) 都能调用, 支持任意多地址。

地址配置 (跟原 config.WECOM_ROBOT_KEYS 完全兼容):
- 写死在 fetcher/stock_top_algorithms/config.py 第 236-239 行 (info / warn 默认同一 key)
- 环境变量覆盖: WECOM_ROBOT_KEY_INFO / WECOM_ROBOT_KEY_WARN
- **新增任意多地址**: 在 config.py WECOM_ROBOT_KEYS dict 加 "主力群": "key..." 即可
- 也可纯环境变量: WECOM_ROBOT_KEY_<地址名>=xxxx (脚本会扫描)

安全机制:
- WECOM_DRY_RUN=0 (默认) → 真发
- WECOM_DRY_RUN=1 → sandbox, 推送只 print 不真发

API:
- list_addresses() → 所有可用地址列表
- is_configured(address) → 该地址是否配了 key
- send_text(text, address="info") → 推文本
- send_markdown(content, address="info") → 推 markdown (企微原生支持, 更易读)
- send_image(path, address="info") → 推图片
- send_ai_report(title, summary, address="info", full_text=None)
  → 推 AI 报告 (markdown 卡片: # 标题 + **关键摘要** + 可选折叠详情)
- send_cycle_result(report, image_path=None, address="info")
  → 推 cycle 报告 (跟原 cycle_notifier 同接口, 复用)
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# 触发 self_runner.install() (跟其他 cycle_* 文件一致, 必须先于对方模块 import)
from fetcher.cycle_self_runner import install, DATA_ROOT, ALGO_DIR  # noqa: F401

logger = logging.getLogger(__name__)


# ==================== 安全开关 (默认真发, 跟原 auto_runner 一致) ====================
def _strip_html(html: str) -> str:
    """HTML 报告剥成纯文本 (复用 server.py 的实现, 独立可用)."""
    import re
    if not html:
        return ""
    s = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<script[^>]*>.*?</script>", "", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"</?(p|div|br|h[1-6]|li|tr|td|th|blockquote|pre|article|section|header|footer)[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = (s.replace("&nbsp;", " ")
           .replace("&amp;", "&")
           .replace("&lt;", "<")
           .replace("&gt;", ">")
           .replace("&quot;", '"')
           .replace("&#39;", "'"))
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def is_dry_run() -> bool:
    """是否沙盒模式。WECOM_DRY_RUN=1 → sandbox, 默认 0 → 真发。"""
    val = os.environ.get("WECOM_DRY_RUN", "0").strip().lower()
    if val in ("1", "true", "yes", "y", "on"):
        return True
    if val in ("0", "false", "no", "n", "off", ""):
        return False
    return False  # 未知值默认真发


# ==================== 路径 + config 加载 ====================
def _ensure_algo_path() -> None:
    if str(ALGO_DIR) not in sys.path:
        sys.path.insert(0, str(ALGO_DIR))


def _load_keys() -> Dict[str, str]:
    """从 config.WECOM_ROBOT_KEYS 读, 再叠加纯环境变量 WECOM_ROBOT_KEY_<NAME>。

    返回 {address: key} dict, 已 trim, 空 key 过滤掉。
    """
    _ensure_algo_path()
    install()  # idempotent, 确保 config 路径指自管
    keys: Dict[str, str] = {}

    # 1) 从 config.WECOM_ROBOT_KEYS 读
    try:
        from config import WECOM_ROBOT_KEYS
        for addr, k in (WECOM_ROBOT_KEYS or {}).items():
            k = (k or "").strip()
            if k:
                keys[addr] = k
    except Exception as e:
        logger.warning("读 config.WECOM_ROBOT_KEYS 失败: %s", e)

    # 2) 叠加纯环境变量 WECOM_ROBOT_KEY_<地址名>
    #    例如 WECOM_ROBOT_KEY_主力群=xxxx → address="主力群"
    for env_name, env_val in os.environ.items():
        if env_name.startswith("WECOM_ROBOT_KEY_") and env_name not in (
            "WECOM_ROBOT_KEY",          # 通用 key (兼容老 config)
            "WECOM_ROBOT_KEY_INFO",     # info 专用 (兼容老 config)
            "WECOM_ROBOT_KEY_WARN",     # warn 专用 (兼容老 config)
        ):
            addr = env_name[len("WECOM_ROBOT_KEY_"):].strip()
            k = (env_val or "").strip()
            if addr and k:
                keys[addr] = k

    return keys


# ==================== 公共 API ====================
def list_addresses() -> List[str]:
    """返回所有可用地址列表 (按字母排序, 多个名字指向同一 webhook 时去重只保留一个)。

    设计原因: config.WECOM_ROBOT_KEYS 里 'info' 和 'warn' 默认是同一 key,
              用户其实只配了一个 webhook, 但名字被注册成 2 个。前端下拉不该
              让用户选 2 个一样的, 所以按 value 去重。
    """
    keys = _load_keys()
    seen_values: set = set()
    unique: List[str] = []
    for name in sorted(keys.keys()):
        v = (keys.get(name) or "").strip()
        if v and v not in seen_values:
            seen_values.add(v)
            unique.append(name)
    return unique


def is_configured(address: str = "info") -> bool:
    """指定地址是否配了 key。"""
    return address in _load_keys()


def get_key(address: str) -> str:
    """取某地址的 key, 未配时返回空串。"""
    return _load_keys().get(address, "")


# ==================== 底层 post ====================
_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
_MAX_IMAGE_BYTES = 2 * 1024 * 1024
_MAX_MARKDOWN_BYTES = 4096  # 企微 markdown 长度上限


def _post(payload: dict, address: str) -> bool:
    """实际推送, dry-run 时只 print。"""
    if is_dry_run():
        msgtype = payload.get("msgtype", "?")
        if msgtype == "text":
            preview = payload.get("text", {}).get("content", "")
        elif msgtype == "markdown":
            preview = payload.get("markdown", {}).get("content", "")
        elif msgtype == "image":
            preview = f"[image base64 {len(payload.get('image', {}).get('base64', ''))} chars]"
        else:
            preview = json.dumps(payload, ensure_ascii=False)[:200]
        print("=" * 60)
        print(f"[WECOM_DRY_RUN=1, sandbox] 推 {msgtype} 到 address='{address}':")
        print(preview[:500] + ("..." if len(preview) > 500 else ""))
        print("=" * 60)
        return True

    key = get_key(address)
    if not key:
        print(f"[wecom] address='{address}' 未配 key, 跳过推送 (调 list_addresses() 看可用地址)")
        return False

    # 调对方底层 notifier (走 self_runner 已 install 的 config 路径)
    _ensure_algo_path()
    install()
    try:
        import requests as _req
    except ImportError:
        logger.warning("requests 没装, 推不了")
        return False

    try:
        # 读 NOTIFY_PROXIES (代理配置, 跟原 config 一致)
        from config import NOTIFY_PROXIES
        proxies = NOTIFY_PROXIES
    except Exception:
        proxies = None

    try:
        resp = _req.post(
            _WEBHOOK_URL + key,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json;charset=utf-8"},
            timeout=10,
            proxies=proxies,
        )
        result = resp.json()
        if result.get("errcode", 0) != 0:
            print(f"[wecom] 企微返回错误 (address={address}): {result}")
            return False
        return True
    except Exception as e:
        print(f"[wecom] 推送失败 (address={address}): {e}")
        return False


# ==================== 公开 send API ====================
def send_text(content: str, address: str = "info") -> bool:
    """推文本消息。address 默认 'info'。"""
    payload = {"msgtype": "text", "text": {"content": content}}
    return _post(payload, address)


def send_markdown(content: str, address: str = "info") -> bool:
    """推 markdown 消息 (企微原生支持, 长度 ≤ 4096 字节)。超长会被截断。
    Markdown 语法: # 标题 / **加粗** / [link](url) / > 引用 / <font color="info">文本</font>
    """
    if len(content.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        # 截断到 4096 字节, 留个省略号
        truncated = content.encode("utf-8")[:_MAX_MARKDOWN_BYTES - 20].decode("utf-8", errors="ignore")
        content = truncated + "\n\n...(内容过长已截断)"
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    return _post(payload, address)


def send_image(image_path: Union[str, Path], address: str = "info") -> bool:
    """推图片 (≤ 2MB, base64 + md5)。"""
    path = Path(image_path)
    if not path.exists():
        print(f"[wecom] 图片不存在, 跳过: {path}")
        return False
    data = path.read_bytes()
    if len(data) > _MAX_IMAGE_BYTES:
        print(f"[wecom] 图片 {len(data) // 1024}KB 超过 2MB 上限, 跳过: {path.name}")
        return False
    payload = {
        "msgtype": "image",
        "image": {
            "base64": base64.b64encode(data).decode("utf-8"),
            "md5": hashlib.md5(data).hexdigest(),
        },
    }
    return _post(payload, address)


# ==================== AI 报告专用 ====================
def _split_markdown_chunks(text: str, max_bytes: int) -> List[str]:
    """把一段长文本按段落 (\n\n) 切成多个 <= max_bytes 的块。

    设计目标:
    - 不在段落中间切, 保持语义完整
    - 实在一个段落就超 max_bytes, 才在句末 (。!?\n) 切
    - 极小段落 (< max_bytes) 直接合到下一段
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]

    # 先按 \n\n 切段落
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: List[str] = []
    buf: List[str] = []
    buf_bytes = 0

    def _flush():
        nonlocal buf, buf_bytes
        if buf:
            chunks.append("\n\n".join(buf))
        buf, buf_bytes = [], 0

    for p in paragraphs:
        p_bytes = len(p.encode("utf-8"))
        # 单段就超 max_bytes — 按句末切
        if p_bytes > max_bytes:
            _flush()
            chunks.extend(_split_long_paragraph(p, max_bytes))
            continue
        # 当前 buf + 新段还放得下
        if buf_bytes + p_bytes + 2 <= max_bytes:  # +2 for \n\n
            buf.append(p)
            buf_bytes += p_bytes + 2
        else:
            _flush()
            buf.append(p)
            buf_bytes = p_bytes
    _flush()
    return chunks


def _split_long_paragraph(text: str, max_bytes: int) -> List[str]:
    """单段超过 max_bytes 时, 按句末标点切。"""
    chunks: List[str] = []
    buf = ""
    buf_bytes = 0
    # 优先在 。!?\n 处切
    i = 0
    while i < len(text):
        ch = text[i]
        buf += ch
        buf_bytes += len(ch.encode("utf-8"))
        # 句末标点 + 后面有内容 + 已经攒够 60% 才切
        if ch in "。!?\n" and buf_bytes >= int(max_bytes * 0.6):
            # 看后面还有没有字符
            if i < len(text) - 1:
                chunks.append(buf)
                buf = ""
                buf_bytes = 0
        i += 1
    if buf:
        if chunks and len(buf.encode("utf-8")) < 50:
            # 末尾碎片 < 50 字节, 合并到上一块
            chunks[-1] += buf
        else:
            chunks.append(buf)
    # 兜底: 还是超就硬切
    final: List[str] = []
    for c in chunks:
        cb = c.encode("utf-8")
        if len(cb) <= max_bytes:
            final.append(c)
        else:
            for k in range(0, len(cb), max_bytes):
                final.append(cb[k:k + max_bytes].decode("utf-8", errors="ignore"))
    return final


def send_ai_report(
    title: str,
    summary: str,
    address: str = "info",
    full_text: Optional[str] = None,
) -> bool:
    """推 AI 报告 (markdown 卡片, 长内容自动分段发多条)。

    消息结构 (单条时):
        # 标题

        摘要

        --- 完整报告 (1/3) ---

        <chunk 1>

    多条时 (N=3):
        第 1 条: # 标题 + 摘要 + --- 完整报告 (1/3) --- + chunk1
        第 2 条: # 标题 (续 2/3) + chunk2
        第 3 条: # 标题 (续 3/3) + chunk3

    Returns:
        bool: 所有 chunk 都成功才 True, 任一失败立刻停 (避免半发状态)
    """
    full_text = (full_text or "").strip()

    if not full_text:
        # 单条
        content = f"# {title}\n\n{summary.strip()}"
        return send_markdown(content, address=address)

    # 算第一段的 header 字节, 后续段 header 更小 (只有 "标题 (续 X/N)")
    first_header = f"# {title}\n\n{summary.strip()}\n\n<font color=\"comment\">--- 完整报告 (1/{{N}}) ---</font>\n\n"
    other_header = f"# {title} (续 {{i}}/{{N}})\n\n"
    # 各 chunk 的最大字节
    first_chunk_max = _MAX_MARKDOWN_BYTES - len(first_header.encode("utf-8")) - 50
    other_chunk_max = _MAX_MARKDOWN_BYTES - len(other_header.format(i=1, N=2).encode("utf-8")) - 50
    # 用更严的 (other 更小) 保险一点
    chunk_max = min(first_chunk_max, other_chunk_max)
    chunk_max = max(chunk_max, 200)  # 起码 200 字节

    chunks = _split_markdown_chunks(full_text, chunk_max)
    total = len(chunks)

    for idx, chunk in enumerate(chunks, 1):
        if idx == 1:
            content = (
                f"# {title}\n\n"
                f"{summary.strip()}\n\n"
                f"<font color=\"comment\">--- 完整报告 ({idx}/{total}) ---</font>\n\n"
                f"{chunk}"
            )
        else:
            content = f"# {title} (续 {idx}/{total})\n\n{chunk}"
        ok = send_markdown(content, address=address)
        if not ok:
            logger.warning("[send_ai_report] 第 %d/%d 段推送失败, 中断后续", idx, total)
            return False
        # 真发模式: 短间隔避免触发企微限流
        if not is_dry_run() and idx < total:
            import time
            time.sleep(0.3)
    return True


# ==================== 兼容 cycle 旧 API ====================
def send_cycle_result(
    report: Dict[str, Any],
    image_path: Optional[Union[str, Path]] = None,
    address: str = "info",
) -> Dict[str, Any]:
    """推周期信号 (跟 cycle 旧接口一致, 复用 send_text + send_image)。"""
    if not is_configured(address):
        return {
            "configured": False,
            "dry_run": is_dry_run(),
            "text_ok": False,
            "image_ok": False,
            "skipped_reason": f"address='{address}' 未配 key (调 list_addresses() 看可用)",
        }

    # 组装文本 (跟原 _format_cycle_message 一致)
    date = report.get("date", "未知")
    score = report.get("cycle_score", 0)
    phase = report.get("phase", "未知")
    emoji = report.get("emoji", "")
    advice = report.get("advice", "")
    signal_state = report.get("signal_state", "normal")
    state_emoji = {
        "normal": "⚪", "near_top": "🔴", "near_bottom": "🟢", "cooldown": "⏳",
    }.get(signal_state, "⚪")

    lines = [
        "📊 A股牛熊周期定位",
        f"日期: {date}",
        f"周期分数: {score}",
        f"市场阶段: {emoji} {phase}",
        f"操作建议: {advice}",
        f"信号状态: {state_emoji} {signal_state}",
    ]
    last_sig = report.get("last_signal")
    if last_sig:
        sig_label = {
            "escape_top": "🔴 重仓逃顶", "buy_bottom": "🟢 重仓抄底",
            "re_entry": "🔵 认错回补", "re_entry_stop": "🟠 回补止损",
        }.get(last_sig.get("type"), last_sig.get("type"))
        lines.append(f"最近重仓信号: {sig_label} ({last_sig.get('date')})")
    last_light = report.get("last_light_signal")
    if last_light:
        lines.append(f"最近轻仓信号: {last_light.get('type')} ({last_light.get('date')})")
    fr = report.get("freshness")
    if fr:
        level_cn = {"high": "🟢 高", "medium": "🟡 中", "low": "🔴 低"}.get(fr.get("level"), fr.get("level"))
        lines.append(f"数据可信度: {level_cn} ({fr.get('fresh_count')}/{fr.get('total')}, 截至 {fr.get('as_of')})")

    text_ok = send_text("\n".join(lines), address=address)

    image_ok = False
    if image_path is None:
        image_path = DATA_ROOT / "pic" / "cycle_position.png"
    image_path = Path(image_path)
    if image_path.exists():
        image_ok = send_image(image_path, address=address)
    else:
        logger.info("周期定位图不存在, 跳过图片推送: %s", image_path)

    return {
        "configured": True,
        "dry_run": is_dry_run(),
        "text_ok": text_ok,
        "image_ok": image_ok,
        "address": address,
    }


# ==================== CLI ====================
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true", help="列出所有可用地址")
    p.add_argument("--text", help="推一段文本 (--address 指定地址)")
    p.add_argument("--markdown", help="推一段 markdown")
    p.add_argument("--ai-report", action="store_true", help="推一段示例 AI 报告")
    p.add_argument("--cycle", action="store_true", help="推 cycle 报告 (从 disk 读)")
    p.add_argument("--address", default="info", help="推送地址 (默认 info)")
    p.add_argument("--dry-run", action="store_true", help="强制 sandbox")
    p.add_argument("--force-send", action="store_true", help="强制真发, 覆盖 env")
    args = p.parse_args()

    if args.dry_run:
        os.environ["WECOM_DRY_RUN"] = "1"
    elif args.force_send:
        os.environ["WECOM_DRY_RUN"] = "0"

    print(f"沙盒模式: {is_dry_run()} (WECOM_DRY_RUN={os.environ.get('WECOM_DRY_RUN', '(未设, 默认 0 真发)')})")
    print(f"可用地址: {list_addresses()}")
    print()

    if args.list:
        pass
    elif args.text:
        ok = send_text(args.text, address=args.address)
        print(f"text ok: {ok}")
    elif args.markdown:
        ok = send_markdown(args.markdown, address=args.address)
        print(f"markdown ok: {ok}")
    elif args.ai_report:
        ok = send_ai_report(
            title="📊 示例 AI 报告",
            summary="**情绪阶段**: 🟡 中性\n\n**市场**: 震荡偏弱, 关注 3200 支撑",
            full_text="- 沪深300 跌 0.5%\n- 成交萎缩\n- 北向净流出 30 亿",
            address=args.address,
        )
        print(f"ai report ok: {ok}")
    elif args.cycle:
        json_path = DATA_ROOT / "results" / "cycle_signal_latest.json"
        if json_path.exists():
            report = json.loads(json_path.read_text(encoding="utf-8"))
            result = send_cycle_result(report, address=args.address)
            print(f"cycle result: {result}")
        else:
            print(f"找不到 {json_path}")
    else:
        p.print_help()
