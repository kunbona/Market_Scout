"""
research_board — 研报抓取 + PDF 文字提取
"""

import io
import logging
import os
import time
from datetime import datetime, timedelta

import pytz
import requests

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

BASE_URL = "https://reportapi.eastmoney.com/report/list"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/report/stock.jshtml",
}
PDF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/report/",
}

# 每批提交给 Kimi 的最大研报字符数（约 50 篇摘要或 5 篇全文）
MAX_CHARS_PER_BATCH = 60_000

# PDF 下载并发限速
_PDF_INTERVAL = 1.0  # 秒


def _today_beijing() -> str:
    return datetime.now(_TZ_BEIJING).strftime("%Y-%m-%d")


def _report_url(encode_url: str) -> str:
    if encode_url:
        return f"https://pdf.dfcfw.com/pdf/H3_{encode_url}_1.pdf"
    return ""


def _keyword_match(title: str, keywords: list[str]) -> bool:
    """标题是否包含任意关键词（大小写不敏感）。空关键词列表视为全匹配。"""
    if not keywords:
        return True
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


def fetch_reports_for_project(project_id: int, progress_cb=None) -> int:
    """
    按项目配置抓取研报元数据（不下载 PDF）。
    返回新增研报数量。
    progress_cb: callable(msg: str) 用于向调用方推送进度文字。
    """
    project = get_project(project_id)
    if not project:
        raise ValueError(f"project {project_id} not found")

    keywords: list[str] = project["keywords"]
    qtype_filter: list[int] = project["qtype_filter"]
    days_back: int = project["days_back"]

    end = _today_beijing()
    begin = (datetime.now(_TZ_BEIJING) - timedelta(days=days_back)).strftime("%Y-%m-%d")

    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    _log(f"[rb_fetcher] 项目 {project_id} 抓取研报 {begin} → {end}，关键词={keywords}")
    total_new = 0

    for qtype in qtype_filter:
        page = 1
        while True:
            try:
                params = {
                    "qType": qtype,
                    "pageSize": 100,
                    "pageNo": page,
                    "industryCode": "*",
                    "industry": "*",
                    "rating": "*",
                    "ratingChange": "*",
                    "beginTime": begin,
                    "endTime": end,
                    "fields": "",
                    "orgCode": "",
                    "code": "*",
                    "rcode": "",
                }
                resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=15)
                resp.raise_for_status()
                data = resp.json()

                raw = data.get("data", [])
                items = raw if isinstance(raw, list) else raw.get("list", [])

                count_this_page = 0
                for item in items:
                    title = str(item.get("title", "") or "")
                    if not title:
                        continue
                    if not _keyword_match(title, keywords):
                        continue

                    stock_code = str(item.get("stockCode", "") or "")
                    stock_name = str(item.get("stockName", "") or "")
                    org_name   = str(item.get("orgSName", "") or item.get("orgName", "") or "")
                    researcher = str(item.get("researcher", "") or item.get("author", "") or "")
                    pub_raw    = str(item.get("publishDate", "") or "")
                    pub_date   = pub_raw[:10] if pub_raw else ""
                    rating     = str(item.get("emRatingName", "") or "")
                    aim_price  = str(item.get("indvAimPriceT", "") or "")
                    encode_url = str(item.get("encodeUrl", "") or "")
                    report_url = _report_url(encode_url)

                    insert_rb_report(
                        project_id, title, stock_code, stock_name, org_name,
                        researcher, pub_date, rating, aim_price, report_url, qtype,
                    )
                    total_new += 1
                    count_this_page += 1

                total_pages = int(data.get("TotalPage", 1) or 1)
                _log(f"[rb_fetcher] qtype={qtype} page={page}/{total_pages} 命中={count_this_page}")

                if page >= total_pages:
                    break
                page += 1
                time.sleep(0.5)

            except Exception as e:
                logger.warning(f"[rb_fetcher] qtype={qtype} page={page} 失败: {e}")
                break

    _log(f"[rb_fetcher] 抓取完成，共新增 {total_new} 篇研报")
    return total_new


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """用 pdfplumber 从 PDF 字节流中提取纯文字。"""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
        return "\n".join(text_parts)
    except Exception as e:
        logger.warning(f"[rb_fetcher] pdfplumber 失败: {e}")
        return ""


def download_pdfs_for_project(project_id: int, max_count: int = 200,
                               progress_cb=None) -> int:
    """
    下载项目下所有 pending 状态研报的 PDF 并提取文字。
    max_count 限制本次最多下载数量（防止单次过多）。
    返回成功提取文字的数量。
    """
    def _log(msg: str):
        logger.info(msg)
        if progress_cb:
            progress_cb(msg)

    reports = get_rb_reports(project_id, pdf_status="pending")
    _log(f"[rb_fetcher] 待下载 PDF: {len(reports)} 篇，本次上限 {max_count}")

    done = 0
    for i, r in enumerate(reports[:max_count]):
        if not r["report_url"]:
            update_rb_report_pdf_status(r["id"], "failed")
            continue

        update_rb_report_pdf_status(r["id"], "downloading")
        try:
            resp = requests.get(r["report_url"], headers=PDF_HEADERS, timeout=30)
            resp.raise_for_status()
            text = extract_pdf_text(resp.content)
            if text.strip():
                update_rb_report_text(r["id"], text, "done")
                done += 1
            else:
                update_rb_report_pdf_status(r["id"], "failed")
            _log(f"[rb_fetcher] [{i+1}/{min(len(reports), max_count)}] {r['title'][:30]} → {'ok' if text.strip() else 'empty'}")
        except Exception as e:
            logger.warning(f"[rb_fetcher] 下载失败 {r['title'][:30]}: {e}")
            update_rb_report_pdf_status(r["id"], "failed")

        time.sleep(_PDF_INTERVAL)

    _log(f"[rb_fetcher] PDF 提取完成，成功 {done} 篇")
    return done


def get_reports_text_batches(project_id: int, max_chars: int = MAX_CHARS_PER_BATCH) -> list[str]:
    """
    将项目下所有有文字的研报分批，每批不超过 max_chars 字符。
    返回文字批次列表，每个元素是多篇研报拼接后的字符串。
    无 PDF 全文时退化为用标题+摘要信息拼接（摘要模式）。
    """
    reports = get_rb_reports(project_id)

    # 有全文的研报
    full_text_reports = [r for r in reports if r.get("full_text") and r["full_text"].strip()]
    # 无全文的研报（用元数据拼摘要）
    meta_only_reports = [r for r in reports if not (r.get("full_text") and r["full_text"].strip())]

    batches = []
    current_batch = []
    current_len = 0

    def _flush():
        if current_batch:
            batches.append("\n\n---\n\n".join(current_batch))

    # 优先全文
    for r in full_text_reports:
        snippet = f"【{r['title']}】（{r['org_name']} {r['publish_date']} 评级:{r['rating']}）\n{r['full_text'][:8000]}"
        if current_len + len(snippet) > max_chars and current_batch:
            _flush()
            current_batch = []
            current_len = 0
        current_batch.append(snippet)
        current_len += len(snippet)
    _flush()

    # 再追加元数据摘要批次
    if meta_only_reports:
        meta_texts = []
        for r in meta_only_reports:
            meta_texts.append(
                f"【{r['title']}】{r['org_name']} {r['publish_date']} 股票:{r['stock_name']}({r['stock_code']}) 评级:{r['rating']} 目标价:{r['aim_price']}"
            )
        # 元数据简短，全部放一批
        chunk = []
        chunk_len = 0
        for m in meta_texts:
            if chunk_len + len(m) > max_chars and chunk:
                batches.append("\n".join(chunk))
                chunk = []
                chunk_len = 0
            chunk.append(m)
            chunk_len += len(m)
        if chunk:
            batches.append("\n".join(chunk))

    return batches
