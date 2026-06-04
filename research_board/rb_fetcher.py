"""
research_board — 研报抓取 + PDF 全文提取
使用 curl_cffi 模拟 Chrome TLS 指纹，绕过 dfcfw.com 的 JA3 指纹检测，直接下载真实 PDF。
HTML 摘要页作为 PDF 提取失败时的降级方案。
"""

import logging
import time
from datetime import datetime, timedelta

import pytz
import pdfplumber
from curl_cffi import requests as cf_requests
from bs4 import BeautifulSoup

from research_board.rb_storage import (
    get_project,
    get_rb_reports,
    insert_rb_report,
    update_rb_report_pdf_status,
    update_rb_report_text,
    update_project_status,
)

logger = logging.getLogger(__name__)

_TZ_BEIJING = pytz.timezone("Asia/Shanghai")

_SESSION = cf_requests.Session(impersonate="chrome124")
_SESSION.headers.update({
    "Referer": "https://data.eastmoney.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
})
_LAST_EM_CALL = [0.0]
_EM_MIN_INTERVAL = 1.0  # 东财限速 ≥1s

BASE_URL    = "https://reportapi.eastmoney.com/report/list"
HTML_TPL    = "https://data.eastmoney.com/report/zw_industry.jshtml?infocode={info_code}"
PDF_URL_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"

MAX_CHARS_PER_BATCH = 350_000  # kimi-k2.6 262K token 留余量
_PDF_INTERVAL = 1.0            # PDF 下载间隔
_HTML_INTERVAL = 1.2           # HTML 页面降级抓取间隔


def _em_get(url, **kwargs):
    """串行限速 GET，间隔 ≥1s + 随机抖动。"""
    import random
    wait = _EM_MIN_INTERVAL - (time.time() - _LAST_EM_CALL[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.4))
    try:
        return _SESSION.get(url, **kwargs)
    finally:
        _LAST_EM_CALL[0] = time.time()


def _today_beijing() -> str:
    return datetime.now(_TZ_BEIJING).strftime("%Y-%m-%d")


def _keyword_match(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


def _extract_html_abstract(info_code: str) -> str:
    """
    从东财研报 HTML 页面提取摘要正文。
    通常能拿到 300-2000 字的核心观点，不需要登录。
    """
    url = HTML_TPL.format(info_code=info_code)
    try:
        r = _em_get(url, timeout=20)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        # 过滤掉免责声明和短段落，只取正文
        paras = []
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) < 30:
                continue
            if any(kw in text for kw in ["郑重声明", "东方财富网发布", "风险自担", "不构成任何投资建议"]):
                continue
            paras.append(text)
        return "\n".join(paras)
    except Exception as e:
        logger.debug(f"[rb_fetcher] HTML 摘要失败 {info_code}: {e}")
        return ""


# ── 元数据抓取 ─────────────────────────────────────────────────────────────

def fetch_reports_for_project(project_id: int, progress_cb=None) -> int:
    """抓取研报元数据，同时存储 infoCode 用于后续摘要提取。"""
    project = get_project(project_id)
    if not project:
        raise ValueError(f"project {project_id} not found")

    keywords: list[str] = project["keywords"]
    qtype_filter: list[int] = project["qtype_filter"]
    days_back: int = project["days_back"]

    end   = _today_beijing()
    begin = (datetime.now(_TZ_BEIJING) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    _log(f"正在抓取研报（{begin} 至 {end}），关键词：{', '.join(keywords) or '全部'}")
    total_new = 0

    for qtype in qtype_filter:
        page = 1
        while True:
            try:
                params = {
                    "qType": qtype, "pageSize": 100, "pageNo": page,
                    "industryCode": "*", "industry": "*",
                    "rating": "*", "ratingChange": "*",
                    "beginTime": begin, "endTime": end,
                    "fields": "", "orgCode": "", "code": "*", "rcode": "",
                }
                resp = _em_get(BASE_URL, params=params, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                raw   = data.get("data", [])
                items = raw if isinstance(raw, list) else raw.get("list", [])

                count_this_page = 0
                for item in items:
                    title = str(item.get("title", "") or "")
                    if not title or not _keyword_match(title, keywords):
                        continue

                    info_code  = str(item.get("infoCode", "") or "")
                    stock_code = str(item.get("stockCode", "") or "")
                    stock_name = str(item.get("stockName", "") or "")
                    org_name   = str(item.get("orgSName", "") or item.get("orgName", "") or "")
                    researcher = str(item.get("researcher", "") or item.get("author", "") or "")
                    pub_raw    = str(item.get("publishDate", "") or "")
                    pub_date   = pub_raw[:10] if pub_raw else ""
                    rating     = str(item.get("emRatingName", "") or "")
                    aim_price  = str(item.get("indvAimPriceT", "") or "")
                    # report_url 存前端可用的 PDF 链接（含 infoCode），后端抓摘要用 HTML_TPL
                    report_url = PDF_URL_TPL.format(info_code=info_code) if info_code else ""

                    insert_rb_report(
                        project_id, title, stock_code, stock_name, org_name,
                        researcher, pub_date, rating, aim_price, report_url, qtype,
                    )
                    total_new += 1
                    count_this_page += 1

                total_pages = int(data.get("TotalPage", 1) or 1)
                QTYPE_NAMES = {0: "个股", 1: "行业", 2: "宏观", 3: "策略"}
                qname = QTYPE_NAMES.get(qtype, f"类型{qtype}")
                if count_this_page > 0:
                    _log(f"  {qname}报告 第 {page}/{total_pages} 页，本页命中 {count_this_page} 篇")
                else:
                    _log(f"  {qname}报告 第 {page}/{total_pages} 页，扫描中…")

                if page >= total_pages:
                    break
                page += 1

            except Exception as e:
                QTYPE_NAMES = {0: "个股", 1: "行业", 2: "宏观", 3: "策略"}
                qname = QTYPE_NAMES.get(qtype, f"类型{qtype}")
                logger.warning(f"[rb_fetcher] qtype={qtype} page={page} 失败: {e}")
                _log(f"  {qname}报告 第 {page} 页请求超时，跳过后续页面")
                break

    _log(f"研报抓取完成，共命中 {total_new} 篇")
    return total_new


# ── PDF 下载 + 文本提取（curl_cffi 模拟 Chrome TLS 指纹）─────────────────

def _extract_pdf_text(pdf_bytes: bytes, max_chars: int = 80_000) -> str:
    """用 pdfplumber 从 PDF bytes 提取文本，最多 max_chars 字。"""
    import io
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            parts = []
            total = 0
            for page in pdf.pages:
                t = page.extract_text() or ""
                parts.append(t)
                total += len(t)
                if total >= max_chars:
                    break
            return "\n".join(parts)[:max_chars]
    except Exception as e:
        logger.debug(f"[rb_fetcher] pdfplumber 失败: {e}")
        return ""


def download_pdfs_for_project(project_id: int, max_count: int = 200,
                               progress_cb=None) -> int:
    """
    下载 PDF 并提取全文（curl_cffi 绕过 TLS 指纹检测）。
    PDF 失败时降级到 HTML 摘要页。
    """
    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    reports = get_rb_reports(project_id, pdf_status="pending")
    _log(f"开始下载全文，共 {len(reports)} 篇待处理，本次上限 {max_count} 篇")

    done = 0
    total_cap = min(len(reports), max_count)
    for i, r in enumerate(reports[:max_count]):
        url = r.get("report_url", "") or ""
        info_code = ""
        if "H3_" in url:
            try:
                info_code = url.split("H3_")[1].split("_1.pdf")[0]
            except Exception:
                pass

        short_title = r['title'][:30] + ('…' if len(r['title']) > 30 else '')
        label = f"[{i+1}/{total_cap}]"

        if not info_code:
            update_rb_report_pdf_status(r["id"], "failed")
            _log(f"  {label} 跳过（无法解析文档编号）：{short_title}")
            continue

        update_rb_report_pdf_status(r["id"], "downloading")

        # 1. 先尝试 PDF
        text = ""
        try:
            pdf_url = PDF_URL_TPL.format(info_code=info_code)
            resp = _SESSION.get(pdf_url, timeout=30)
            if resp.content[:4] == b"%PDF":
                text = _extract_pdf_text(resp.content)
                if text.strip():
                    _log(f"  {label} PDF 已提取 {len(text)} 字：{short_title}")
        except Exception as e:
            logger.debug(f"[rb_fetcher] PDF 下载异常 {info_code}: {e}")

        # 2. PDF 失败则降级 HTML 摘要
        if not text.strip():
            time.sleep(_HTML_INTERVAL)
            text = _extract_html_abstract(info_code)
            if text.strip():
                _log(f"  {label} 摘要已提取 {len(text)} 字（PDF不可用，降级为网页摘要）：{short_title}")

        if text.strip():
            update_rb_report_text(r["id"], text, "done")
            done += 1
        else:
            update_rb_report_pdf_status(r["id"], "failed")
            _log(f"  {label} 未能获取内容：{short_title}")

        time.sleep(_PDF_INTERVAL)

    _log(f"全文下载完成，成功 {done}/{total_cap} 篇")
    return done


# ── 分批供 LLM 分析 ────────────────────────────────────────────────────────

def get_reports_text_batches(project_id: int, max_chars: int = MAX_CHARS_PER_BATCH) -> list[str]:
    """
    将所有研报分批，每批不超过 max_chars 字符。
    有摘要全文的优先，无摘要的用元数据拼接。
    """
    reports = get_rb_reports(project_id)
    full_reports = [r for r in reports if r.get("full_text") and r["full_text"].strip()]
    meta_reports = [r for r in reports if not (r.get("full_text") and r["full_text"].strip())]

    batches: list[str] = []
    current: list[str] = []
    current_len = 0

    def _flush():
        if current:
            batches.append("\n\n---\n\n".join(current))

    for r in full_reports:
        snippet = (
            f"【{r['title']}】\n"
            f"机构：{r['org_name']}  日期：{r['publish_date']}  "
            f"评级：{r['rating']}  目标价：{r['aim_price']}\n"
            f"{r['full_text'][:6000]}"
        )
        if current_len + len(snippet) > max_chars and current:
            _flush()
            current = []
            current_len = 0
        current.append(snippet)
        current_len += len(snippet)
    _flush()

    if meta_reports:
        chunk: list[str] = []
        chunk_len = 0
        for r in meta_reports:
            line = (
                f"【{r['title']}】"
                f"{r['org_name']} {r['publish_date']} "
                f"股票:{r['stock_name']}({r['stock_code']}) "
                f"评级:{r['rating']} 目标价:{r['aim_price']}"
            )
            if chunk_len + len(line) > max_chars and chunk:
                batches.append("\n".join(chunk))
                chunk = []
                chunk_len = 0
            chunk.append(line)
            chunk_len += len(line)
        if chunk:
            batches.append("\n".join(chunk))

    return batches
