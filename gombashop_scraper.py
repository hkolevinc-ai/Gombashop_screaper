#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GombaShop lead scraper for Bulgarian websites.

How it works:
1) Finds candidate URLs via search-engine queries for GombaShop fingerprints.
2) Normalizes results to website roots/domains.
3) Downloads a small set of public pages per site.
4) Validates whether the site is really/probably on GombaShop.
5) Extracts URL, site name, company/legal entity, category and email(s).
6) Exports a clean Excel file.

Supported search backends:
- SerpAPI: env SERPAPI_KEY
- Bing Web Search API: env BING_SEARCH_API_KEY
- Google Custom Search JSON API: env GOOGLE_API_KEY + GOOGLE_CSE_ID
- DuckDuckGo HTML search: no key, less stable
- none: only uses manual_domains.txt / --seed-file
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36; GombaShopResearchBot/1.0"
)

SEARCH_QUERIES = [
    '"www.gombashop.com" site:.bg',
    '"gombashop.com" site:.bg -gombashop.bg',
    '"powered by GombaShop" site:.bg',
    '"Powered by GombaShop™" site:.bg',
    '"GombaShop™" site:.bg',
    '"meta name=\"Author\" content=\"www.gombashop.com\""',
    '"plugins/RssFeedPlugin/feed" site:.bg',
    '"cart-wishlistCount.html" site:.bg',
    '"cart-wishlistEditAx.html" site:.bg',
    '"/static/common/scripts/pub.product.js" site:.bg',
    '"gs-header" "gs-main-container" site:.bg',
    'inurl:"/mobile/" "powered by GombaShop" site:.bg',
    'inurl:"/content/details?Id=" "GombaShop" site:.bg',
    'inurl:"/catalog/details?Id=" "GombaShop" site:.bg',
    'inurl:"/product/details?Id=" "GombaShop" site:.bg',
]

GOMBASHOP_STRONG_PATTERNS = [
    "www.gombashop.com",
    "powered by gombashop",
    "powered by gombashop™",
    "gombashop™",
    "gombashop, sva prava",
]

GOMBASHOP_MEDIUM_PATTERNS = [
    "/plugins/RssFeedPlugin/feed",
    "/plugins/FbDynamicProducts/conversion",
    "/cart-wishlistCount.html",
    "/cart-wishlistEditAx.html",
    "/static/common/scripts/pub.product.js",
    "gs-header",
    "gs-main-container",
    "gs-cart",
    "gs-item-data",
    "gs-view-more",
    "gs-gomba-slider",
    "QuickView.showInfo",
    "data-quick-view-btn",
]

EMAIL_RE = re.compile(
    r"(?<![\w.-])([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})(?![\w.-])",
    flags=re.IGNORECASE,
)

LEGAL_RE = re.compile(
    r"(?:[„\"']?)([A-ZА-Я0-9][A-ZА-Яа-яa-z0-9\s&.,'\"“”\-]{2,80}?\s+(?:ЕООД|ООД|АД|ЕАД|ЕТ|СД|КД))(?:[“\"']?)",
    flags=re.IGNORECASE,
)

CONTACT_KEYWORDS = [
    "contact",
    "contacts",
    "kontakt",
    "kontakti",
    "контакт",
    "контакти",
    "за нас",
    "za-nas",
    "about",
    "details",
    "privacy",
    "поверителност",
    "usloviya",
    "условия",
]

EXCLUDED_EMAIL_DOMAINS = {
    "gombashop.com",
    "example.com",
    "example.bg",
    "domain.com",
    "yourdomain.com",
    "sentry.io",
    "schema.org",
}

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "Спорт / Аутдор": [
        "спорт", "outdoor", "аутдор", "туризъм", "къмпинг", "бягане", "fitness", "фитнес",
        "trekking", "ски", "колела", "велосипеди", "sup", "wakeboard", "риболов", "лов",
    ],
    "Мода / Обувки / Дрехи": [
        "дрех", "облек", "мода", "fashion", "обув", "shoes", "тениски", "рокли", "чанти",
        "бижута", "аксесоари", "дънки", "бельо",
    ],
    "Здраве / Добавки / Козметика": [
        "здрав", "добавк", "витамин", "минерал", "апте", "козмет", "beauty", "масла",
        "натурал", "пробиотик", "колаген", "храни", "wellness",
    ],
    "Електроника": [
        "електрон", "техника", "компют", "gsm", "телефон", "камер", "bluetooth", "колонки",
        "зарядни", "термовиз", "осветление", "led",
    ],
    "Авто / Мото": [
        "авто", "мото", "коли", "автомобил", "резервни части", "гуми", "масла", "акумулатор",
        "car", "motorcycle", "bmw", "audi", "mercedes",
    ],
    "Дом / Градина / Мебели": [
        "дом", "градина", "мебел", "кухня", "баня", "матрак", "осветление", "декорация",
        "home", "garden", "furniture",
    ],
    "Храни / Напитки": [
        "храни", "напитки", "кафе", "чай", "вино", "мед", "био", "зърно", "пшеница",
        "food", "drink",
    ],
    "Детски / Играчки / Книги": [
        "детски", "играч", "бебе", "книга", "книги", "ученически", "училище", "канцелар",
        "toy", "book",
    ],
    "Индустриално / B2B": [
        "индустри", "машин", "производ", "оборудване", "b2b", "server", "automation", "вентилация",
        "климатизация", "склад", "строителство",
    ],
    "Изкуство / Подаръци": [
        "изкуство", "подарък", "сувенир", "картини", "декор", "art", "gift", "ръчно изработени",
    ],
}


@dataclass
class Candidate:
    url: str
    source_query: str = "manual"
    source_backend: str = "manual"


@dataclass
class SiteResult:
    url: str
    matched_url: str
    site_name: str
    company_name: str
    category: str
    emails: str
    confidence: int
    signals: str
    status: str
    source_query: str
    source_backend: str
    title: str
    meta_description: str
    pages_checked: str
    error: str
    checked_at: str


def build_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=2,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=30, pool_maxsize=30)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "bg,en;q=0.8"})
    return session


def clean_url(raw_url: str) -> Optional[str]:
    if not raw_url:
        return None
    raw_url = html.unescape(raw_url.strip())
    if raw_url.startswith("//"):
        raw_url = "https:" + raw_url
    if not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url
    parsed = urlparse(raw_url)
    if not parsed.netloc or "." not in parsed.netloc:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    netloc = parsed.netloc.lower().split("@").pop()
    netloc = netloc.split(":")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    # Exclude obvious search/social/CDN URLs
    bad_hosts = [
        "google.", "bing.", "duckduckgo.", "facebook.", "instagram.", "linkedin.",
        "youtube.", "pinterest.", "gombashop.bg", "gombashop.com",
    ]
    if any(b in netloc for b in bad_hosts):
        return None
    return urlunparse(("https", netloc, "/", "", "", ""))


def same_host_url(base_root: str, candidate_href: str) -> Optional[str]:
    absolute = urljoin(base_root, candidate_href)
    parsed_base = urlparse(base_root)
    parsed = urlparse(absolute)
    if parsed.netloc.lower().lstrip("www.") != parsed_base.netloc.lower().lstrip("www."):
        return None
    clean_path = parsed.path or "/"
    return urlunparse((parsed.scheme, parsed.netloc, clean_path, "", parsed.query, ""))


def fetch(session: requests.Session, url: str, timeout: int = 15) -> Tuple[str, str, Optional[str]]:
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        ctype = resp.headers.get("content-type", "").lower()
        if resp.status_code >= 400:
            return url, "", f"HTTP {resp.status_code}"
        if "text/html" not in ctype and "application/xhtml" not in ctype and not resp.text.lstrip().startswith("<"):
            return resp.url, "", f"non-html: {ctype}"
        resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"
        return resp.url, resp.text[:2_000_000], None
    except Exception as exc:
        return url, "", str(exc)


def gombashop_score(html_text: str) -> Tuple[int, List[str]]:
    lower = html_text.lower()
    signals: List[str] = []
    score = 0

    soup = BeautifulSoup(html_text[:200_000], "html.parser")
    author = soup.find("meta", attrs={"name": re.compile("^author$", re.I)})
    if author and "gombashop" in (author.get("content") or "").lower():
        signals.append("meta author=gombashop")
        score += 6

    for pattern in GOMBASHOP_STRONG_PATTERNS:
        if pattern in lower:
            signals.append(pattern)
            score += 4

    for pattern in GOMBASHOP_MEDIUM_PATTERNS:
        if pattern.lower() in lower:
            signals.append(pattern)
            score += 1

    # Cap repeated noisy signals but keep strong sites high enough
    return min(score, 20), signals


def is_verified_gombashop(score: int, signals: Sequence[str]) -> str:
    joined = " | ".join(signals).lower()
    if score >= 4 and ("gombashop" in joined or "meta author" in joined):
        return "verified"
    if score >= 5:
        return "probable"
    return "not_gombashop"


def extract_meta(soup: BeautifulSoup) -> Tuple[str, str, str]:
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    desc = ""
    site_name = ""
    meta_desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if meta_desc:
        desc = meta_desc.get("content", "").strip()
    og_site = soup.find("meta", property="og:site_name")
    if og_site:
        site_name = og_site.get("content", "").strip()
    og_title = soup.find("meta", property="og:title")
    if not site_name and og_title:
        site_name = og_title.get("content", "").strip()
    if not site_name:
        site_name = title
    site_name = simplify_title(site_name)
    return title, desc, site_name


def simplify_title(value: str) -> str:
    value = html.unescape(value or "").strip()
    value = re.sub(r"\s+", " ", value)
    if not value:
        return ""
    # Split common SEO separators, keep first meaningful part
    for sep in [" | ", " - ", " – ", " — ", " ▷ ", " :: "]:
        if sep in value:
            parts = [p.strip() for p in value.split(sep) if p.strip()]
            if parts:
                return parts[0][:120]
    return value[:120]


def extract_emails(text: str) -> List[str]:
    # Decode simple obfuscations
    normalized = html.unescape(text)
    normalized = re.sub(r"\s*\[at\]\s*|\s*\(at\)\s*|\s+at\s+", "@", normalized, flags=re.I)
    normalized = re.sub(r"\s*\[dot\]\s*|\s*\(dot\)\s*|\s+dot\s+", ".", normalized, flags=re.I)

    emails: Set[str] = set()
    for match in EMAIL_RE.findall(normalized):
        email = match.strip(".,;:()[]{}<>\"'").lower()
        domain = email.split("@")[-1]
        if domain in EXCLUDED_EMAIL_DOMAINS:
            continue
        if any(email.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"]):
            continue
        if len(email) > 120:
            continue
        emails.add(email)
    return sorted(emails)


def extract_company(text: str, site_name: str = "") -> str:
    decoded = html.unescape(text)
    decoded = re.sub(r"\s+", " ", decoded)
    candidates: List[str] = []
    for match in LEGAL_RE.findall(decoded):
        c = match.strip(" .,:;–—-|\"'„“”")
        c = re.sub(r"\s+", " ", c)
        # Avoid long policy sentences falsely ending with legal suffix
        if 4 <= len(c) <= 95 and not c.lower().startswith(("адрес", "имейл", "телефон")):
            candidates.append(c)
    if candidates:
        # Prefer quoted/name-looking shorter candidate
        candidates = sorted(set(candidates), key=lambda x: (len(x), x.lower()))
        return candidates[0]
    return simplify_title(site_name)


def collect_contact_urls(root_url: str, soup: BeautifulSoup, max_pages: int = 5) -> List[str]:
    urls: List[str] = []
    seen: Set[str] = set()

    # Common GombaShop/contact paths first
    common_paths = [
        "/kontakti.html",
        "/contact.html",
        "/contacts.html",
        "/za-nas.html",
        "/about.html",
        "/content/details?Id=1&langId=1",
        "/content/details?Id=2&langId=1",
        "/Mobile.html/content/details?Id=1&langId=1",
        "/Mobile.html/content/details?Id=2&langId=1",
        "/delivery.html",
        "/terms-and-conditions.html",
        "/privacy-policy.html",
    ]
    for p in common_paths:
        u = urljoin(root_url, p)
        if u not in seen:
            seen.add(u)
            urls.append(u)

    # Add discovered links that look like contact/about/policy pages
    for a in soup.find_all("a", href=True):
        label = (a.get_text(" ", strip=True) + " " + a.get("href", "")).lower()
        if any(k in label for k in CONTACT_KEYWORDS):
            u = same_host_url(root_url, a["href"])
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        if len(urls) >= max_pages + len(common_paths):
            break
    return urls[: max_pages + 3]


def infer_category(text: str) -> str:
    value = html.unescape(text or "").lower()
    value = re.sub(r"\s+", " ", value)
    scores: List[Tuple[int, str]] = []
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = sum(value.count(k.lower()) for k in keywords)
        if score:
            scores.append((score, cat))
    if not scores:
        return "Друго"
    scores.sort(reverse=True)
    return scores[0][1]


def analyze_site(candidate: Candidate, delay: float = 0.0) -> SiteResult:
    session = build_session()
    checked_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    root = clean_url(candidate.url) or candidate.url
    matched_url = ""
    pages_checked: List[str] = []
    error = ""

    # Try https root, then http root if needed
    urls_to_try = [root]
    parsed = urlparse(root)
    if parsed.scheme == "https":
        urls_to_try.append(urlunparse(("http", parsed.netloc, "/", "", "", "")))

    html_text = ""
    final_url = root
    for u in urls_to_try:
        final_url, html_text, err = fetch(session, u)
        pages_checked.append(final_url)
        if html_text:
            error = ""
            break
        error = err or "empty response"
        if delay:
            time.sleep(delay)

    if not html_text:
        return SiteResult(
            url=root,
            matched_url=matched_url,
            site_name="",
            company_name="",
            category="",
            emails="",
            confidence=0,
            signals="",
            status="fetch_failed",
            source_query=candidate.source_query,
            source_backend=candidate.source_backend,
            title="",
            meta_description="",
            pages_checked=" | ".join(pages_checked),
            error=error,
            checked_at=checked_at,
        )

    score, signals = gombashop_score(html_text)
    status = is_verified_gombashop(score, signals)

    soup = BeautifulSoup(html_text, "html.parser")
    title, meta_desc, site_name = extract_meta(soup)
    combined_text = "\n".join([title, meta_desc, soup.get_text(" ", strip=True)[:200_000]])
    all_texts = [combined_text, html_text[:300_000]]

    emails = set(extract_emails("\n".join(all_texts)))
    company_name = extract_company("\n".join(all_texts), site_name=site_name)

    # Pull a few likely contact/company pages for emails and legal name.
    contact_urls = collect_contact_urls(final_url, soup, max_pages=5)
    for cu in contact_urls:
        if cu in pages_checked:
            continue
        fetched_url, page_html, err = fetch(session, cu, timeout=12)
        pages_checked.append(fetched_url)
        if page_html:
            all_texts.append(page_html[:300_000])
            emails.update(extract_emails(page_html))
            if not company_name or company_name == site_name:
                company_name = extract_company(page_html, site_name=site_name)
        if delay:
            time.sleep(delay)

    category_source = "\n".join([title, meta_desc, BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)[:100_000]])
    category = infer_category(category_source)

    # If search result pointed to an internal GombaShop page, keep the actual resolved root host.
    root_final = clean_url(final_url) or root
    matched_url = final_url

    return SiteResult(
        url=root_final,
        matched_url=matched_url,
        site_name=site_name,
        company_name=company_name,
        category=category,
        emails=", ".join(sorted(emails)),
        confidence=score,
        signals=" | ".join(dict.fromkeys(signals)),
        status=status,
        source_query=candidate.source_query,
        source_backend=candidate.source_backend,
        title=title[:220],
        meta_description=meta_desc[:500],
        pages_checked=" | ".join(dict.fromkeys(pages_checked)),
        error=error,
        checked_at=checked_at,
    )


def search_serpapi(query: str, limit: int) -> List[str]:
    key = os.getenv("SERPAPI_KEY")
    if not key:
        return []
    urls: List[str] = []
    start = 0
    while len(urls) < limit:
        params = {
            "engine": "google",
            "q": query,
            "api_key": key,
            "google_domain": "google.bg",
            "gl": "bg",
            "hl": "bg",
            "num": min(100, limit - len(urls)),
            "start": start,
        }
        r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        organic = data.get("organic_results") or []
        if not organic:
            break
        urls.extend([o.get("link") for o in organic if o.get("link")])
        start += len(organic)
        if len(organic) < 10:
            break
        time.sleep(1.0)
    return urls[:limit]


def search_bing(query: str, limit: int) -> List[str]:
    key = os.getenv("BING_SEARCH_API_KEY")
    if not key:
        return []
    endpoint = os.getenv("BING_SEARCH_ENDPOINT", "https://api.bing.microsoft.com/v7.0/search")
    urls: List[str] = []
    offset = 0
    while len(urls) < limit:
        params = {"q": query, "count": min(50, limit - len(urls)), "offset": offset, "mkt": "bg-BG"}
        r = requests.get(endpoint, params=params, headers={"Ocp-Apim-Subscription-Key": key}, timeout=30)
        r.raise_for_status()
        values = (r.json().get("webPages") or {}).get("value") or []
        if not values:
            break
        urls.extend([v.get("url") for v in values if v.get("url")])
        offset += len(values)
        if len(values) < 10:
            break
        time.sleep(1.0)
    return urls[:limit]


def search_google_cse(query: str, limit: int) -> List[str]:
    key = os.getenv("GOOGLE_API_KEY")
    cx = os.getenv("GOOGLE_CSE_ID")
    if not key or not cx:
        return []
    urls: List[str] = []
    start = 1
    while len(urls) < limit and start <= 91:
        params = {
            "key": key,
            "cx": cx,
            "q": query,
            "num": min(10, limit - len(urls)),
            "start": start,
            "hl": "bg",
            "gl": "bg",
        }
        r = requests.get("https://www.googleapis.com/customsearch/v1", params=params, timeout=30)
        r.raise_for_status()
        items = r.json().get("items") or []
        if not items:
            break
        urls.extend([it.get("link") for it in items if it.get("link")])
        start += len(items)
        time.sleep(1.0)
    return urls[:limit]


def search_duckduckgo(query: str, limit: int) -> List[str]:
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except Exception:
        print("duckduckgo_search is not installed. Run: pip install duckduckgo_search", file=sys.stderr)
        return []
    urls: List[str] = []
    try:
        with DDGS() as ddgs:
            for row in ddgs.text(query, region="bg-bg", safesearch="off", max_results=limit):
                href = row.get("href") or row.get("url")
                if href:
                    urls.append(href)
    except Exception as exc:
        print(f"DuckDuckGo search failed for {query!r}: {exc}", file=sys.stderr)
    return urls[:limit]


def run_search_backend(backend: str, query: str, limit: int) -> List[str]:
    if backend == "serpapi":
        return search_serpapi(query, limit)
    if backend == "bing":
        return search_bing(query, limit)
    if backend == "google_cse":
        return search_google_cse(query, limit)
    if backend == "duckduckgo":
        return search_duckduckgo(query, limit)
    return []


def choose_auto_backend() -> str:
    if os.getenv("SERPAPI_KEY"):
        return "serpapi"
    if os.getenv("BING_SEARCH_API_KEY"):
        return "bing"
    if os.getenv("GOOGLE_API_KEY") and os.getenv("GOOGLE_CSE_ID"):
        return "google_cse"
    return "duckduckgo"


def load_seed_file(path: Optional[str]) -> List[str]:
    if not path or not os.path.exists(path):
        return []
    urls: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls


def discover_candidates(args: argparse.Namespace) -> List[Candidate]:
    candidates: Dict[str, Candidate] = {}

    # Manual seeds first
    for u in load_seed_file(args.seed_file):
        cu = clean_url(u)
        if cu:
            candidates[cu] = Candidate(url=cu, source_query="manual seed", source_backend="manual")

    backend = args.search_backend
    if backend == "auto":
        backend = choose_auto_backend()

    if backend == "none":
        return list(candidates.values())

    print(f"Search backend: {backend}")
    queries = SEARCH_QUERIES[: args.max_queries]
    for i, query in enumerate(queries, start=1):
        print(f"[{i}/{len(queries)}] Searching: {query}")
        try:
            found = run_search_backend(backend, query, args.search_limit_per_query)
        except Exception as exc:
            print(f"Search failed for {query!r}: {exc}", file=sys.stderr)
            found = []
        for raw_url in found:
            cu = clean_url(raw_url)
            if cu and cu not in candidates:
                candidates[cu] = Candidate(url=cu, source_query=query, source_backend=backend)
        if args.search_delay:
            time.sleep(args.search_delay)

    return list(candidates.values())


def export_excel(results: List[SiteResult], candidates: List[Candidate], out_path: str, include_rejected: bool) -> None:
    verified = [r for r in results if r.status in {"verified", "probable"}]
    rejected = [r for r in results if r.status not in {"verified", "probable"}]

    main_cols = [
        "url",
        "site_name",
        "company_name",
        "category",
        "emails",
        "confidence",
        "signals",
        "matched_url",
        "status",
        "source_query",
        "source_backend",
        "title",
        "meta_description",
        "pages_checked",
        "checked_at",
    ]
    candidates_df = pd.DataFrame([asdict(c) for c in candidates])
    verified_df = pd.DataFrame([asdict(r) for r in verified])
    rejected_df = pd.DataFrame([asdict(r) for r in rejected])

    if verified_df.empty:
        verified_df = pd.DataFrame(columns=main_cols)
    else:
        verified_df = verified_df[main_cols].sort_values(["confidence", "url"], ascending=[False, True])

    run_info = pd.DataFrame(
        [
            ["generated_at", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["verified_or_probable_sites", len(verified)],
            ["rejected_or_failed_sites", len(rejected)],
            ["candidate_domains", len(candidates)],
            ["note", "Use confidence/signals to manually review borderline probable rows."],
        ],
        columns=["metric", "value"],
    )

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        verified_df.to_excel(writer, index=False, sheet_name="GombaShop sites")
        candidates_df.to_excel(writer, index=False, sheet_name="Search candidates")
        run_info.to_excel(writer, index=False, sheet_name="Run info")
        if include_rejected:
            if rejected_df.empty:
                rejected_df = pd.DataFrame(columns=list(asdict(SiteResult('', '', '', '', '', '', 0, '', '', '', '', '', '', '', '', '')).keys()))
            rejected_df.to_excel(writer, index=False, sheet_name="Rejected")

        workbook = writer.book
        for ws in workbook.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = cell.font.copy(bold=True)
            for col in ws.columns:
                max_len = 0
                col_letter = col[0].column_letter
                for cell in col[:200]:
                    if cell.value is not None:
                        max_len = max(max_len, len(str(cell.value)))
                ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 55)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find Bulgarian websites using GombaShop and export them to Excel.")
    parser.add_argument("--out", default="gombashop_sites.xlsx", help="Output XLSX file path.")
    parser.add_argument("--seed-file", default="manual_domains.txt", help="Optional text file with one URL/domain per line.")
    parser.add_argument(
        "--search-backend",
        choices=["auto", "serpapi", "bing", "google_cse", "duckduckgo", "none"],
        default="auto",
        help="Search backend. Use 'none' to validate only --seed-file domains.",
    )
    parser.add_argument("--max-queries", type=int, default=len(SEARCH_QUERIES), help="How many predefined search queries to use.")
    parser.add_argument("--search-limit-per-query", type=int, default=80, help="Max search results per query.")
    parser.add_argument("--search-delay", type=float, default=1.0, help="Delay between search queries in seconds.")
    parser.add_argument("--request-delay", type=float, default=0.15, help="Delay between requests inside one site analysis.")
    parser.add_argument("--workers", type=int, default=8, help="Concurrent website checks.")
    parser.add_argument("--include-rejected", action="store_true", help="Include rejected/non-GombaShop candidates in Excel.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidates = discover_candidates(args)
    if not candidates:
        print("No candidate domains found. Add domains to manual_domains.txt or configure a search backend.", file=sys.stderr)
        return 2

    print(f"Candidate domains: {len(candidates)}")
    results: List[SiteResult] = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {executor.submit(analyze_site, c, args.request_delay): c for c in candidates}
        for idx, future in enumerate(cf.as_completed(future_map), start=1):
            c = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                result = SiteResult(
                    url=c.url,
                    matched_url="",
                    site_name="",
                    company_name="",
                    category="",
                    emails="",
                    confidence=0,
                    signals="",
                    status="error",
                    source_query=c.source_query,
                    source_backend=c.source_backend,
                    title="",
                    meta_description="",
                    pages_checked="",
                    error=str(exc),
                    checked_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
            results.append(result)
            print(f"[{idx}/{len(candidates)}] {result.status:13s} {result.confidence:2d} {result.url}")

    # Deduplicate by final URL, keeping highest confidence / best status
    priority = {"verified": 3, "probable": 2, "not_gombashop": 1, "fetch_failed": 0, "error": 0}
    best: Dict[str, SiteResult] = {}
    for r in results:
        current = best.get(r.url)
        if not current or (priority.get(r.status, 0), r.confidence) > (priority.get(current.status, 0), current.confidence):
            best[r.url] = r

    final_results = list(best.values())
    export_excel(final_results, candidates, args.out, args.include_rejected)
    verified_count = sum(1 for r in final_results if r.status in {"verified", "probable"})
    print(f"Done. Exported {verified_count} verified/probable GombaShop sites to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
