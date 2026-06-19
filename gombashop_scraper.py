#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GombaShop lead scraper for Bulgarian/e-commerce websites.

Goal:
  Find websites that use GombaShop, validate them by HTML fingerprints, extract:
  URL, site name, legal company name, category, email(s), confidence/signals.

Why v2:
  A single search query/backend will usually return too few sites. This version uses:
  - many query variants/fingerprints
  - optional SerpAPI / Bing / Google CSE / DuckDuckGo discovery
  - optional crt.sh discovery for *.gombashop.com subdomains
  - optional URLScan discovery for gombashop-related indexed pages
  - optional manual seed/domain file validation

For 1000+ results, use a paid/search API backend such as SerpAPI or Bing Search API.
DuckDuckGo is free but unstable and commonly returns very few results.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import json
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse, urlunparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36; GombaShopResearchBot/2.0"
)

# These are intentionally broad. Search engines cap results per query, so many varied
# queries are needed to discover enough unique domains.
BASE_FINGERPRINT_QUERIES = [
    '"powered by GombaShop"',
    '"Powered by GombaShop™"',
    '"powered by GombaShop™"',
    '"Онлайн магазин създаден с GombaShop"',
    '"Онлайн магазин създаден с GombaShop™"',
    '"онлайн магазин създаден с GombaShop"',
    '"GombaShop Powered by GombaShop™"',
    '"All Rights Reserved powered by GombaShop"',
    '"Sva prava zadržana Powered by GombaShop"',
    '"Autorsko pravo" "GombaShop"',
    '"©2026 GombaShop"',
    '"©2025 GombaShop"',
    '"©2024 GombaShop"',
    '"©2023 GombaShop"',
    '"www.gombashop.com" "Author"',
    '"meta name=\"Author\" content=\"www.gombashop.com\""',
    '"/plugins/RssFeedPlugin/feed"',
    '"/plugins/FbDynamicProducts/conversion"',
    '"cart-wishlistCount.html" "gs-header"',
    '"cart-wishlistEditAx.html" "QuickView.showInfo"',
    '"data-quick-view-btn" "cart-wishlistEditAx"',
    '"gs-gomba-slider" "gs-main-container"',
    '"gs-preloader" "gs-header" "gs-cart"',
    '"static/32/styles/main" "gs-section-wrap"',
    '"/cart-wishlist.html" "gs-wishlist"',
    '"/search.php" "gs-search-form"',
]

QUERY_MODIFIERS = [
    '',
    ' site:.bg',
    ' -site:gombashop.bg -site:gombashop.com',
    ' България',
    ' контакти',
    ' "контакти"',
    ' "ЕООД"',
    ' "ООД"',
    ' "Общи условия"',
    ' "Политика за поверителност"',
]

INDUSTRY_TERMS = [
    'дрехи', 'обувки', 'козметика', 'бижута', 'спорт', 'туризъм', 'къмпинг', 'здраве',
    'хранителни добавки', 'електроника', 'авто', 'мото', 'дом', 'градина', 'мебели',
    'подаръци', 'детски', 'играчки', 'книги', 'канцеларски', 'храни', 'кафе', 'чай',
    'риболов', 'лов', 'инструменти', 'осветление', 'оборудване', 'галерия', 'цветя',
]

SUBDOMAIN_QUERIES = [
    'site:gombashop.com -site:www.gombashop.com -site:gombashop.bg "kontakti"',
    'site:gombashop.com -site:www.gombashop.com -site:gombashop.bg "contacts"',
    'site:gombashop.com -site:www.gombashop.com -site:gombashop.bg "Общи условия"',
    'site:gombashop.com -site:www.gombashop.com -site:gombashop.bg "ЕООД"',
    'site:gombashop.com -site:www.gombashop.com -site:gombashop.bg "ООД"',
    'site:gombashop.com -site:www.gombashop.com -site:gombashop.bg "Онлайн магазин"',
    'site:gombashop.com -site:www.gombashop.com -site:gombashop.bg "All Rights Reserved"',
]

GOMBASHOP_STRONG_PATTERNS = [
    'www.gombashop.com',
    'powered by gombashop',
    'powered by gombashop™',
    'gombashop powered by gombashop',
    'онлайн магазин създаден с gombashop',
    'gombashop™',
    'gombashop, sva prava',
]

GOMBASHOP_MEDIUM_PATTERNS = [
    '/plugins/RssFeedPlugin/feed',
    '/plugins/FbDynamicProducts/conversion',
    '/cart-wishlistCount.html',
    '/cart-wishlistEditAx.html',
    '/cart-wishlist.html',
    '/static/common/scripts/pub.product.js',
    '/search.php',
    'gs-header',
    'gs-main-container',
    'gs-cart',
    'gs-section-wrap',
    'gs-item-data',
    'gs-view-more',
    'gs-gomba-slider',
    'QuickView.showInfo',
    'data-quick-view-btn',
    'data-wishlist',
]

EMAIL_RE = re.compile(
    r"(?<![\w.-])([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})(?![\w.-])",
    flags=re.IGNORECASE,
)

EXCEL_ILLEGAL_RE = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')

LEGAL_RE = re.compile(
    r"(?:[„\"']?)([A-ZА-Я0-9][A-ZА-Яа-яa-z0-9\s&.,'\"“”\-]{2,90}?\s+(?:ЕООД|ООД|АД|ЕАД|ЕТ|СД|КД))(?:[“\"']?)",
    flags=re.IGNORECASE,
)

CONTACT_KEYWORDS = [
    'contact', 'contacts', 'kontakt', 'kontakti', 'контакт', 'контакти', 'за нас',
    'za-nas', 'about', 'details', 'privacy', 'поверителност', 'usloviya', 'условия',
    'gdpr', 'лични данни', 'delivery', 'доставка', 'terms', 'obshti', 'общи',
]

EXCLUDED_EMAIL_DOMAINS = {
    'gombashop.com', 'example.com', 'example.bg', 'domain.com', 'yourdomain.com',
    'sentry.io', 'schema.org', 'w3.org', 'facebook.com', 'google.com', 'google.bg',
}

EXCLUDED_HOST_CONTAINS = [
    'google.', 'bing.', 'duckduckgo.', 'facebook.', 'instagram.', 'linkedin.', 'youtube.',
    'pinterest.', 'twitter.', 'x.com', 'tiktok.', 'gombashop.bg', 'www.gombashop.com',
    'support.gombashop.com', 'blog.gombashop.com', 'black-and-white-theme.gombashop.com',
]

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    'Спорт / Аутдор': [
        'спорт', 'outdoor', 'аутдор', 'туризъм', 'къмпинг', 'бягане', 'fitness', 'фитнес',
        'trekking', 'ски', 'колела', 'велосипеди', 'sup', 'wakeboard', 'риболов', 'лов',
        'въдици', 'палатки', 'обувки за планина',
    ],
    'Мода / Обувки / Дрехи': [
        'дрех', 'облек', 'мода', 'fashion', 'обув', 'shoes', 'тениски', 'рокли', 'чанти',
        'бижута', 'аксесоари', 'дънки', 'бельо', 'шалове', 'очила',
    ],
    'Здраве / Добавки / Козметика': [
        'здрав', 'добавк', 'витамин', 'минерал', 'апте', 'козмет', 'beauty', 'масла',
        'натурал', 'пробиотик', 'колаген', 'храни', 'wellness', 'парфюм', 'аромати',
    ],
    'Електроника': [
        'електрон', 'техника', 'компют', 'gsm', 'телефон', 'камер', 'bluetooth', 'колонки',
        'зарядни', 'термовиз', 'осветление', 'led', 'батерии', 'кабели',
    ],
    'Авто / Мото': [
        'авто', 'мото', 'коли', 'автомобил', 'резервни части', 'гуми', 'масла', 'акумулатор',
        'car', 'motorcycle', 'bmw', 'audi', 'mercedes', 'сервиз',
    ],
    'Дом / Градина / Мебели': [
        'дом', 'градина', 'мебел', 'кухня', 'баня', 'матрак', 'осветление', 'декорация',
        'home', 'garden', 'furniture', 'текстил', 'пердета', 'цветя',
    ],
    'Храни / Напитки': [
        'храни', 'напитки', 'кафе', 'чай', 'вино', 'мед', 'био', 'зърно', 'пшеница',
        'food', 'drink', 'италиански продукти', 'сладки', 'подправки',
    ],
    'Детски / Играчки / Книги': [
        'детски', 'играч', 'бебе', 'книга', 'книги', 'ученически', 'училище', 'канцелар',
        'toy', 'book', 'дете', 'бебешки',
    ],
    'Индустриално / B2B': [
        'индустри', 'машин', 'производ', 'оборудване', 'b2b', 'server', 'automation',
        'вентилация', 'климатизация', 'склад', 'строителство', 'инструменти',
    ],
    'Изкуство / Подаръци': [
        'изкуство', 'подарък', 'сувенир', 'картини', 'декор', 'art', 'gift', 'ръчно изработени',
        'галерия', 'татуировки',
    ],
}


@dataclass
class Candidate:
    url: str
    source_query: str = 'manual'
    source_backend: str = 'manual'


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
        backoff_factor=0.7,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=['HEAD', 'GET'],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=80, pool_maxsize=80)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({
        'User-Agent': USER_AGENT,
        'Accept-Language': 'bg,en;q=0.8,sr;q=0.5',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    })
    return session


def decode_search_redirect(raw_url: str) -> str:
    """Extract real URL from Google/Bing redirect URLs where possible."""
    if not raw_url:
        return raw_url
    try:
        parsed = urlparse(raw_url)
        qs = parse_qs(parsed.query)
        for key in ('url', 'u', 'q'):
            if key in qs and qs[key]:
                maybe = unquote(qs[key][0])
                if maybe.startswith(('http://', 'https://')):
                    return maybe
    except Exception:
        pass
    return raw_url


def clean_url(raw_url: str, keep_path: bool = False) -> Optional[str]:
    if not raw_url:
        return None
    raw_url = decode_search_redirect(html.unescape(raw_url.strip()))
    raw_url = raw_url.strip(' \'\"<>),.;')
    if raw_url.startswith('//'):
        raw_url = 'https:' + raw_url
    if not raw_url.startswith(('http://', 'https://')):
        raw_url = 'https://' + raw_url
    parsed = urlparse(raw_url)
    if not parsed.netloc or '.' not in parsed.netloc:
        return None
    if parsed.scheme not in {'http', 'https'}:
        return None
    netloc = parsed.netloc.lower().split('@').pop().split(':')[0]
    # Normalize www, but keep gombashop subdomains.
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    if any(bad in netloc for bad in EXCLUDED_HOST_CONTAINS):
        return None
    path = parsed.path if keep_path else '/'
    query = parsed.query if keep_path else ''
    return urlunparse(('https', netloc, path or '/', '', query, ''))


def same_host_url(base_root: str, candidate_href: str) -> Optional[str]:
    absolute = urljoin(base_root, candidate_href)
    parsed_base = urlparse(base_root)
    parsed = urlparse(absolute)
    if parsed.netloc.lower().lstrip('www.') != parsed_base.netloc.lower().lstrip('www.'):
        return None
    clean_path = parsed.path or '/'
    return urlunparse((parsed.scheme, parsed.netloc, clean_path, '', parsed.query, ''))


def fetch(session: requests.Session, url: str, timeout: int = 16) -> Tuple[str, str, Optional[str]]:
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        ctype = resp.headers.get('content-type', '').lower()
        if resp.status_code >= 400:
            return resp.url or url, '', f'HTTP {resp.status_code}'
        if 'text/html' not in ctype and 'application/xhtml' not in ctype and not resp.text.lstrip().startswith('<'):
            return resp.url or url, '', f'non-html: {ctype}'
        resp.encoding = resp.apparent_encoding or resp.encoding or 'utf-8'
        return resp.url or url, resp.text[:2_500_000], None
    except Exception as exc:
        return url, '', str(exc)


def gombashop_score(html_text: str) -> Tuple[int, List[str]]:
    lower = html_text.lower()
    signals: List[str] = []
    score = 0

    soup = BeautifulSoup(html_text[:250_000], 'html.parser')
    author = soup.find('meta', attrs={'name': re.compile('^author$', re.I)})
    if author and 'gombashop' in (author.get('content') or '').lower():
        signals.append('meta author=gombashop')
        score += 8

    generator = soup.find('meta', attrs={'name': re.compile('^generator$', re.I)})
    if generator and 'gombashop' in (generator.get('content') or '').lower():
        signals.append('meta generator=gombashop')
        score += 8

    for pattern in GOMBASHOP_STRONG_PATTERNS:
        if pattern in lower:
            signals.append(pattern)
            score += 5

    medium_hits = 0
    for pattern in GOMBASHOP_MEDIUM_PATTERNS:
        if pattern.lower() in lower:
            signals.append(pattern)
            medium_hits += 1
    score += min(medium_hits, 8)

    # A GombaShop page often contains multiple gs-* classes plus cart-wishlist endpoints.
    if medium_hits >= 4:
        signals.append(f'{medium_hits} medium GombaShop UI signals')
        score += 2

    return min(score, 30), list(dict.fromkeys(signals))


def is_verified_gombashop(score: int, signals: Sequence[str]) -> str:
    joined = ' | '.join(signals).lower()
    if score >= 5 and ('gombashop' in joined or 'meta author' in joined or 'meta generator' in joined):
        return 'verified'
    if score >= 7:
        return 'probable'
    return 'not_gombashop'


def simplify_title(value: str) -> str:
    value = html.unescape(value or '').strip()
    value = re.sub(r'\s+', ' ', value)
    if not value:
        return ''
    for sep in [' | ', ' - ', ' – ', ' — ', ' ▷ ', ' :: ', ' • ']:
        if sep in value:
            parts = [p.strip() for p in value.split(sep) if p.strip()]
            if parts:
                return parts[0][:140]
    return value[:140]


def extract_meta(soup: BeautifulSoup) -> Tuple[str, str, str]:
    title = soup.title.get_text(' ', strip=True) if soup.title else ''
    desc = ''
    site_name = ''
    meta_desc = soup.find('meta', attrs={'name': re.compile('^description$', re.I)})
    if meta_desc:
        desc = meta_desc.get('content', '').strip()
    og_site = soup.find('meta', property='og:site_name')
    if og_site:
        site_name = og_site.get('content', '').strip()
    og_title = soup.find('meta', property='og:title')
    if not site_name and og_title:
        site_name = og_title.get('content', '').strip()
    if not site_name:
        site_name = title
    return title, desc, simplify_title(site_name)


def extract_emails(text: str) -> List[str]:
    normalized = html.unescape(text or '')
    normalized = re.sub(r'\s*\[at\]\s*|\s*\(at\)\s*|\s+at\s+', '@', normalized, flags=re.I)
    normalized = re.sub(r'\s*\[dot\]\s*|\s*\(dot\)\s*|\s+dot\s+', '.', normalized, flags=re.I)
    normalized = normalized.replace('mailto:', ' ')

    emails: Set[str] = set()
    for match in EMAIL_RE.findall(normalized):
        email = match.strip('.,;:()[]{}<>"\'').lower()
        domain = email.split('@')[-1]
        if domain in EXCLUDED_EMAIL_DOMAINS:
            continue
        if any(email.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.css', '.js']):
            continue
        if len(email) > 120 or '..' in email:
            continue
        emails.add(email)
    return sorted(emails)


def extract_company(text: str, site_name: str = '') -> str:
    decoded = html.unescape(text or '')
    decoded = re.sub(r'\s+', ' ', decoded)
    candidates: List[str] = []
    for match in LEGAL_RE.findall(decoded):
        c = match.strip(' .,:;–—-|"\'„“”')
        c = re.sub(r'\s+', ' ', c)
        bad_prefixes = ('адрес', 'имейл', 'email', 'телефон', 'phone', 'наименование', 'фирма е', 'copyright')
        if 4 <= len(c) <= 100 and not c.lower().startswith(bad_prefixes):
            candidates.append(c)
    if candidates:
        # Prefer quoted/name-looking shorter candidates.
        return sorted(set(candidates), key=lambda x: (len(x), x.lower()))[0]
    return simplify_title(site_name)


def collect_contact_urls(root_url: str, soup: BeautifulSoup, max_pages: int = 9) -> List[str]:
    urls: List[str] = []
    seen: Set[str] = set()

    common_paths = [
        '/kontakti.html', '/kontakt.html', '/contact.html', '/contacts.html', '/za-nas.html',
        '/about.html', '/about-us.html', '/content-details-1.html', '/content-details-2.html',
        '/content/details?Id=1&langId=1', '/content/details?Id=2&langId=1',
        '/Mobile.html/content/details?Id=1&langId=1', '/Mobile.html/content/details?Id=2&langId=1',
        '/delivery.html', '/dostavka.html', '/terms-and-conditions.html', '/obshti-usloviia.html',
        '/obshti-usloviia-za-polzvane.html', '/privacy-policy.html', '/politika-za-poveritelnost.html',
    ]
    for p in common_paths:
        u = urljoin(root_url, p)
        if u not in seen:
            seen.add(u)
            urls.append(u)

    for a in soup.find_all('a', href=True):
        label = (a.get_text(' ', strip=True) + ' ' + a.get('href', '')).lower()
        if any(k in label for k in CONTACT_KEYWORDS):
            u = same_host_url(root_url, a['href'])
            if u and u not in seen:
                seen.add(u)
                urls.append(u)
        if len(urls) >= max_pages + len(common_paths):
            break
    return urls[: max_pages + 5]


def infer_category(text: str) -> str:
    value = html.unescape(text or '').lower()
    value = re.sub(r'\s+', ' ', value)
    scores: List[Tuple[int, str]] = []
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = sum(value.count(k.lower()) for k in keywords)
        if score:
            scores.append((score, cat))
    if not scores:
        return 'Друго'
    scores.sort(reverse=True)
    return scores[0][1]


def root_variants(root: str) -> List[str]:
    parsed = urlparse(root)
    host = parsed.netloc
    variants = [urlunparse(('https', host, '/', '', '', ''))]
    if not host.startswith('www.'):
        variants.append(urlunparse(('https', 'www.' + host, '/', '', '', '')))
    variants.append(urlunparse(('http', host, '/', '', '', '')))
    if not host.startswith('www.'):
        variants.append(urlunparse(('http', 'www.' + host, '/', '', '', '')))
    return list(dict.fromkeys(variants))


def analyze_site(candidate: Candidate, delay: float = 0.0, max_contact_pages: int = 9) -> SiteResult:
    session = build_session()
    checked_at = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    root = clean_url(candidate.url) or candidate.url
    pages_checked: List[str] = []
    error = ''

    html_text = ''
    final_url = root
    for u in root_variants(root):
        final_url, html_text, err = fetch(session, u)
        pages_checked.append(final_url)
        if html_text:
            error = ''
            break
        error = err or 'empty response'
        if delay:
            time.sleep(delay)

    if not html_text:
        return SiteResult(
            url=root, matched_url='', site_name='', company_name='', category='', emails='',
            confidence=0, signals='', status='fetch_failed', source_query=candidate.source_query,
            source_backend=candidate.source_backend, title='', meta_description='',
            pages_checked=' | '.join(pages_checked), error=error, checked_at=checked_at,
        )

    score, signals = gombashop_score(html_text)
    status = is_verified_gombashop(score, signals)

    soup = BeautifulSoup(html_text, 'html.parser')
    title, meta_desc, site_name = extract_meta(soup)
    body_text = soup.get_text(' ', strip=True)[:250_000]
    all_texts = [title, meta_desc, body_text, html_text[:350_000]]

    emails = set(extract_emails('\n'.join(all_texts)))
    company_name = extract_company('\n'.join(all_texts), site_name=site_name)

    # Fetch contact/about/legal pages; also update Gomba score if footer is only present there.
    contact_urls = collect_contact_urls(final_url, soup, max_pages=max_contact_pages)
    for cu in contact_urls:
        if cu in pages_checked:
            continue
        fetched_url, page_html, err = fetch(session, cu, timeout=12)
        pages_checked.append(fetched_url)
        if page_html:
            all_texts.append(page_html[:350_000])
            emails.update(extract_emails(page_html))
            if not company_name or company_name == site_name:
                company_name = extract_company(page_html, site_name=site_name)
            page_score, page_signals = gombashop_score(page_html)
            if page_score > 0:
                score = min(30, score + min(page_score, 6))
                signals.extend(page_signals)
                signals = list(dict.fromkeys(signals))
                status = is_verified_gombashop(score, signals)
        if delay:
            time.sleep(delay)

    category_source = '\n'.join([title, meta_desc, body_text])
    category = infer_category(category_source)
    root_final = clean_url(final_url) or root

    return SiteResult(
        url=root_final,
        matched_url=final_url,
        site_name=site_name,
        company_name=company_name,
        category=category,
        emails=', '.join(sorted(emails)),
        confidence=score,
        signals=' | '.join(dict.fromkeys(signals)),
        status=status,
        source_query=candidate.source_query,
        source_backend=candidate.source_backend,
        title=title[:240],
        meta_description=meta_desc[:700],
        pages_checked=' | '.join(dict.fromkeys(pages_checked)),
        error=error,
        checked_at=checked_at,
    )


def generate_search_queries(max_queries: int, aggressive: bool = True) -> List[str]:
    queries: List[str] = []

    # Base fingerprints with modifiers.
    for base in BASE_FINGERPRINT_QUERIES:
        for mod in QUERY_MODIFIERS:
            q = (base + mod).strip()
            if q not in queries:
                queries.append(q)

    # Industry split. This helps bypass search-result caps and produces more unique domains.
    if aggressive:
        for term in INDUSTRY_TERMS:
            for base in ['"powered by GombaShop"', '"GombaShop™"', '"cart-wishlistEditAx.html"', '"gs-main-container"']:
                queries.append(f'{base} "{term}"')
                queries.append(f'{base} "{term}" България')

    queries.extend(SUBDOMAIN_QUERIES)

    # Remove duplicates while preserving order.
    unique = list(dict.fromkeys(queries))
    return unique[:max_queries]


def search_serpapi(query: str, limit: int, pages: int = 10) -> List[str]:
    key = os.getenv('SERPAPI_KEY')
    if not key:
        return []
    urls: List[str] = []
    # Google usually caps deep pagination, but varying queries makes this useful.
    for page in range(pages):
        if len(urls) >= limit:
            break
        start = page * 100
        params = {
            'engine': 'google',
            'q': query,
            'api_key': key,
            'google_domain': 'google.bg',
            'gl': 'bg',
            'hl': 'bg',
            'num': min(100, limit - len(urls)),
            'start': start,
        }
        r = requests.get('https://serpapi.com/search.json', params=params, timeout=35)
        r.raise_for_status()
        data = r.json()
        organic = data.get('organic_results') or []
        if not organic:
            break
        for o in organic:
            if o.get('link'):
                urls.append(o['link'])
        if len(organic) < 10:
            break
        time.sleep(0.8)
    return urls[:limit]


def search_bing(query: str, limit: int) -> List[str]:
    key = os.getenv('BING_SEARCH_API_KEY')
    if not key:
        return []
    endpoint = os.getenv('BING_SEARCH_ENDPOINT', 'https://api.bing.microsoft.com/v7.0/search')
    urls: List[str] = []
    offset = 0
    while len(urls) < limit and offset < 1000:
        params = {'q': query, 'count': min(50, limit - len(urls)), 'offset': offset, 'mkt': 'bg-BG'}
        r = requests.get(endpoint, params=params, headers={'Ocp-Apim-Subscription-Key': key}, timeout=35)
        r.raise_for_status()
        values = (r.json().get('webPages') or {}).get('value') or []
        if not values:
            break
        urls.extend([v.get('url') for v in values if v.get('url')])
        offset += len(values)
        if len(values) < 10:
            break
        time.sleep(0.8)
    return urls[:limit]


def search_google_cse(query: str, limit: int) -> List[str]:
    key = os.getenv('GOOGLE_API_KEY')
    cx = os.getenv('GOOGLE_CSE_ID')
    if not key or not cx:
        return []
    urls: List[str] = []
    start = 1
    while len(urls) < limit and start <= 91:
        params = {
            'key': key,
            'cx': cx,
            'q': query,
            'num': min(10, limit - len(urls)),
            'start': start,
            'hl': 'bg',
            'gl': 'bg',
        }
        r = requests.get('https://www.googleapis.com/customsearch/v1', params=params, timeout=35)
        r.raise_for_status()
        items = r.json().get('items') or []
        if not items:
            break
        urls.extend([it.get('link') for it in items if it.get('link')])
        start += len(items)
        time.sleep(0.8)
    return urls[:limit]


def search_duckduckgo(query: str, limit: int) -> List[str]:
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except Exception:
        print('duckduckgo_search is not installed. Run: pip install duckduckgo_search', file=sys.stderr)
        return []
    urls: List[str] = []
    try:
        with DDGS() as ddgs:
            # DDG can silently cap or throttle. This is not suitable for guaranteed 1000+ discovery.
            for row in ddgs.text(query, region='bg-bg', safesearch='off', max_results=limit):
                href = row.get('href') or row.get('url')
                if href:
                    urls.append(href)
    except Exception as exc:
        print(f'DuckDuckGo search failed for {query!r}: {exc}', file=sys.stderr)
    return urls[:limit]


def run_search_backend(backend: str, query: str, limit: int) -> List[str]:
    if backend == 'serpapi':
        return search_serpapi(query, limit)
    if backend == 'bing':
        return search_bing(query, limit)
    if backend == 'google_cse':
        return search_google_cse(query, limit)
    if backend == 'duckduckgo':
        return search_duckduckgo(query, limit)
    return []


def choose_auto_backend() -> str:
    if os.getenv('SERPAPI_KEY'):
        return 'serpapi'
    if os.getenv('BING_SEARCH_API_KEY'):
        return 'bing'
    if os.getenv('GOOGLE_API_KEY') and os.getenv('GOOGLE_CSE_ID'):
        return 'google_cse'
    return 'duckduckgo'


def load_seed_file(path: Optional[str]) -> List[str]:
    if not path or not os.path.exists(path):
        return []
    urls: List[str] = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            urls.append(line)
    return urls


def discover_crtsh(limit: int = 5000) -> List[str]:
    """Discover *.gombashop.com subdomains from Certificate Transparency.

    This does not find custom .bg domains, but it is a useful candidate source for
    stores that still use a gombashop.com subdomain or redirect from it.
    """
    url = 'https://crt.sh/?q=%25.gombashop.com&output=json'
    try:
        r = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f'crt.sh discovery failed: {exc}', file=sys.stderr)
        return []

    hosts: Set[str] = set()
    for row in data:
        name_value = row.get('name_value') or ''
        for host in str(name_value).split('\n'):
            host = host.strip().lower().lstrip('*.')
            if not host or host in {'gombashop.com', 'www.gombashop.com'}:
                continue
            if host.endswith('.gombashop.com') and not any(bad in host for bad in EXCLUDED_HOST_CONTAINS):
                hosts.add('https://' + host + '/')
            if len(hosts) >= limit:
                break
        if len(hosts) >= limit:
            break
    return sorted(hosts)[:limit]


def discover_urlscan(limit: int = 3000) -> List[str]:
    """Discover pages indexed by urlscan.io. No API key required for small usage."""
    queries = [
        'page.domain:gombashop.com',
        'page.url:"powered%20by%20GombaShop"',
        'page.url:"cart-wishlistEditAx"',
    ]
    urls: List[str] = []
    for q in queries:
        if len(urls) >= limit:
            break
        api = 'https://urlscan.io/api/v1/search/?q=' + quote_plus(q) + '&size=1000'
        try:
            r = requests.get(api, headers={'User-Agent': USER_AGENT}, timeout=45)
            if r.status_code == 429:
                print('urlscan rate-limited discovery.', file=sys.stderr)
                break
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            print(f'urlscan discovery failed for {q}: {exc}', file=sys.stderr)
            continue
        for row in data.get('results') or []:
            page = row.get('page') or {}
            u = page.get('url') or page.get('domain')
            if u:
                urls.append(u)
        time.sleep(1.0)
    return urls[:limit]


def add_candidate(candidates: Dict[str, Candidate], raw_url: str, source_query: str, source_backend: str) -> None:
    cu = clean_url(raw_url)
    if cu and cu not in candidates:
        candidates[cu] = Candidate(url=cu, source_query=source_query, source_backend=source_backend)


def discover_candidates(args: argparse.Namespace) -> List[Candidate]:
    candidates: Dict[str, Candidate] = {}

    for u in load_seed_file(args.seed_file):
        add_candidate(candidates, u, 'manual seed', 'manual')

    if args.ct_gombashop:
        print('Discovering candidates from crt.sh (*.gombashop.com)...')
        for u in discover_crtsh(limit=args.ct_limit):
            add_candidate(candidates, u, 'crt.sh %.gombashop.com', 'crtsh')
        print(f'Candidates after crt.sh: {len(candidates)}')

    if args.urlscan_gombashop:
        print('Discovering candidates from urlscan.io...')
        for u in discover_urlscan(limit=args.urlscan_limit):
            add_candidate(candidates, u, 'urlscan gombashop', 'urlscan')
        print(f'Candidates after urlscan: {len(candidates)}')

    backend = args.search_backend
    if backend == 'auto':
        backend = choose_auto_backend()

    if backend != 'none':
        print(f'Search backend: {backend}')
        queries = generate_search_queries(args.max_queries, aggressive=not args.no_aggressive_queries)
        for i, query in enumerate(queries, start=1):
            if len(candidates) >= args.target_candidates:
                print(f'Target candidates reached: {len(candidates)}')
                break
            print(f'[{i}/{len(queries)}] Searching: {query}')
            try:
                found = run_search_backend(backend, query, args.search_limit_per_query)
            except Exception as exc:
                print(f'Search failed for {query!r}: {exc}', file=sys.stderr)
                found = []
            for raw_url in found:
                add_candidate(candidates, raw_url, query, backend)
            print(f'  total candidates: {len(candidates)}')
            if args.search_delay:
                time.sleep(args.search_delay)

    return list(candidates.values())



def clean_excel_value(value, max_len: int = 32000):
    """Make scraped values safe for openpyxl/Excel.

    Some websites contain control characters in title/description/body text.
    openpyxl raises IllegalCharacterError at the very end of the run if these
    characters are written to XLSX. This sanitizer prevents losing a long run.
    """
    if value is None:
        return ''
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    text = EXCEL_ILLEGAL_RE.sub(' ', text)
    # Excel has a hard 32,767 character cell limit. Keep a safety margin.
    if len(text) > max_len:
        text = text[:max_len] + '…'
    return text


def sanitize_record(record: dict) -> dict:
    return {key: clean_excel_value(value) for key, value in record.items()}


def export_progress_csv(results: List[SiteResult], out_path: str) -> None:
    if not results:
        return
    path = out_path or 'gombashop_progress.csv'
    df = pd.DataFrame([sanitize_record(asdict(r)) for r in results])
    df.to_csv(path, index=False, encoding='utf-8-sig')


def export_excel(results: List[SiteResult], candidates: List[Candidate], out_path: str, include_rejected: bool) -> None:
    verified = [r for r in results if r.status in {'verified', 'probable'}]
    rejected = [r for r in results if r.status not in {'verified', 'probable'}]

    main_cols = [
        'url', 'site_name', 'company_name', 'category', 'emails', 'confidence', 'signals',
        'matched_url', 'status', 'source_query', 'source_backend', 'title', 'meta_description',
        'pages_checked', 'checked_at',
    ]
    all_cols = list(asdict(SiteResult('', '', '', '', '', '', 0, '', '', '', '', '', '', '', '', '')).keys())
    candidates_df = pd.DataFrame([sanitize_record(asdict(c)) for c in candidates])
    verified_df = pd.DataFrame([sanitize_record(asdict(r)) for r in verified])
    rejected_df = pd.DataFrame([sanitize_record(asdict(r)) for r in rejected])

    if verified_df.empty:
        verified_df = pd.DataFrame(columns=main_cols)
    else:
        verified_df = verified_df[main_cols].sort_values(['confidence', 'url'], ascending=[False, True])

    run_info = pd.DataFrame(
        [
            ['generated_at', dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')],
            ['verified_or_probable_sites', len(verified)],
            ['rejected_or_failed_sites', len(rejected)],
            ['candidate_domains', len(candidates)],
            ['note', 'If verified count is low, use SERPAPI_KEY/BING_SEARCH_API_KEY and increase --target-candidates/--max-queries.'],
            ['note_2', 'DuckDuckGo is free but not reliable for 1000+ discovery.'],
        ],
        columns=['metric', 'value'],
    )

    with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
        verified_df.to_excel(writer, index=False, sheet_name='GombaShop sites')
        candidates_df.to_excel(writer, index=False, sheet_name='Search candidates')
        run_info.to_excel(writer, index=False, sheet_name='Run info')
        if include_rejected:
            if rejected_df.empty:
                rejected_df = pd.DataFrame(columns=all_cols)
            rejected_df.to_excel(writer, index=False, sheet_name='Rejected')

        workbook = writer.book
        for ws in workbook.worksheets:
            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = cell.font.copy(bold=True)
            for col in ws.columns:
                max_len = 0
                col_letter = col[0].column_letter
                for cell in col[:250]:
                    if cell.value is not None:
                        max_len = max(max_len, len(str(cell.value)))
                ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 65)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Find websites using GombaShop and export them to Excel.')
    parser.add_argument('--out', default='gombashop_sites.xlsx', help='Output XLSX file path.')
    parser.add_argument('--seed-file', default='manual_domains.txt', help='Text file with one URL/domain per line.')
    parser.add_argument(
        '--search-backend', choices=['auto', 'serpapi', 'bing', 'google_cse', 'duckduckgo', 'none'],
        default='auto', help='Use serpapi/bing for 1000+ discovery. DuckDuckGo is limited.',
    )
    parser.add_argument('--target-candidates', type=int, default=6000, help='Stop discovery after this many candidate domains.')
    parser.add_argument('--max-queries', type=int, default=180, help='How many generated search queries to use.')
    parser.add_argument('--search-limit-per-query', type=int, default=100, help='Max search results per query.')
    parser.add_argument('--search-delay', type=float, default=0.8, help='Delay between search queries in seconds.')
    parser.add_argument('--request-delay', type=float, default=0.08, help='Delay between requests inside one site analysis.')
    parser.add_argument('--workers', type=int, default=16, help='Concurrent website checks.')
    parser.add_argument('--contact-pages', type=int, default=9, help='Max contact/legal pages to fetch per site.')
    parser.add_argument('--include-rejected', action='store_true', help='Include rejected/non-GombaShop candidates in Excel.')
    parser.add_argument('--ct-gombashop', action='store_true', help='Add candidates from crt.sh *.gombashop.com.')
    parser.add_argument('--ct-limit', type=int, default=5000, help='Max crt.sh subdomain candidates.')
    parser.add_argument('--urlscan-gombashop', action='store_true', help='Add candidates from urlscan.io search.')
    parser.add_argument('--urlscan-limit', type=int, default=3000, help='Max urlscan candidates.')
    parser.add_argument('--no-aggressive-queries', action='store_true', help='Disable industry-split query expansion.')
    parser.add_argument('--shuffle', action='store_true', help='Shuffle candidates before validation.')
    parser.add_argument('--limit-candidates', type=int, default=0, help='Validate only first N candidates after discovery. 0 = all.')
    parser.add_argument('--checkpoint-csv', default='gombashop_progress.csv', help='Write partial results to CSV during the run.')
    parser.add_argument('--checkpoint-every', type=int, default=50, help='Write progress CSV after every N validated candidates. 0 disables checkpoints.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidates = discover_candidates(args)
    if args.shuffle:
        random.shuffle(candidates)
    if args.limit_candidates and args.limit_candidates > 0:
        candidates = candidates[: args.limit_candidates]

    if not candidates:
        print('No candidate domains found. Add domains to manual_domains.txt or configure SerpAPI/Bing.', file=sys.stderr)
        return 2

    print(f'Candidate domains to validate: {len(candidates)}', flush=True)
    results: List[SiteResult] = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        future_map = {
            executor.submit(analyze_site, c, args.request_delay, args.contact_pages): c
            for c in candidates
        }
        for idx, future in enumerate(cf.as_completed(future_map), start=1):
            c = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                result = SiteResult(
                    url=c.url, matched_url='', site_name='', company_name='', category='', emails='',
                    confidence=0, signals='', status='error', source_query=c.source_query,
                    source_backend=c.source_backend, title='', meta_description='', pages_checked='',
                    error=str(exc), checked_at=dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                )
            results.append(result)
            print(f'[{idx}/{len(candidates)}] {result.status:13s} {result.confidence:2d} {result.url}', flush=True)
            if args.checkpoint_every and idx % args.checkpoint_every == 0:
                export_progress_csv(results, args.checkpoint_csv)

    export_progress_csv(results, args.checkpoint_csv)

    priority = {'verified': 3, 'probable': 2, 'not_gombashop': 1, 'fetch_failed': 0, 'error': 0}
    best: Dict[str, SiteResult] = {}
    for r in results:
        current = best.get(r.url)
        if not current or (priority.get(r.status, 0), r.confidence) > (priority.get(current.status, 0), current.confidence):
            best[r.url] = r

    final_results = list(best.values())
    export_excel(final_results, candidates, args.out, args.include_rejected)
    verified_count = sum(1 for r in final_results if r.status in {'verified', 'probable'})
    print(f'Done. Exported {verified_count} verified/probable GombaShop sites to {args.out}', flush=True)
    if verified_count < 1000:
        print('WARNING: fewer than 1000 verified sites. Use SerpAPI/Bing, enable --ct-gombashop and increase --target-candidates/--max-queries.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
