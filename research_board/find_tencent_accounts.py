#!/usr/bin/env python3
"""
Find Tencent News account IDs for major brokerages.
Uses WebSearch to find articles, then extracts suid from article HTML.
"""

import json
import time
import re
import urllib.parse
from datetime import datetime

try:
    from curl_cffi import requests as cffi_requests
    USE_CURL_CFFI = True
    print("[INFO] Using curl_cffi")
except ImportError:
    import requests as cffi_requests
    USE_CURL_CFFI = False
    print("[INFO] Using requests")

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://new.qq.com/",
}

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
    "Referer": "https://new.qq.com/",
}

DELAY = 1.8  # seconds between requests


def safe_get(url, headers=None, timeout=15, params=None):
    """Make a GET request with error handling."""
    try:
        if USE_CURL_CFFI:
            resp = cffi_requests.get(url, headers=headers or HEADERS, timeout=timeout, params=params, impersonate="chrome120")
        else:
            resp = requests.get(url, headers=headers or HEADERS, timeout=timeout, params=params)
        return resp
    except Exception as e:
        print(f"  [ERROR] GET {url}: {e}")
        return None


def get_user_homepage_by_chlid(chlid):
    """Get user homepage info by chlid."""
    url = f"https://i.news.qq.com/i/getUserHomepageInfo"
    resp = safe_get(url, headers=API_HEADERS, params={"chlid": chlid})
    if resp and resp.status_code == 200:
        try:
            return resp.json()
        except:
            return None
    return None


def get_user_homepage_by_suid(suid):
    """Get user homepage info by suid (already encoded)."""
    url = f"https://i.news.qq.com/i/getUserHomepageInfo"
    resp = safe_get(url, headers=API_HEADERS, params={"guestSuid": suid})
    if resp and resp.status_code == 200:
        try:
            return resp.json()
        except:
            return None
    return None


def get_article_list_by_suid(suid, limit=5):
    """Get article list for a user by suid."""
    url = "https://i.news.qq.com/getSubNewsMixedList"
    resp = safe_get(url, headers=API_HEADERS, params={"guestSuid": suid, "tabId": "om_index"})
    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            return data
        except:
            return None
    return None


def extract_suid_from_html(html):
    """Extract suid from article HTML - look for omn/author/{suid} links."""
    # Pattern: /omn/author/{suid} or similar
    patterns = [
        r'/omn/author/([A-Za-z0-9+/=%]+)',
        r'"authorSuid"\s*:\s*"([^"]+)"',
        r'"suid"\s*:\s*"([^"]+)"',
        r'guestSuid=([A-Za-z0-9+/=%]+)',
    ]
    for pat in patterns:
        matches = re.findall(pat, html)
        for m in matches:
            decoded = urllib.parse.unquote(m)
            # Filter out known noise
            if len(decoded) > 8 and decoded not in ['undefined', 'null']:
                return decoded
    return None


def get_article_page_suid(article_url):
    """Fetch an article page and extract the author's suid."""
    resp = safe_get(article_url, headers=HEADERS)
    if not resp or resp.status_code != 200:
        return None
    html = resp.text

    # Try to find author suid in page
    # Look for omn/author links but skip 人民视频 and other side panel entries
    # The first relevant author link in the article body

    # Try JSON embedded data first
    json_patterns = [
        r'"authorSuid"\s*:\s*"([^"]+)"',
        r'"mediaId"\s*:\s*"([^"]+)"',
        r'"uin"\s*:\s*"([^"]+)"',
    ]
    for pat in json_patterns:
        m = re.search(pat, html)
        if m:
            val = urllib.parse.unquote(m.group(1))
            if len(val) > 5:
                return val

    # Look for omn author links
    author_links = re.findall(r'href=["\']([^"\']*omn/author/[^"\']+)["\']', html)
    for link in author_links:
        suid_part = re.search(r'/omn/author/([^"\'?&]+)', link)
        if suid_part:
            decoded = urllib.parse.unquote(suid_part.group(1))
            if len(decoded) > 5:
                return decoded

    return None


def search_bing_for_qq_article(query):
    """Use Bing to search for a Tencent News article."""
    search_url = "https://www.bing.com/search"
    params = {"q": f"site:new.qq.com {query}"}
    resp = safe_get(search_url, params=params, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    if not resp or resp.status_code != 200:
        return []

    html = resp.text
    # Extract new.qq.com/rain/a/ links
    links = re.findall(r'https://new\.qq\.com/rain/a/[A-Z0-9]+', html)
    # Deduplicate
    seen = set()
    result = []
    for link in links:
        if link not in seen:
            seen.add(link)
            result.append(link)
    return result[:5]


def extract_suid_from_qq_search(query):
    """Search Tencent News internal search for an author."""
    # Try Tencent News search API
    url = "https://new.qq.com/rain/search"
    resp = safe_get(url, params={"query": query, "type": "author"}, headers=HEADERS)
    if resp and resp.status_code == 200:
        # Try to extract from response
        suids = re.findall(r'omn/author/([A-Za-z0-9%+/=]+)', resp.text)
        if suids:
            return urllib.parse.unquote(suids[0])
    return None


def parse_homepage_info(data, query_type, query_value):
    """Parse homepage API response into account info."""
    if not data:
        return None

    # Navigate into possible response structures
    info = data
    if isinstance(data, dict):
        if 'data' in data:
            info = data['data']
        elif 'idInfo' in data:
            info = data['idInfo']

    if not info:
        return None

    # Extract nick name
    nick = None
    for key in ['nick', 'nickName', 'name', 'title']:
        if key in info:
            nick = info[key]
            break

    # Extract article count
    article_count = 0
    for key in ['articleNum', 'article_count', 'postNum', 'count']:
        if key in info:
            try:
                article_count = int(info[key])
                break
            except:
                pass

    # Extract suid if available
    suid = None
    for key in ['suid', 'openId', 'mediaId']:
        if key in info:
            suid = info[key]
            break

    # Extract last article date
    last_date = None
    for key in ['lastArticleTime', 'lastPostTime', 'updateTime', 'lastTime']:
        if key in info:
            ts = info[key]
            try:
                if isinstance(ts, (int, float)) and ts > 1000000000:
                    last_date = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
                elif isinstance(ts, str):
                    last_date = ts[:10]
                break
            except:
                pass

    return {
        "nick": nick,
        "suid": suid,
        "article_count": article_count,
        "last_date": last_date,
        "raw": info,
    }


def verify_chlid_account(name, chlid, category):
    """Verify a chlid-based account."""
    print(f"\n[VERIFY chlid] {name} (chlid={chlid})")
    data = get_user_homepage_by_chlid(chlid)
    time.sleep(DELAY)

    if not data:
        print(f"  -> No response")
        return None

    print(f"  -> Response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")

    info = parse_homepage_info(data, "chlid", chlid)
    if not info:
        print(f"  -> Could not parse info")
        return None

    print(f"  -> nick={info['nick']}, articles={info['article_count']}, last={info['last_date']}")

    # Check if article_count > 0 and last date is 2025+
    valid = True
    if info['article_count'] == 0:
        # Try to get article list to check
        print(f"  -> article_count=0, may still be valid")

    # Check date
    if info['last_date']:
        year = info['last_date'][:4]
        if year < '2025':
            print(f"  -> SKIP: last article too old ({info['last_date']})")
            return None

    return {
        "name": name,
        "nick": info['nick'] or name,
        "query_type": "chlid",
        "query_value": str(chlid),
        "category": category,
        "verified": True,
        "last_article_date": info['last_date'],
        "article_count": info['article_count'],
    }


def verify_suid_account(name, suid, category):
    """Verify a suid-based account."""
    print(f"\n[VERIFY suid] {name} (suid={suid})")
    data = get_user_homepage_by_suid(suid)
    time.sleep(DELAY)

    if not data:
        print(f"  -> No response")
        return None

    print(f"  -> Response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")

    info = parse_homepage_info(data, "guestSuid", suid)
    if not info:
        print(f"  -> Could not parse info")
        return None

    print(f"  -> nick={info['nick']}, articles={info['article_count']}, last={info['last_date']}")

    # Check date
    if info['last_date']:
        year = info['last_date'][:4]
        if year < '2025':
            print(f"  -> SKIP: last article too old ({info['last_date']})")
            return None

    return {
        "name": name,
        "nick": info['nick'] or name,
        "query_type": "guestSuid",
        "query_value": suid,
        "category": category,
        "verified": True,
        "last_article_date": info['last_date'],
        "article_count": info['article_count'],
    }


def search_and_find_suid(broker_name, search_queries):
    """Search for broker on Tencent News and extract suid."""
    for query in search_queries:
        print(f"  [SEARCH] '{query}'")
        # Try Bing search
        article_urls = search_bing_for_qq_article(query)
        time.sleep(DELAY)

        if not article_urls:
            print(f"    -> No articles found via Bing")
            continue

        print(f"    -> Found {len(article_urls)} articles: {article_urls[:2]}")

        # Try each article
        for url in article_urls[:3]:
            print(f"    [FETCH] {url}")
            suid = get_article_page_suid(url)
            time.sleep(DELAY)

            if suid:
                print(f"    -> Extracted suid: {suid}")
                return suid
            else:
                print(f"    -> No suid found in article")

    return None


def main():
    results = []
    not_found = []

    # ===== Pre-confirmed accounts =====
    pre_confirmed = [
        {
            "name": "研报精选",
            "nick": "研报精选",
            "query_type": "chlid",
            "query_value": "18213759",
            "category": "finance_media",
            "verified": True,
            "last_article_date": None,
            "article_count": 0,
        },
        {
            "name": "晚点LatePost",
            "nick": "晚点LatePost",
            "query_type": "chlid",
            "query_value": "17234203",
            "category": "finance_media",
            "verified": True,
            "last_article_date": None,
            "article_count": 0,
        },
        {
            "name": "远川投资评论",
            "nick": "远川投资评论",
            "query_type": "chlid",
            "query_value": "17169014",
            "category": "finance_media",
            "verified": True,
            "last_article_date": None,
            "article_count": 0,
        },
        {
            "name": "36氪",
            "nick": "36氪",
            "query_type": "guestSuid",
            "query_value": "8QMf2Hdb64wYuTvR",
            "category": "finance_media",
            "verified": True,
            "last_article_date": None,
            "article_count": 0,
        },
    ]

    # Verify pre-confirmed accounts to get actual data
    print("=" * 60)
    print("Verifying pre-confirmed accounts...")
    print("=" * 60)

    for acc in pre_confirmed:
        if acc["query_type"] == "chlid":
            verified = verify_chlid_account(acc["name"], acc["query_value"], acc["category"])
        else:
            verified = verify_suid_account(acc["name"], acc["query_value"], acc["category"])

        if verified:
            results.append(verified)
        else:
            # Keep the pre-confirmed entry even if verification fails
            results.append(acc)

    # ===== Broker accounts to find =====
    brokers_to_find = [
        {
            "name": "中信证券研究",
            "category": "broker_research",
            "queries": ["中信证券研究 研报", "CITIC Securities Research", "中信证券 晨报"],
        },
        {
            "name": "招商证券研究",
            "category": "broker_research",
            "queries": ["招商证券研究 研报", "招商证券研究所", "招商证券 策略"],
        },
        {
            "name": "华泰证券研究",
            "category": "broker_research",
            "queries": ["华泰证券研究 研报", "华泰证券研究所", "华泰证券 策略"],
        },
        {
            "name": "广发证券研究",
            "category": "broker_research",
            "queries": ["广发证券研究 研报", "广发证券研究所", "广发证券 策略"],
        },
        {
            "name": "兴业证券研究",
            "category": "broker_research",
            "queries": ["兴业证券研究 研报", "兴业证券经济与金融研究院", "兴业证券 策略"],
        },
        {
            "name": "申万宏源研究",
            "category": "broker_research",
            "queries": ["申万宏源研究 研报", "申万宏源证券研究", "申万宏源 策略"],
        },
        {
            "name": "东吴证券研究",
            "category": "broker_research",
            "queries": ["东吴证券研究 研报", "东吴证券研究所", "东吴证券 策略"],
        },
        {
            "name": "中金公司研究部",
            "category": "broker_research",
            "queries": ["中金点睛 研报", "中金公司研究部", "CICC Research"],
        },
        {
            "name": "国泰海通证券研究",
            "category": "broker_research",
            "queries": ["国泰海通证券研究 研报", "国泰海通 策略", "海通证券研究所"],
        },
        {
            "name": "国信证券经济研究所",
            "category": "broker_research",
            "queries": ["国信证券经济研究所 研报", "国信证券研究 策略", "国信证券 晨报"],
        },
        {
            "name": "国投证券研究",
            "category": "broker_research",
            "queries": ["国投证券研究 研报", "国投证券研究所", "国投证券 策略"],
        },
        {
            "name": "医药魔方",
            "category": "industry_media",
            "queries": ["医药魔方 研究", "医药魔方 医药行业"],
        },
        {
            "name": "汇博资讯",
            "category": "industry_media",
            "queries": ["汇博资讯 研究", "汇博资讯 股市"],
        },
    ]

    print("\n" + "=" * 60)
    print("Searching for broker accounts...")
    print("=" * 60)

    for broker in brokers_to_find:
        print(f"\n{'=' * 40}")
        print(f"Looking for: {broker['name']}")

        suid = search_and_find_suid(broker['name'], broker['queries'])

        if suid:
            verified = verify_suid_account(broker['name'], suid, broker['category'])
            if verified:
                results.append(verified)
                print(f"  -> FOUND and VERIFIED: {broker['name']}")
            else:
                print(f"  -> Found suid but verification failed")
                not_found.append(broker['name'])
        else:
            print(f"  -> NOT FOUND")
            not_found.append(broker['name'])

        time.sleep(DELAY)

    # ===== Output =====
    output = {"accounts": results}

    output_path = "/mnt/ssd_1T/runist/code/Q/market-radar/research_board/tencent_accounts.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print(f"Done! Written to: {output_path}")
    print(f"Found {len(results)} valid accounts")
    print(f"Not found ({len(not_found)}): {not_found}")

    return results, not_found


if __name__ == "__main__":
    main()
