from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import html
import json
import os
from pathlib import Path
import re
import smtplib
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterable
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit
import warnings
from xml.etree import ElementTree

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL.*")
try:
    from urllib3.exceptions import NotOpenSSLWarning

    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except Exception:
    pass

import requests


DEFAULT_KEYWORDS = (
    "专家网络",
    "专家咨询",
    "专家咨询服务",
    "专家咨询服务采购",
    "专家咨询服务采购项目",
    "专家咨询服务采购入围",
    "专家咨询服务入围采购",
    "专家咨询服务入围采购招标",
    "专家咨询服务框架协议",
    "专家咨询服务框架协议采购",
    "专家咨询服务供应商",
    "专家咨询服务供应商招标",
    "专家咨询服务供应商招标项目",
    "专家咨询服务供应商入围",
    "专家咨询服务供应商入围采购",
    "专家咨询服务招标",
    "专家咨询服务选聘",
    "行业专家咨询服务",
    "行业专家咨询服务采购",
    "专家服务选型",
    "专家服务选型入围",
    "专家服务供应商",
    "专家服务供应商入围",
    "外部专家咨询服务",
    "外部专家咨询服务采购",
    "外部专家咨询服务选聘",
    "专家访谈咨询服务",
    "专家访谈咨询服务项目",
    "专家咨询库",
    "专家访谈",
    "专家访谈服务",
    "外部专家",
    "专家库",
    "专家库项目",
    "专家资源",
    "咨询中介机构",
    "咨询平台服务",
)

DEFAULT_SITE_FILTERS = (
    "ctbpsp.com",
    "cebpubservice.com",
    "ccgp.gov.cn",
    "ggzy.gov.cn",
    "sdicc.com.cn",
    "dljczb.com",
    "ec.chng.com.cn",
    "bidding.ec.chng.com.cn",
    "tj.chinamae.com",
    "gy.chinamae.com",
    "china-tender.com.cn",
    "chinabidding.com.cn",
    "etu365.com",
    "supplier.gtht.com",
    "bocichina.com",
    "ctsec.com",
    "stbid.stdzzb.com",
    "bbda.com",
    "epp.sinomach.com.cn",
    "jzcg.chinagoldgroup.com",
    "cfcpn.com",
    "hbbidding.com.cn",
    "sntba.com",
    "jszbtb.com",
    "yuxinec.com",
)
DEFAULT_CTBPSP_MIRROR_SITE_FILTERS = (
    "sdicc.com.cn",
    "gy.chinamae.com",
    "stbid.stdzzb.com",
    "ctsec.com",
    "bbda.com",
    "epp.sinomach.com.cn",
    "jzcg.chinagoldgroup.com",
    "supplier.gtht.com",
    "ec.chng.com.cn",
    "bidding.ec.chng.com.cn",
    "yuxinec.com",
    "sntba.com",
    "jszbtb.com",
)

DEFAULT_EXTRA_QUERIES = (
    '"专家咨询服务采购" "入围"',
    '"专家咨询服务采购" "候选人公示"',
    '"专家咨询服务采购" "结果公告"',
    '"专家咨询服务采购" "更正公告"',
    '"专家咨询服务框架协议"',
    '"专家咨询服务供应商"',
    '"专家咨询服务供应商入围采购"',
    '"专家咨询服务招标"',
    '"专家咨询服务选聘"',
    '"外部专家咨询服务"',
    '"外部专家咨询服务采购"',
    '"外部专家咨询服务选聘"',
    '"行业专家咨询服务采购"',
    '"专家访谈咨询服务"',
    '"专家服务选型"',
    '"专家服务供应商入围"',
    '"证券" "专家咨询服务采购"',
    '"证券" "专家咨询服务供应商"',
    '"证券" "外部专家咨询服务"',
    '"股权投资" "专家咨询服务采购"',
    '"私募基金" "专家咨询服务"',
    '"私募基金" "专家访谈咨询服务"',
    '"基金管理" "专家咨询服务"',
)

DEFAULT_SOURCE_URLS = (
    "https://www.sdicc.com.cn/",
    "https://gy.chinamae.com/purchases-latest",
    "https://gy.chinamae.com/purchases/type/1",
    "https://gy.chinamae.com/purchases/type/3",
    "https://www.ctsec.com/bidding",
    "https://www.ctsec.com/bidding/list/1",
    "https://www.ctsec.com/bidding/list/2",
    "https://www.ctsec.com/bidding/list/3",
)
DEFAULT_SEARCH_URL_TEMPLATE = "https://www.bing.com/search?q={query}&format=rss"
DEFAULT_ENV_FILE = ".env"
DEFAULT_STATE_FILE = "work/bid-alerts/state.json"
DEFAULT_SUBJECT_PREFIX = "招标公告提醒"
DEFAULT_MIN_RELEVANCE_SCORE = 5
DEFAULT_MAX_EMAIL_ITEMS = 50
USER_AGENT = "Mozilla/5.0 (compatible; bid-alerts/0.1; +local-monitor)"
STRONG_TERMS = (
    "行业专家咨询服务",
    "行业专家咨询服务采购",
    "专家咨询服务采购",
    "专家咨询服务采购项目",
    "专家咨询服务采购入围",
    "专家咨询服务入围采购",
    "专家咨询服务入围采购招标",
    "专家咨询服务框架协议",
    "专家咨询服务框架协议采购",
    "专家咨询服务供应商",
    "专家咨询服务供应商招标",
    "专家咨询服务供应商入围",
    "专家咨询服务供应商入围采购",
    "专家咨询服务招标",
    "专家咨询服务选聘",
    "专家服务选型",
    "专家服务供应商",
    "专家服务供应商入围",
    "专家咨询库",
    "专家咨询服务",
    "外部专家咨询服务",
    "外部专家咨询服务采购",
    "外部专家咨询服务选聘",
    "专家访谈咨询服务",
    "专家访谈服务",
    "专家访谈",
    "外部专家",
    "专家网络",
    "专家选型",
    "咨询中介机构",
)
OPPORTUNITY_TERMS = ("采购公告", "招标公告", "公开招标", "选聘", "入围", "框架采购", "供应商招标", "征集")
RESULT_TERMS = (
    "结果公告",
    "结果公示",
    "采购结果",
    "采购结果公告",
    "中标结果",
    "中标结果公示",
    "入围结果",
    "成交结果",
    "中标候选人",
    "候选人公示",
    "入围候选人",
    "评标结果",
    "评标结果公示",
    "询比采购结果",
    "更正公告",
)
WEAK_CONTEXT_TERMS = ("评标专家", "评审专家", "专家评审", "专家抽取", "专家库随机")
FINANCIAL_CONTEXT_TERMS = ("证券", "股权投资", "产业研究院", "资本公司", "基金", "私募", "基金管理", "券商", "投研")
EXCLUDED_TERMS = (
    "户外广告设施安全评估",
    "广告设施安全评估",
)
NOTICE_STAGE_LABELS = {
    "opportunity": "机会公告",
    "change": "变更/澄清",
    "candidate": "候选/评标",
    "result": "结果/成交",
    "other": "其他",
}
PRIORITY_BUCKETS = ("立即关注", "候选机会", "结果复盘", "其他")
OPPORTUNITY_STAGE_TERMS = (
    "采购公告",
    "招标公告",
    "公开招标",
    "询比采购",
    "谈判采购",
    "竞争性磋商",
    "竞争性谈判",
    "选聘",
    "征集",
    "供应商入围",
    "框架协议采购",
)
CHANGE_STAGE_TERMS = ("更正公告", "变更公告", "补遗", "澄清", "延期公告")
CANDIDATE_STAGE_TERMS = ("中标候选人", "候选人公示", "入围候选人", "评标结果")
RESULT_STAGE_TERMS = (
    "中标结果",
    "结果公告",
    "结果公示",
    "成交公告",
    "成交结果",
    "采购结果",
    "入围结果",
)
PROJECT_TITLE_SUFFIX_TERMS = (
    "公开招标公告",
    "招标公告",
    "采购公告",
    "询比采购公告",
    "竞争性磋商公告",
    "竞争性谈判公告",
    "谈判采购公告",
    "更正公告",
    "变更公告",
    "澄清公告",
    "延期公告",
    "中标候选人公示",
    "候选人公示",
    "入围候选人公示",
    "评标结果公示",
    "中标结果公告",
    "中标结果公示",
    "结果公告",
    "结果公示",
    "成交公告",
    "成交结果公告",
    "采购结果公告",
    "入围结果公告",
)
PROJECT_NUMBER_PATTERNS = (
    r"(?:项目编号|招标编号|采购编号|采购项目编号|标段编号|编号)\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9._/\-()（）]{2,})",
    r"[（(]\s*(?:项目编号|招标编号|采购编号|编号)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9._/\-()（）]{2,})\s*[）)]",
)
ATTACHMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip")
GENERIC_ATTACHMENT_TITLES = ("附件", "附件下载", "下载", "点击下载", "查看附件", "公告正文", "PDF")
MIN_TARGET_TERMS = (
    "专家咨询",
    "专家网络",
    "专家访谈",
    "外部专家",
    "外部专家咨询服务",
    "咨询中介机构",
    "行业专家",
    "专家服务",
)
NATIONAL_PLATFORM_DOMAINS = ("ctbpsp.com", "cebpubservice.com")
CTBPSP_UUID_RE = re.compile(r"(?:[?&]uuid=|uuid%3D)([0-9a-fA-F-]{36})")
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "from",
    "ref",
    "refs",
    "source",
    "spm",
}


class BidAlertConfigError(ValueError):
    pass


class BidAlertFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class BidAlertSettings:
    keywords: tuple[str, ...]
    site_filters: tuple[str, ...]
    extra_queries: tuple[str, ...]
    source_urls: tuple[str, ...]
    query_suffix: str
    broad_search: bool
    state_file: Path
    search_url_template: str
    max_results_per_query: int
    min_relevance_score: int
    max_email_items: int
    request_timeout_seconds: int
    recipient_email: str | None
    sender_email: str | None
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_use_ssl: bool
    smtp_use_tls: bool
    subject_prefix: str
    ctbpsp_mirror_search: bool = True
    ctbpsp_mirror_site_filters: tuple[str, ...] = DEFAULT_CTBPSP_MIRROR_SITE_FILTERS
    ctbpsp_mirror_max_items: int = 5


@dataclass(frozen=True)
class AlertItem:
    title: str
    link: str
    summary: str
    published: str
    source: str
    keyword: str
    query: str


@dataclass(frozen=True)
class SourceProfile:
    name: str
    domain: str
    tier: str
    kind: str
    tags: tuple[str, ...] = ()


DEFAULT_SOURCE_PROFILES = (
    SourceProfile("中国招标投标公共服务平台", "ctbpsp.com", "daily", "national-platform", ("聚合",)),
    SourceProfile("中国招标投标公共服务平台", "cebpubservice.com", "daily", "national-platform", ("聚合",)),
    SourceProfile("国投集团电子采购平台", "sdicc.com.cn", "hot", "source-page", ("证券", "央企")),
    SourceProfile("广元机电采购网", "gy.chinamae.com", "daily", "source-page", ("证券", "镜像")),
    SourceProfile("中国华能集团电子招投标系统", "ec.chng.com.cn", "daily", "mirror", ("证券", "央企")),
    SourceProfile("财通证券招标公告", "ctsec.com", "hot", "source-page", ("证券",)),
    SourceProfile("江苏省招标投标公共服务平台", "jszbtb.com", "daily", "mirror", ("证券",)),
    SourceProfile("陕西采购与招标网", "sntba.com", "daily", "mirror", ("证券",)),
)


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(value: str | None, default: int, *, name: str) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise BidAlertConfigError(f"Invalid integer for {name}: {value}") from exc


def _split_list(value: str | None, default: Iterable[str]) -> tuple[str, ...]:
    if not value:
        return tuple(default)
    parts = re.split(r"[,，;\n]+", value)
    return tuple(part.strip() for part in parts if part.strip())


def _unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_dotenv_values(path: Path = Path(DEFAULT_ENV_FILE)) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    with path.open("r", encoding="utf-8") as file_obj:
        for raw_line in file_obj:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                continue
            values[key] = _unquote_env_value(value)
    return values


def source_profile_for_url(url: str) -> SourceProfile | None:
    host = urlsplit(url).netloc.lower()
    if not host and "://" not in url:
        host = url.lower()
    for profile in DEFAULT_SOURCE_PROFILES:
        if host == profile.domain or host.endswith(f".{profile.domain}") or profile.domain in url.lower():
            return profile
    return None


def source_display_name(item: AlertItem) -> str:
    profile = source_profile_for_url(item.link) or source_profile_for_url(item.source)
    if not profile:
        return item.source or "-"
    return f"{profile.name}（{profile.tier}）"


def load_settings(env: dict[str, str] | None = None) -> BidAlertSettings:
    values = env if env is not None else {**load_dotenv_values(), **dict(os.environ)}
    keywords = _split_list(values.get("BID_ALERT_KEYWORDS"), DEFAULT_KEYWORDS)
    site_filters = _split_list(values.get("BID_ALERT_SITE_FILTERS"), DEFAULT_SITE_FILTERS)
    extra_queries = _split_list(values.get("BID_ALERT_EXTRA_QUERIES"), DEFAULT_EXTRA_QUERIES)
    source_urls = _split_list(values.get("BID_ALERT_SOURCE_URLS"), DEFAULT_SOURCE_URLS)
    ctbpsp_mirror_site_filters = _split_list(
        values.get("BID_ALERT_CTBPSP_MIRROR_SITE_FILTERS"),
        DEFAULT_CTBPSP_MIRROR_SITE_FILTERS,
    )
    if not keywords:
        raise BidAlertConfigError("BID_ALERT_KEYWORDS must include at least one keyword")
    if not site_filters:
        raise BidAlertConfigError("BID_ALERT_SITE_FILTERS must include at least one site")

    return BidAlertSettings(
        keywords=keywords,
        site_filters=site_filters,
        extra_queries=extra_queries,
        source_urls=source_urls,
        query_suffix=values.get("BID_ALERT_QUERY_SUFFIX", "").strip(),
        broad_search=_env_bool(values.get("BID_ALERT_BROAD_SEARCH"), True),
        state_file=Path(values.get("BID_ALERT_STATE_FILE", DEFAULT_STATE_FILE)),
        search_url_template=values.get("BID_ALERT_SEARCH_URL_TEMPLATE", DEFAULT_SEARCH_URL_TEMPLATE),
        max_results_per_query=_env_int(
            values.get("BID_ALERT_MAX_RESULTS_PER_QUERY"),
            10,
            name="BID_ALERT_MAX_RESULTS_PER_QUERY",
        ),
        min_relevance_score=_env_int(
            values.get("BID_ALERT_MIN_RELEVANCE_SCORE"),
            DEFAULT_MIN_RELEVANCE_SCORE,
            name="BID_ALERT_MIN_RELEVANCE_SCORE",
        ),
        max_email_items=_env_int(
            values.get("BID_ALERT_MAX_EMAIL_ITEMS"),
            DEFAULT_MAX_EMAIL_ITEMS,
            name="BID_ALERT_MAX_EMAIL_ITEMS",
        ),
        request_timeout_seconds=_env_int(
            values.get("BID_ALERT_REQUEST_TIMEOUT_SECONDS"),
            20,
            name="BID_ALERT_REQUEST_TIMEOUT_SECONDS",
        ),
        recipient_email=values.get("BID_ALERT_RECIPIENT_EMAIL") or None,
        sender_email=values.get("BID_ALERT_SENDER_EMAIL") or values.get("BID_ALERT_SMTP_USERNAME") or None,
        smtp_host=values.get("BID_ALERT_SMTP_HOST") or None,
        smtp_port=_env_int(values.get("BID_ALERT_SMTP_PORT"), 587, name="BID_ALERT_SMTP_PORT"),
        smtp_username=values.get("BID_ALERT_SMTP_USERNAME") or None,
        smtp_password=values.get("BID_ALERT_SMTP_PASSWORD") or None,
        smtp_use_ssl=_env_bool(values.get("BID_ALERT_SMTP_USE_SSL"), False),
        smtp_use_tls=_env_bool(values.get("BID_ALERT_SMTP_USE_TLS"), True),
        subject_prefix=values.get("BID_ALERT_SUBJECT_PREFIX", DEFAULT_SUBJECT_PREFIX).strip()
        or DEFAULT_SUBJECT_PREFIX,
        ctbpsp_mirror_search=_env_bool(values.get("BID_ALERT_CTBPSP_MIRROR_SEARCH"), True),
        ctbpsp_mirror_site_filters=ctbpsp_mirror_site_filters,
        ctbpsp_mirror_max_items=_env_int(
            values.get("BID_ALERT_CTBPSP_MIRROR_MAX_ITEMS"),
            5,
            name="BID_ALERT_CTBPSP_MIRROR_MAX_ITEMS",
        ),
    )


def build_query(keyword: str, site_filter: str, query_suffix: str = "") -> str:
    site_prefix = f"site:{site_filter} " if site_filter else ""
    query = f'{site_prefix}"{keyword}"'
    return append_query_suffix(query, query_suffix)


def append_query_suffix(query: str, query_suffix: str = "") -> str:
    if query_suffix:
        query = f"{query} {query_suffix.strip()}"
    return query


def build_search_url(template: str, query: str) -> str:
    return template.format(query=quote_plus(query), raw_query=query)


def build_queries(settings: BidAlertSettings) -> list[tuple[str, str, str]]:
    queries = [
        (keyword, site_filter, build_query(keyword, site_filter, settings.query_suffix))
        for site_filter in settings.site_filters
        for keyword in settings.keywords
    ]
    if settings.broad_search:
        queries.extend(
            (keyword, "", build_query(keyword, "", settings.query_suffix))
            for keyword in settings.keywords
        )
    queries.extend(
        (extra_query, "", append_query_suffix(extra_query, settings.query_suffix))
        for extra_query in settings.extra_queries
    )
    return queries


def _strip_markup(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ElementTree.Element, *names: str) -> str:
    wanted = set(names)
    for child in list(element):
        if _local_name(child.tag) in wanted:
            return _strip_markup(child.text or "")
    return ""


def _link_text(element: ElementTree.Element) -> str:
    link = _child_text(element, "link")
    if link:
        return link
    for child in list(element):
        if _local_name(child.tag) == "link":
            href = child.attrib.get("href")
            if href:
                return href.strip()
    return ""


def parse_feed(xml_text: str, *, keyword: str, query: str, max_items: int) -> list[AlertItem]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise BidAlertFetchError("Search feed returned invalid XML") from exc

    items: list[AlertItem] = []
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        title = _child_text(node, "title")
        link = _link_text(node)
        summary = _child_text(node, "description", "summary", "content")
        published = _child_text(node, "pubDate", "published", "updated")
        if not title or not link:
            continue
        items.append(
            AlertItem(
                title=title,
                link=link,
                summary=summary,
                published=published,
                source=urlsplit(link).netloc.lower(),
                keyword=keyword,
                query=query,
            )
        )
        if len(items) >= max_items:
            break
    return items


def fetch_feed(url: str, *, timeout_seconds: int) -> str:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return fetch_with_curl(url, timeout_seconds=timeout_seconds, label="search feed", original_error=exc)
    return response.text


def fetch_page(url: str, *, timeout_seconds: int) -> str:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return fetch_with_curl(url, timeout_seconds=timeout_seconds, label="source page", original_error=exc)
    return response.text


def fetch_with_curl(
    url: str,
    *,
    timeout_seconds: int,
    label: str,
    original_error: requests.RequestException,
) -> str:
    try:
        completed = subprocess.run(
            [
                "curl",
                "-L",
                "--fail",
                "--silent",
                "--show-error",
                "--retry",
                "2",
                "--retry-delay",
                "1",
                "--retry-all-errors",
                "--max-time",
                str(timeout_seconds),
                "--user-agent",
                USER_AGENT,
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as curl_error:
        raise BidAlertFetchError(f"Failed to fetch {label}: {url}") from curl_error
    if not completed.stdout:
        raise BidAlertFetchError(f"Failed to fetch {label}: {url}") from original_error
    return completed.stdout


def parse_public_html_items(html_text: str, *, source_url: str) -> list[AlertItem]:
    items = parse_structured_list_items(html_text, source_url=source_url)
    items.extend(parse_attachment_items(html_text, source_url=source_url))
    items.extend(parse_anchor_items(html_text, source_url=source_url))
    items.extend(parse_sdicc_table_items(html_text, source_url=source_url))
    return dedupe_items(items)


def _first_match(pattern: str, text: str, *, flags: int = re.IGNORECASE | re.DOTALL) -> str:
    match = re.search(pattern, text, flags=flags)
    return _strip_markup(match.group(1)) if match else ""


def _title_from_anchor(attrs: str, body: str) -> str:
    title_match = re.search(r"""title\s*=\s*["'](?P<title>[^"']+)["']""", attrs, flags=re.IGNORECASE)
    if title_match:
        return _strip_markup(title_match.group("title"))
    hot_title = _first_match(r"""<span\b[^>]*class\s*=\s*["'][^"']*hots_tit[^"']*["'][^>]*>(.*?)</span>""", body)
    if hot_title:
        return hot_title
    return _strip_markup(body)


def _published_from_block(block_text: str) -> str:
    explicit_time = _first_match(r"""<span\b[^>]*class\s*=\s*["'][^"']*time[^"']*["'][^>]*>(.*?)</span>""", block_text)
    if explicit_time:
        return explicit_time
    explicit_date = _first_match(r"""<div\b[^>]*style\s*=\s*["'][^"']*color:\s*red[^"']*["'][^>]*>(.*?)</div>""", block_text)
    if explicit_date:
        return explicit_date
    text = _strip_markup(block_text)
    date_match = re.search(r"\b20\d{2}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b", text)
    if date_match:
        return date_match.group(0)
    return ""


def _looks_like_attachment_href(href: str) -> bool:
    path = urlsplit(href).path.lower()
    if any(path.endswith(extension) for extension in ATTACHMENT_EXTENSIONS):
        return True
    return "download" in path or "attachment" in path


def _filename_from_href(href: str) -> str:
    path = urlsplit(href).path
    filename = Path(path).name
    return html.unescape(filename) if filename else ""


def _is_generic_attachment_title(title: str) -> bool:
    normalized = re.sub(r"\s+", "", title).upper()
    return normalized in {term.upper() for term in GENERIC_ATTACHMENT_TITLES}


def _notice_title_from_nearby_text(text: str) -> str:
    normalized = _strip_markup(text)
    candidates = re.findall(
        r"([\u4e00-\u9fffA-Za-z0-9（）()【】\[\]\-—_\s]{8,140}(?:公告|公示|项目|通知))",
        normalized,
    )
    for candidate in reversed(candidates):
        candidate = re.sub(r"\s+", "", candidate).strip("：:，,。；;")
        if any(term in candidate for term in MIN_TARGET_TERMS):
            return candidate
    return ""


def _attachment_title(html_text: str, match: re.Match[str], attrs: str, body: str, href: str) -> str:
    title = _title_from_anchor(attrs, body)
    if title and not _is_generic_attachment_title(title):
        return title
    start = max(0, match.start() - 600)
    end = min(len(html_text), match.end() + 200)
    nearby_title = _notice_title_from_nearby_text(html_text[start:end])
    if nearby_title:
        return nearby_title
    filename = _filename_from_href(href)
    return filename or title or "附件"


def parse_attachment_items(html_text: str, *, source_url: str) -> list[AlertItem]:
    items: list[AlertItem] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        attrs = match.group("attrs")
        href_match = re.search(r"""href\s*=\s*["'](?P<href>[^"']+)["']""", attrs, flags=re.IGNORECASE)
        if not href_match:
            continue
        href = href_match.group("href").strip()
        if href.startswith(("javascript:", "#")) or not _looks_like_attachment_href(href):
            continue
        items.append(
            AlertItem(
                title=_attachment_title(html_text, match, attrs, match.group("body"), href),
                link=urljoin(source_url, href),
                summary="公开页附件链接",
                published=_published_from_block(html_text[max(0, match.start() - 600) : match.end() + 200]),
                source=urlsplit(source_url).netloc.lower(),
                keyword="source-attachment",
                query=source_url,
            )
        )
    return items


def parse_structured_list_items(html_text: str, *, source_url: str) -> list[AlertItem]:
    items: list[AlertItem] = []
    for block_match in re.finditer(r"<li\b[^>]*>(?P<body>.*?)</li>", html_text, re.IGNORECASE | re.DOTALL):
        block = block_match.group("body")
        for anchor_match in re.finditer(
            r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            attrs = anchor_match.group("attrs")
            href_match = re.search(r"""href\s*=\s*["'](?P<href>[^"']+)["']""", attrs, flags=re.IGNORECASE)
            if not href_match:
                continue
            href = href_match.group("href").strip()
            title = _title_from_anchor(attrs, anchor_match.group("body"))
            if not title or href.startswith(("javascript:", "#")):
                continue
            if "/news/" not in href and "/bidding/" not in href and "/res/pdf/" not in href:
                continue
            items.append(
                AlertItem(
                    title=title,
                    link=urljoin(source_url, href),
                    summary="",
                    published=_published_from_block(block),
                    source=urlsplit(source_url).netloc.lower(),
                    keyword="source-page",
                    query=source_url,
                )
            )
    return items


def parse_anchor_items(html_text: str, *, source_url: str) -> list[AlertItem]:
    items: list[AlertItem] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        attrs = match.group("attrs")
        href_match = re.search(r"""href\s*=\s*["'](?P<href>[^"']+)["']""", attrs, flags=re.IGNORECASE)
        if not href_match:
            continue
        title = _title_from_anchor(attrs, match.group("body"))
        href = href_match.group("href").strip()
        if not title or href.startswith(("javascript:", "#")):
            continue
        items.append(
            AlertItem(
                title=title,
                link=urljoin(source_url, href),
                summary="",
                published="",
                source=urlsplit(source_url).netloc.lower(),
                keyword="source-page",
                query=source_url,
            )
        )
    return items


def parse_sdicc_table_items(html_text: str, *, source_url: str) -> list[AlertItem]:
    items: list[AlertItem] = []
    for row_match in re.finditer(r"<tr\b(?P<attrs>[^>]*)>(?P<body>.*?)</tr>", html_text, re.IGNORECASE | re.DOTALL):
        onclick_match = re.search(
            r"""onclick\s*=\s*(?P<quote>["'])(?P<call>urlChange(?:Hxr|Jg|Bg|PlanNotice|PreNotice)?\(.*?\))(?P=quote)""",
            row_match.group("attrs"),
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not onclick_match:
            continue
        link = sdicc_detail_url(source_url, onclick_match.group("call"))
        if not link:
            continue
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row_match.group("body"), re.IGNORECASE | re.DOTALL)
        cell_texts = [_strip_markup(cell) for cell in cells]
        if not cell_texts:
            continue
        title = cell_texts[0]
        published = cell_texts[-1] if len(cell_texts) >= 2 else ""
        items.append(
            AlertItem(
                title=title,
                link=link,
                summary="",
                published=published,
                source=urlsplit(source_url).netloc.lower(),
                keyword="source-page",
                query=source_url,
            )
        )
    return items


def sdicc_detail_url(source_url: str, call_text: str) -> str | None:
    match = re.match(r"urlChange(?P<kind>Hxr|Jg|Bg|PlanNotice|PreNotice)?\((?P<args>.*)\)$", call_text)
    if not match:
        return None
    args = re.findall(r"""['"]([^'"]+)['"]""", match.group("args"))
    kind = match.group("kind") or ""
    if kind == "" and len(args) >= 2:
        return urljoin(source_url, f"/cgxx/ggDetail?gcGuid={args[1]}&ggGuid={args[0]}")
    if kind == "Hxr" and len(args) >= 2:
        return urljoin(source_url, f"/cgxx/zbhxrDetail?bdGuid={args[0]}&guid={args[1]}")
    if kind == "Jg" and len(args) >= 2:
        return urljoin(source_url, f"/cgxx/zbjgDetail?bdGuid={args[0]}&guid={args[1]}")
    if kind == "Bg" and len(args) >= 2:
        return urljoin(source_url, f"/cgxx/bgggDetail?ggGuid={args[0]}&shiXiangGuid={args[1]}")
    if kind == "PlanNotice" and args:
        return urljoin(source_url, f"/cgxx/planNoticeDetail?guid={args[0]}")
    if kind == "PreNotice" and len(args) >= 2:
        return urljoin(source_url, f"/cgxx/preNoticeDetail?guid={args[0]}&fromType={args[1]}")
    return None


def collect_source_page_items(
    settings: BidAlertSettings,
    *,
    progress: Callable[[str], None] | None = None,
) -> list[AlertItem]:
    items: list[AlertItem] = []
    errors: list[BidAlertFetchError] = []
    for source_url in settings.source_urls:
        _emit_progress(progress, f"读取源站公开页：{source_url}")
        try:
            page_text = fetch_page(source_url, timeout_seconds=settings.request_timeout_seconds)
        except BidAlertFetchError as exc:
            errors.append(exc)
            continue
        items.extend(parse_public_html_items(page_text, source_url=source_url))
    if errors and not items:
        raise BidAlertFetchError(f"All source pages failed. First error: {errors[0]}")
    return sort_items_by_relevance(dedupe_items(items))


def is_national_platform_item(item: AlertItem) -> bool:
    link = item.link.lower()
    host = urlsplit(item.link).netloc.lower()
    return any(domain in host or f"{domain}/" in link for domain in NATIONAL_PLATFORM_DOMAINS)


def ctbpsp_uuid(item: AlertItem) -> str:
    match = CTBPSP_UUID_RE.search(item.link)
    return match.group(1).lower() if match else ""


def _normalized_notice_text(value: str) -> str:
    return re.sub(r"\s+", "", _strip_markup(value)).strip()


def _quoted_title_query(title: str) -> str:
    safe_title = _strip_markup(title).replace('"', " ").strip()
    return f'"{safe_title}"'


def build_ctbpsp_mirror_queries(item: AlertItem, settings: BidAlertSettings) -> list[str]:
    title_query = _quoted_title_query(item.title)
    queries = [title_query]
    uuid = ctbpsp_uuid(item)
    if uuid:
        queries.append(f'{title_query} "{uuid}"')
    queries.extend(f"site:{site_filter} {title_query}" for site_filter in settings.ctbpsp_mirror_site_filters)
    return [append_query_suffix(query, settings.query_suffix) for query in queries]


def likely_mirror_for_national_platform_item(original: AlertItem, candidate: AlertItem) -> bool:
    if is_national_platform_item(candidate) or is_excluded_item(candidate) or not has_target_terms(candidate):
        return False

    original_title = _normalized_notice_text(original.title)
    candidate_text = _normalized_notice_text(f"{candidate.title}\n{candidate.summary}\n{candidate.link}")
    candidate_title = _normalized_notice_text(candidate.title)
    if original_title and original_title in candidate_text:
        return True
    if candidate_title and candidate_title in original_title:
        return True

    original_terms = [term for term in STRONG_TERMS if term in original.title]
    if not original_terms or not any(term in candidate_text for term in original_terms):
        return False
    context_terms = FINANCIAL_CONTEXT_TERMS + OPPORTUNITY_TERMS + RESULT_TERMS
    original_context = [term for term in context_terms if term in original.title]
    return any(term in candidate_text for term in original_context)


def attach_national_platform_reference(candidate: AlertItem, original: AlertItem) -> AlertItem:
    uuid = ctbpsp_uuid(original)
    note = f"国家平台聚合链接：{original.link}"
    if uuid:
        note = f"{note}\n国家平台 UUID：{uuid}"
    summary_parts = [part for part in (candidate.summary.strip(), note) if part]
    return replace(
        candidate,
        summary="\n".join(summary_parts),
        keyword=f"{candidate.keyword} + ctbpsp补链",
    )


def search_public_mirrors_for_national_platform_item(
    item: AlertItem,
    settings: BidAlertSettings,
) -> list[AlertItem]:
    mirrors: list[AlertItem] = []
    for query in build_ctbpsp_mirror_queries(item, settings):
        url = build_search_url(settings.search_url_template, query)
        try:
            feed_text = fetch_feed(url, timeout_seconds=settings.request_timeout_seconds)
        except BidAlertFetchError:
            continue
        candidates = parse_feed(
            feed_text,
            keyword="ctbpsp补链",
            query=query,
            max_items=settings.max_results_per_query,
        )
        mirrors.extend(
            attach_national_platform_reference(candidate, item)
            for candidate in candidates
            if likely_mirror_for_national_platform_item(item, candidate)
        )
    return sort_items_by_relevance(dedupe_items(mirrors))


def enrich_national_platform_items(settings: BidAlertSettings, items: list[AlertItem]) -> list[AlertItem]:
    if not settings.ctbpsp_mirror_search or settings.ctbpsp_mirror_max_items <= 0:
        return items

    replacement_ids: set[str] = set()
    enriched_items: list[AlertItem] = []
    national_platform_items = [item for item in items if is_national_platform_item(item)]
    for item in national_platform_items[: settings.ctbpsp_mirror_max_items]:
        mirrors = search_public_mirrors_for_national_platform_item(item, settings)
        if not mirrors:
            continue
        replacement_ids.add(item_id(item))
        enriched_items.extend(mirrors)

    kept_items = [item for item in items if item_id(item) not in replacement_ids]
    return sort_items_by_relevance(dedupe_items(enriched_items + kept_items))


def collect_alert_items(
    settings: BidAlertSettings,
    *,
    progress: Callable[[str], None] | None = None,
) -> list[AlertItem]:
    items: list[AlertItem] = []
    errors: list[BidAlertFetchError] = []
    queries = build_queries(settings)
    for index, (keyword, _site_filter, query) in enumerate(queries, start=1):
        _emit_progress(progress, f"搜索公开 RSS：{index}/{len(queries)} {keyword}")
        url = build_search_url(settings.search_url_template, query)
        try:
            feed_text = fetch_feed(url, timeout_seconds=settings.request_timeout_seconds)
        except BidAlertFetchError as exc:
            errors.append(exc)
            continue
        items.extend(
            parse_feed(
                feed_text,
                keyword=keyword,
                query=query,
                max_items=settings.max_results_per_query,
            )
        )
    try:
        items.extend(collect_source_page_items(settings, progress=progress))
    except BidAlertFetchError as exc:
        errors.append(exc)
    items = enrich_national_platform_items(settings, dedupe_items(items))
    if errors and not items:
        raise BidAlertFetchError(f"All search feeds failed. First error: {errors[0]}")
    return sort_items_by_relevance(dedupe_items(items))


def canonical_link(link: str) -> str:
    parts = urlsplit(link.strip())
    uuid_match = CTBPSP_UUID_RE.search(link)
    if uuid_match and any(domain in parts.netloc.lower() for domain in NATIONAL_PLATFORM_DOMAINS):
        uuid = uuid_match.group(1).lower()
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), "/", "", f"/bulletinDetail?uuid={uuid}"))
    filtered_query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_KEYS
            and not any(key.lower().startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES)
        ],
        doseq=True,
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, filtered_query, ""))


def item_id(item: AlertItem) -> str:
    stable_value = canonical_link(item.link) or f"{item.title}\n{item.source}"
    return hashlib.sha256(stable_value.encode("utf-8")).hexdigest()


def dedupe_items(items: Iterable[AlertItem]) -> list[AlertItem]:
    seen: set[str] = set()
    deduped: list[AlertItem] = []
    for item in items:
        identifier = item_id(item)
        if identifier in seen:
            continue
        seen.add(identifier)
        deduped.append(item)
    return deduped


def relevance_score(item: AlertItem) -> int:
    title = item.title
    text = f"{item.title}\n{item.summary}"
    score = 0
    score += sum(5 for term in STRONG_TERMS if term in title)
    score += sum(2 for term in STRONG_TERMS if term not in title and term in text)
    score += sum(2 for term in OPPORTUNITY_TERMS if term in title)
    score += sum(2 for term in RESULT_TERMS if term in title)
    score += sum(1 for term in FINANCIAL_CONTEXT_TERMS if term in text)
    score -= sum(3 for term in WEAK_CONTEXT_TERMS if term in text)
    return score


def has_target_terms(item: AlertItem) -> bool:
    text = f"{item.title}\n{item.summary}"
    return any(term in text for term in MIN_TARGET_TERMS)


def is_excluded_item(item: AlertItem) -> bool:
    text = f"{item.title}\n{item.summary}"
    return any(term in text for term in EXCLUDED_TERMS)


def relevance_label(item: AlertItem) -> str:
    score = relevance_score(item)
    if score >= 5:
        return "强相关"
    if score >= 2:
        return "候选"
    return "弱相关"


def sort_items_by_relevance(items: Iterable[AlertItem]) -> list[AlertItem]:
    return sorted(items, key=lambda item: (relevance_score(item), item.published), reverse=True)


def filter_items_by_relevance(items: Iterable[AlertItem], *, min_score: int) -> list[AlertItem]:
    return [
        item
        for item in sort_items_by_relevance(items)
        if not is_excluded_item(item) and has_target_terms(item) and relevance_score(item) >= min_score
    ]


def notice_stage(item: AlertItem) -> str:
    text = f"{item.title}\n{item.summary}"
    if any(term in text for term in CHANGE_STAGE_TERMS):
        return "change"
    if any(term in text for term in CANDIDATE_STAGE_TERMS):
        return "candidate"
    if any(term in text for term in RESULT_STAGE_TERMS):
        return "result"
    if any(term in text for term in OPPORTUNITY_STAGE_TERMS):
        return "opportunity"
    return "other"


def has_financial_context(item: AlertItem) -> bool:
    text = f"{item.title}\n{item.summary}"
    return any(term in text for term in FINANCIAL_CONTEXT_TERMS)


def priority_bucket(item: AlertItem) -> str:
    if is_excluded_item(item) or not has_target_terms(item):
        return "其他"

    stage = notice_stage(item)
    score = relevance_score(item)
    high_intent_terms = ("入围", "框架", "供应商", "选聘", "外部专家", "行业专家")
    text = f"{item.title}\n{item.summary}"

    if stage in {"opportunity", "change"}:
        if score >= 5 and (has_financial_context(item) or any(term in text for term in high_intent_terms)):
            return "立即关注"
        return "候选机会"
    if stage in {"candidate", "result"}:
        return "结果复盘"
    if score >= 5:
        return "候选机会"
    return "其他"


def extract_project_no(text: str) -> str:
    clean_text = _strip_markup(text)
    for pattern in PROJECT_NUMBER_PATTERNS:
        match = re.search(pattern, clean_text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().rstrip("，,。；;）)]】")
    return ""


def normalize_project_title(title: str) -> str:
    text = _strip_markup(title)
    bracketed_project = re.match(r"^[\[【](?P<inner>.+?)[\]】]\s*(?P<tail>.+)$", text)
    if bracketed_project and any(term in bracketed_project.group("tail") for term in PROJECT_TITLE_SUFFIX_TERMS):
        text = bracketed_project.group("inner")

    text = re.sub(
        r"^[\[【](?:公开招标|招标公告|采购公告|询比采购|竞争性磋商|竞争性谈判|结果公示|候选人公示)[\]】]\s*",
        "",
        text,
    )
    text = re.sub(r"\s+", "", text)

    changed = True
    suffixes = sorted(PROJECT_TITLE_SUFFIX_TERMS, key=len, reverse=True)
    while changed:
        changed = False
        for suffix in suffixes:
            if text.endswith(suffix):
                text = text[: -len(suffix)].strip("：:，,。；; -_")
                changed = True
                break
    return text.strip("[]【】（）()：:，,。；; -_")


def project_key(item: AlertItem) -> str:
    project_no = extract_project_no(f"{item.title}\n{item.summary}")
    title = normalize_project_title(item.title)
    stable_value = f"{project_no}|{title}" if project_no else title
    if not stable_value:
        stable_value = canonical_link(item.link) or item.title
    return hashlib.sha256(stable_value.encode("utf-8")).hexdigest()[:16]


def group_items_by_project(items: Iterable[AlertItem]) -> dict[str, list[AlertItem]]:
    grouped: dict[str, list[AlertItem]] = {}
    for item in items:
        grouped.setdefault(project_key(item), []).append(item)
    return grouped


def project_stage_summary(item: AlertItem, project_groups: dict[str, list[AlertItem]]) -> str:
    peers = project_groups.get(project_key(item), [])
    if len(peers) <= 1:
        return ""
    stage_counts: list[str] = []
    for stage in ("opportunity", "change", "candidate", "result", "other"):
        count = sum(1 for peer in peers if notice_stage(peer) == stage)
        if count:
            stage_counts.append(f"{NOTICE_STAGE_LABELS[stage]}{count}条")
    return "同项目已发现阶段：" + "、".join(stage_counts)


def load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"seen_ids": {}}
    with path.open("r", encoding="utf-8") as file_obj:
        state = json.load(file_obj)
    if not isinstance(state, dict) or not isinstance(state.get("seen_ids"), dict):
        raise BidAlertConfigError(f"Invalid state file: {path}")
    return state


def save_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as file_obj:
        json.dump(state, file_obj, ensure_ascii=False, indent=2, sort_keys=True)
        file_obj.write("\n")
    tmp_path.replace(path)


def select_new_items(items: Iterable[AlertItem], state: dict[str, object]) -> list[AlertItem]:
    seen_ids = state.get("seen_ids", {})
    if not isinstance(seen_ids, dict):
        raise BidAlertConfigError("Invalid state: seen_ids must be a dict")
    return [item for item in items if item_id(item) not in seen_ids]


def mark_seen(state: dict[str, object], items: Iterable[AlertItem], *, max_seen: int = 5000) -> None:
    seen_ids = state.setdefault("seen_ids", {})
    if not isinstance(seen_ids, dict):
        raise BidAlertConfigError("Invalid state: seen_ids must be a dict")
    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        seen_ids[item_id(item)] = {
            "title": item.title,
            "link": canonical_link(item.link),
            "keyword": item.keyword,
            "stage": notice_stage(item),
            "project_key": project_key(item),
            "first_seen_at": now,
        }
    if len(seen_ids) > max_seen:
        sorted_items = sorted(
            seen_ids.items(),
            key=lambda pair: str(pair[1].get("first_seen_at", "")) if isinstance(pair[1], dict) else "",
        )
        keep = dict(sorted_items[-max_seen:])
        seen_ids.clear()
        seen_ids.update(keep)


def _require_email_settings(settings: BidAlertSettings) -> None:
    missing = [
        name
        for name, value in {
            "BID_ALERT_RECIPIENT_EMAIL": settings.recipient_email,
            "BID_ALERT_SENDER_EMAIL or BID_ALERT_SMTP_USERNAME": settings.sender_email,
            "BID_ALERT_SMTP_HOST": settings.smtp_host,
            "BID_ALERT_SMTP_USERNAME": settings.smtp_username,
            "BID_ALERT_SMTP_PASSWORD": settings.smtp_password,
        }.items()
        if not value
    ]
    if missing:
        raise BidAlertConfigError("Missing email settings: " + ", ".join(missing))


def _curl_config_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def render_email_body(
    items: list[AlertItem],
    *,
    total_found: int | None = None,
    eligible_count: int | None = None,
) -> str:
    lines = [
        "以下是两层免费监控发现的专家相关新公告。",
        "",
        "说明：第一层来自公开搜索 RSS 和源站公开列表页；第二层会把国家平台聚合页的标题和 UUID 用于补找公开源站或镜像链接。",
        "本提醒不直接抓取付费或禁止自动抓取的网站页面，不绕过验证码、WAF 或付费订阅，只保存可公开访问的标题、来源和链接，结果可能存在延迟。",
        "",
    ]
    if total_found is not None or eligible_count is not None:
        lines.extend(
            [
                "## 本次运行摘要",
                "",
                f"- 原始抓取结果：{total_found if total_found is not None else '-'} 条",
                f"- 符合规则结果：{eligible_count if eligible_count is not None else '-'} 条",
                f"- 新增公告：{len(items)} 条",
                "",
            ]
        )
    if not items:
        lines.extend(
            [
                "## 本次未发现新目标",
                "",
                "本次扫描没有发现新的专家咨询/专家网络相关目标公告。系统已正常运行；若后续发现新公告，会在下一次邮件中列出。",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    ordered_items = sort_items_by_relevance(items)
    project_groups = group_items_by_project(ordered_items)
    for bucket in PRIORITY_BUCKETS:
        grouped_items = [item for item in ordered_items if priority_bucket(item) == bucket]
        if not grouped_items:
            continue
        lines.extend([f"## {bucket}", ""])
        for index, item in enumerate(grouped_items, start=1):
            stage = notice_stage(item)
            lines.extend(
                [
                    f"{index}. [{item.keyword}] {item.title}",
                    f"   来源：{source_display_name(item)}",
                    f"   时间：{item.published or '-'}",
                    f"   阶段：{NOTICE_STAGE_LABELS[stage]}；相关性：{relevance_label(item)}",
                    f"   链接：{item.link}",
                ]
            )
            stage_summary = project_stage_summary(item, project_groups)
            if stage_summary:
                lines.append(f"   {stage_summary}")
            if item.summary:
                lines.append(f"   摘要：{item.summary}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_failure_email_body(error: Exception) -> str:
    lines = [
        "本次招标公告云端扫描已经触发，但扫描过程失败，因此没有完成正常结果筛选。",
        "",
        "这封邮件表示自动任务本身仍在运行并且发信链路可用；它不是“未发现新目标”的摘要。请到 GitHub Actions 对应运行里查看 Run bid alert 日志。",
        "",
        "## 故障摘要",
        "",
        f"- 错误类型：{type(error).__name__}",
        f"- 错误信息：{error}",
        "",
        "常见原因包括公开搜索 RSS 临时不可用、源站网络超时、GitHub Actions 出口网络被目标站限制等。下一次定时任务会继续尝试。",
    ]
    return "\n".join(lines).strip() + "\n"


def _emit_progress(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)


def _send_message(
    settings: BidAlertSettings,
    message: EmailMessage,
    *,
    progress: Callable[[str], None] | None = None,
    smtp_attempts: int = 3,
    curl_retries: int = 2,
) -> None:
    _require_email_settings(settings)
    assert settings.recipient_email
    assert settings.sender_email
    assert settings.smtp_host
    assert settings.smtp_username
    assert settings.smtp_password

    smtp_class = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
    last_error: Exception | None = None
    for attempt in range(max(1, smtp_attempts)):
        _emit_progress(progress, f"正在连接 SMTP（第 {attempt + 1} 次）...")
        try:
            with smtp_class(settings.smtp_host, settings.smtp_port, timeout=settings.request_timeout_seconds) as smtp:
                if settings.smtp_use_tls and not settings.smtp_use_ssl:
                    smtp.starttls()
                smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
            _emit_progress(progress, "SMTP 发送成功。")
            return
        except (OSError, smtplib.SMTPException) as exc:
            last_error = exc
            _emit_progress(progress, "SMTP 连接失败。")
            if attempt < max(1, smtp_attempts) - 1:
                time.sleep(1)
    try:
        _emit_progress(progress, "尝试使用系统 curl 发送邮件...")
        send_email_with_curl(settings, message, retries=curl_retries)
        _emit_progress(progress, "curl 发送成功。")
        return
    except BidAlertFetchError as curl_error:
        _emit_progress(progress, "curl 发送失败。")
        raise BidAlertFetchError("Failed to send email via SMTP") from curl_error or last_error


def send_email(
    settings: BidAlertSettings,
    items: list[AlertItem],
    *,
    total_found: int | None = None,
    eligible_count: int | None = None,
    progress: Callable[[str], None] | None = None,
    smtp_attempts: int = 3,
    curl_retries: int = 2,
) -> None:
    _require_email_settings(settings)
    assert settings.recipient_email
    assert settings.sender_email

    message = EmailMessage()
    message["From"] = settings.sender_email
    message["To"] = settings.recipient_email
    message["Subject"] = f"{settings.subject_prefix}：{len(items)} 条专家相关新公告"
    message.set_content(render_email_body(items, total_found=total_found, eligible_count=eligible_count))
    _send_message(
        settings,
        message,
        progress=progress,
        smtp_attempts=smtp_attempts,
        curl_retries=curl_retries,
    )


def send_failure_email(
    settings: BidAlertSettings,
    error: Exception,
    *,
    progress: Callable[[str], None] | None = None,
) -> None:
    _require_email_settings(settings)
    assert settings.recipient_email
    assert settings.sender_email

    message = EmailMessage()
    message["From"] = settings.sender_email
    message["To"] = settings.recipient_email
    message["Subject"] = f"{settings.subject_prefix}：扫描失败"
    message.set_content(render_failure_email_body(error))
    _send_message(settings, message, progress=progress)


def send_email_with_curl(settings: BidAlertSettings, message: EmailMessage, *, retries: int = 2) -> None:
    assert settings.recipient_email
    assert settings.sender_email
    assert settings.smtp_host
    assert settings.smtp_username
    assert settings.smtp_password

    scheme = "smtps" if settings.smtp_use_ssl else "smtp"
    url = f"{scheme}://{settings.smtp_host}:{settings.smtp_port}"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as message_file:
        message_file.write(message.as_string())
        message_file.flush()
        config_lines = [
            f"url = {_curl_config_quote(url)}",
            f"user = {_curl_config_quote(f'{settings.smtp_username}:{settings.smtp_password}')}",
            f"mail-from = {_curl_config_quote(settings.sender_email)}",
            f"mail-rcpt = {_curl_config_quote(settings.recipient_email)}",
            f"upload-file = {_curl_config_quote(message_file.name)}",
            "ssl-reqd",
            f"max-time = {settings.request_timeout_seconds}",
            f"retry = {max(0, retries)}",
            "retry-delay = 1",
            "retry-all-errors",
            "silent",
            "show-error",
        ]
        try:
            subprocess.run(
                ["curl", "-K", "-"],
                input="\n".join(config_lines) + "\n",
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise BidAlertFetchError("Failed to send email via curl SMTP") from exc


def run_alerts(
    settings: BidAlertSettings,
    *,
    dry_run: bool = False,
    prime: bool = False,
    progress: Callable[[str], None] | None = None,
) -> tuple[int, int]:
    items = collect_alert_items(settings, progress=progress)
    _emit_progress(progress, f"抓取完成，原始结果 {len(items)} 条，开始筛选...")
    eligible_items = filter_items_by_relevance(items, min_score=settings.min_relevance_score)
    state = load_state(settings.state_file)
    new_items = select_new_items(eligible_items, state)
    email_items = sort_items_by_relevance(new_items)[: settings.max_email_items]

    if prime:
        mark_seen(state, eligible_items)
        save_state(settings.state_file, state)
        return (len(eligible_items), 0)

    if dry_run:
        return (len(items), len(email_items))

    _emit_progress(progress, f"准备发送运行摘要邮件，新增公告 {len(email_items)} 条...")
    send_email(
        settings,
        email_items,
        total_found=len(items),
        eligible_count=len(eligible_items),
        progress=progress,
    )
    if email_items:
        mark_seen(state, email_items)
    elif eligible_items:
        mark_seen(state, eligible_items)
    save_state(settings.state_file, state)
    return (len(items), len(email_items))


def send_test_email(settings: BidAlertSettings, *, progress: Callable[[str], None] | None = None) -> None:
    test_settings = replace(settings, request_timeout_seconds=min(settings.request_timeout_seconds, 8))
    item = AlertItem(
        title="测试：证券公司专家咨询服务供应商招标项目招标公告",
        link="https://example.com/test-bid-alert",
        summary="这是一封本地构造的测试邮件，用于验证新版分组、阶段识别和 SMTP 发信链路。",
        published=datetime.now().strftime("%Y-%m-%d"),
        source="test.local",
        keyword="专家咨询服务供应商招标",
        query="local-test",
    )
    _emit_progress(progress, "准备发送测试邮件...")
    send_email(test_settings, [item], total_found=1, eligible_count=1, progress=progress, smtp_attempts=1, curl_retries=0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Free bid announcement email alerts from public search RSS feeds")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print counts without sending or updating state")
    parser.add_argument("--prime", action="store_true", help="Record current results as seen without sending email")
    parser.add_argument("--test-email", action="store_true", help="Send one local test email without fetching search results")
    parser.add_argument("--verbose", action="store_true", help="Print fetch and email progress to stderr")
    parser.add_argument("--state-file", help="Override BID_ALERT_STATE_FILE")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings: BidAlertSettings | None = None
    progress = None
    try:
        settings = load_settings()
        if args.state_file:
            settings = replace(settings, state_file=Path(args.state_file))
        if args.test_email:
            send_test_email(settings, progress=lambda message: print(message, file=sys.stderr, flush=True))
            print("Sent bid alert test email.")
            return 0
        if args.dry_run or args.verbose:
            progress = lambda message: print(message, file=sys.stderr, flush=True)
        total, new_count = run_alerts(settings, dry_run=args.dry_run, prime=args.prime, progress=progress)
    except (BidAlertConfigError, BidAlertFetchError) as exc:
        print(f"Bid alert error: {exc}", file=sys.stderr)
        if settings is not None and not args.dry_run and not args.prime and not args.test_email:
            try:
                send_failure_email(settings, exc, progress=progress)
                print("Sent bid alert failure email.", file=sys.stderr)
            except (BidAlertConfigError, BidAlertFetchError) as notify_exc:
                print(f"Bid alert failure email error: {notify_exc}", file=sys.stderr)
        return 1

    if args.prime:
        print(f"Primed bid alert state with {total} current results.")
    elif args.dry_run:
        print(f"Fetched {total} results; {new_count} would be emailed.")
    else:
        print(f"Fetched {total} results; emailed {new_count} new results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
