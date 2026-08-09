"""
股票搜索与批量导入辅助模块。

功能：
- 从 QUANT_DATA_ROOT/stock-trading-data-pro/*.csv 构建全量 code→name 索引
  （带 JSON 磁盘缓存，避免每次请求遍历 5000+ 文件）
- 拼音简写索引（pypinyin FIRST_LETTER），支持 gzmt → 贵州茅台 这类搜索
- 模糊搜索：代码 / 拼音简写 / 中文名片段
- 批量导入解析：宽容解析 CSV 文本（表头识别、多种代码写法、注释行、去重）

缓存文件：db/stock_names_cache.json，默认 24 小时内直接复用。
"""
import csv
import io
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_PATH = _PROJECT_ROOT / "db" / "stock_names_cache.json"
_CACHE_TTL_SECONDS = 24 * 3600

# 内存缓存：{"code": name}
_index: dict[str, str] | None = None
# 拼音简写索引：abbr -> [code, ...]（保持构建顺序，即文件排序顺序，稳定）
_abbr_index: dict[str, list[str]] | None = None
_index_lock = threading.Lock()

_CODE_HEADER_NAMES = {"code", "代码", "股票代码", "symbol", "ts_code", "证券代码", "股票"}
_NOTE_HEADER_NAMES = {"note", "备注", "注释", "remark"}


def _normalize_code(raw: str) -> str:
    """归一化为 6 位数字代码；非法输入返回空串。
    支持 600519 / 600519.SH / sh600519 / SZ300750.SZ 等写法。
    """
    code = (raw or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break
    code = code.split(".")[0].strip()
    if code.isdigit() and len(code) <= 6:
        return code.zfill(6)
    return ""


def _get_data_root() -> Path | None:
    from quant.loader import DATA_ROOT
    return DATA_ROOT


def _read_name_from_csv(csv_path: Path) -> str:
    """从单个量价 CSV 尾部读取股票名称（第 2 列）。只 seek 尾部 8KB，速度快。"""
    try:
        with open(csv_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 8192))
            tail = f.read().decode("gbk", errors="ignore")
        for line in reversed(tail.strip().splitlines()):
            line = line.strip()
            if not line:
                continue
            cols = next(csv.reader([line]))
            if len(cols) > 1 and cols[1].strip():
                return cols[1].strip()
    except Exception:
        pass
    return ""


def _build_index_from_disk() -> dict[str, str]:
    """遍历 stock-trading-data-pro 构建 code→name 索引（耗时数秒，只在缓存失效时执行）。"""
    root = _get_data_root()
    if not root:
        return {}
    trading_dir = root / "stock-trading-data-pro"
    if not trading_dir.is_dir():
        return {}
    index: dict[str, str] = {}
    for fp in sorted(trading_dir.glob("*.csv")):
        code = _normalize_code(fp.stem)
        if not code:
            continue
        name = _read_name_from_csv(fp)
        if name:
            index[code] = name
    return index


def _load_or_build_index() -> dict[str, str]:
    """优先读 JSON 磁盘缓存（24h 内有效），否则重建并写缓存。"""
    if _CACHE_PATH.exists():
        try:
            payload = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            built_at = payload.get("built_at", 0)
            index = payload.get("index", {})
            if index and (time.time() - built_at) < _CACHE_TTL_SECONDS:
                return {str(k): str(v) for k, v in index.items()}
        except Exception:
            pass  # 缓存损坏则重建

    index = _build_index_from_disk()
    if index:
        try:
            _CACHE_PATH.write_text(
                json.dumps({"built_at": time.time(), "index": index}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[stock_search] 写名称缓存失败: %s", exc)
    return index


def _abbr_of(name: str) -> str:
    from pypinyin import lazy_pinyin, Style
    return "".join(lazy_pinyin(name, style=Style.FIRST_LETTER)).lower()


def _ensure_index() -> tuple[dict[str, str], dict[str, list[str]]]:
    """返回 (code→name, abbr→[code])，首次调用时构建（带锁，只建一次）。"""
    global _index, _abbr_index
    if _index is not None and _abbr_index is not None:
        return _index, _abbr_index
    with _index_lock:
        if _index is None:
            t0 = time.time()
            _index = _load_or_build_index()
            logger.info("[stock_search] 名称索引就绪: %d 只, 耗时 %.1fs", len(_index), time.time() - t0)
        if _abbr_index is None:
            abbr: dict[str, list[str]] = {}
            for code, name in _index.items():
                a = _abbr_of(name)
                if a:
                    abbr.setdefault(a, []).append(code)
            _abbr_index = abbr
    return _index, _abbr_index


def warm_up() -> None:
    """后台预热索引（server 启动时可选调用）。"""
    threading.Thread(target=_ensure_index, daemon=True, name="stock-search-warmup").start()


def lookup_name(code: str) -> str:
    """按 6 位代码查名称（索引未覆盖时回落本地 CSV 查名逻辑）。"""
    norm = _normalize_code(code)
    if not norm:
        return ""
    index, _ = _ensure_index()
    name = index.get(norm, "")
    if name:
        return name
    try:
        from agent.query import lookup_stock_names
        found = lookup_stock_names([norm])
        if found and found[0].get("name"):
            return found[0]["name"]
    except Exception:
        pass
    return ""


def search_stocks(q: str, limit: int = 10, prefix_abbr: bool = True) -> list[dict]:
    """模糊搜索股票。

    匹配优先级：
    1. 代码写法（含 sh/sz/bj 前缀或 .SH 后缀）→ 精确代码
    2. 全字母且长度 2-8 → 拼音简写精确匹配；prefix_abbr=True 且无精确命中时
       做简写前缀匹配（供输入联想；POST 添加时应传 False，避免瞎输入误命中）
    3. 其他（含中文）→ 名称包含匹配
    """
    query = (q or "").strip()
    if not query:
        return []

    index, abbr_index = _ensure_index()

    # 1. 代码写法
    if re.fullmatch(r"(?i)(sh|sz|bj)?\d{1,6}(\.(sh|sz|bj))?", query):
        norm = _normalize_code(query)
        if not norm:
            return []
        return [{"code": norm, "name": index.get(norm, "")}]

    def _hit(code: str) -> dict:
        return {"code": code, "name": index.get(code, "")}

    # 2. 拼音简写
    if re.fullmatch(r"[A-Za-z]{2,8}", query):
        q_lower = query.lower()
        exact = abbr_index.get(q_lower, [])
        if exact:
            return [_hit(c) for c in exact[:limit]]
        if not prefix_abbr:
            return []
        # 前缀匹配（如 gzm 命中 gzmt），供输入联想
        prefix_hits: list[str] = []
        for abbr, codes in abbr_index.items():
            if abbr.startswith(q_lower):
                prefix_hits.extend(codes)
            if len(prefix_hits) >= limit:
                break
        return [_hit(c) for c in prefix_hits[:limit]]

    # 3. 中文/其他 → 名称包含匹配
    hits = [(c, n) for c, n in index.items() if query in n]
    return [{"code": c, "name": n} for c, n in hits[:limit]]


# ── 批量导入解析 ─────────────────────────────────────────────────────────────

def parse_import_text(text: str) -> tuple[list[dict], list[dict]]:
    """宽容解析导入文本。

    返回 (rows, failed)：
      rows   — [{"code": "600519", "note": "..."}]，已归一化、文件内已去重
      failed — [{"raw": 原始行, "reason": 原因}]

    规则：
    - 空行、# 开头注释行直接忽略（不计入 failed）
    - 首个有效行若含常见表头词（code/代码/股票代码/symbol/ts_code 等）则识别为表头
    - 无表头时第一列视为代码；有 note/备注 列时第二列信息作为备注
    - 代码归一化失败的行计入 failed
    """
    rows: list[dict] = []
    failed: list[dict] = []
    seen: set[str] = set()

    if not text or not text.strip():
        return rows, failed

    # 统一分隔符：支持逗号 / 制表符 / 分号
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content_lines = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        content_lines.append(stripped)

    if not content_lines:
        return rows, failed

    def _split(line: str) -> list[str]:
        if "\t" in line:
            parts = line.split("\t")
        elif ";" in line:
            parts = line.split(";")
        else:
            parts = next(csv.reader(io.StringIO(line)))
        parts = [p.strip().strip('"').strip("'") for p in parts]
        # 无逗号/制表符/分号时，尝试按空白切分（如 "600036 招商银行"）
        if len(parts) == 1 and re.search(r"\s", parts[0]):
            parts = parts[0].split(None, 1)
        return parts

    first_cells = _split(content_lines[0])
    header_map: dict[str, int] = {}
    has_header = False
    for idx, cell in enumerate(first_cells):
        cell_norm = cell.strip().lower()
        if cell_norm in {h.lower() for h in _CODE_HEADER_NAMES}:
            header_map["code"] = idx
            has_header = True
        elif cell_norm in {h.lower() for h in _NOTE_HEADER_NAMES}:
            header_map["note"] = idx
            has_header = True

    data_lines = content_lines[1:] if has_header else content_lines

    for line in data_lines:
        cells = _split(line)
        if has_header:
            raw_code = cells[header_map["code"]] if header_map.get("code", 0) < len(cells) else ""
            raw_note = cells[header_map["note"]] if "note" in header_map and header_map["note"] < len(cells) else ""
        else:
            raw_code = cells[0] if cells else ""
            raw_note = cells[1] if len(cells) > 1 else ""

        code = _normalize_code(raw_code)
        if not code:
            failed.append({"raw": line, "reason": f"无法识别的股票代码: {raw_code or '(空)'}"})
            continue
        if code in seen:
            continue  # 文件内去重（静默）
        seen.add(code)
        rows.append({"code": code, "note": raw_note[:200]})

    return rows, failed
