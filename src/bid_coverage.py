from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit


DEFAULT_REGISTRY = Path(__file__).resolve().parents[1] / "data/bid-alerts/brokers.json"
BROKER_SEARCH_TOPICS = ("专家", "访谈", "投研 咨询", "研究 咨询", "智库", "顾问 服务")
RESEARCH_TERMS = ("专家", "访谈", "智库", "投研", "研究咨询", "外部研究", "调研咨询", "顾问服务")
PROCUREMENT_TERMS = ("采购", "招标", "供应商", "征集", "选聘", "磋商", "谈判", "入围", "成交", "中标", "询价", "比选")


@dataclass(frozen=True)
class Broker:
    name: str
    aliases: tuple[str, ...] = ()
    procurement_urls: tuple[str, ...] = ()

    @property
    def search_names(self) -> tuple[str, ...]:
        short_name = self.name
        for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
            if short_name.endswith(suffix):
                short_name = short_name[:-len(suffix)]
                break
        return tuple(dict.fromkeys((short_name, *self.aliases)))


def load_brokers(path: Path | None) -> tuple[Broker, ...]:
    if path is None:
        return ()
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("brokers") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"Broker registry has no brokers: {path}")
    brokers = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"].strip():
            raise ValueError("Invalid broker registry entry")
        name = row["name"].strip()
        if name in seen:
            raise ValueError(f"Duplicate broker in registry: {name}")
        seen.add(name)
        aliases = row.get("aliases", [])
        urls = row.get("procurement_urls", [])
        if not isinstance(aliases, list) or not all(isinstance(value, str) and value.strip() for value in aliases):
            raise ValueError(f"Invalid aliases for {name}")
        if not isinstance(urls, list) or not all(isinstance(value, str) and urlsplit(value).scheme in {"http", "https"} for value in urls):
            raise ValueError(f"Invalid procurement URLs for {name}")
        brokers.append(Broker(name, tuple(aliases), tuple(urls)))
    return tuple(brokers)


def build_broker_queries(brokers: tuple[Broker, ...], suffix: str) -> list[tuple[str, str, str]]:
    queries = []
    for broker in brokers:
        for name in broker.search_names:
            safe_name = name.replace('"', " ").strip()
            for topic in BROKER_SEARCH_TOPICS:
                query = f'"{safe_name}" {topic}'
                if suffix:
                    query += f" {suffix.strip()}"
                queries.append((f"券商覆盖:{broker.name}", "", query))
    return list(dict.fromkeys(queries))


def matching_brokers(text: str, brokers: tuple[Broker, ...]) -> tuple[str, ...]:
    compact = "".join(text.split())
    return tuple(broker.name for broker in brokers if any(len(name) >= 3 and "".join(name.split()) in compact for name in (broker.name, *broker.search_names)))


def is_research_procurement(text: str) -> bool:
    return any(term in text for term in RESEARCH_TERMS) and any(term in text for term in PROCUREMENT_TERMS)


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current: tuple[str, str, list[str]] | None = None
        self.links: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            values = dict(attrs)
            self.current = (values.get("href") or "", values.get("title") or "", [])

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current[2].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current is not None:
            href, title, text = self.current
            self.links.append((href, title, " ".join(text).strip()))
            self.current = None


def page_links(page_html: str) -> list[tuple[str, str, str]]:
    parser = _AnchorParser()
    parser.feed(page_html)
    return parser.links


class _NextPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href = ""
        self.rel_next = False
        self.text: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            values = dict(attrs)
            self.href = values.get("href") or ""
            self.rel_next = "next" in (values.get("rel") or "").split()
            self.text = [values.get("title") or ""]
        elif tag == "img" and self.href:
            values = dict(attrs)
            self.text.append(values.get("alt") or values.get("title") or "")

    def handle_data(self, data: str) -> None:
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href:
            label = "".join(self.text).strip()
            if self.rel_next or re.search(r"下一页|下页|下一頁|\bnext\b", label, re.IGNORECASE) or label in {">", "›"}:
                self.links.append(self.href)
            self.href = ""


def next_list_page(page_html: str, source_url: str) -> str | None:
    parser = _NextPageParser()
    parser.feed(page_html)
    for href in parser.links:
        url = urljoin(source_url, href)
        if urlsplit(url).scheme in {"http", "https"} and urlsplit(url).netloc == urlsplit(source_url).netloc and url != source_url:
            return url
    return None
