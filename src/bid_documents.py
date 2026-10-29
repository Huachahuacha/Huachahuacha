from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import re
import socket
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import requests


MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 100_000
MAX_ATTACHMENTS = 10
MAX_PDF_PAGES = 200
USER_AGENT = "Mozilla/5.0 (compatible; bid-alerts/0.1; +public-document-monitor)"
ATTACHMENT_PATTERN = re.compile(r"\.(pdf|docx|doc|xlsx|xls|zip)(?:$|[?#&])", re.IGNORECASE)
DOCUMENT_MIME_TYPES = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
}


@dataclass(frozen=True)
class DocumentResult:
    text: str
    attachments: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class DocumentReadError(ValueError):
    pass


class _AnonymousAuth(requests.auth.AuthBase):
    def __call__(self, request):
        request.headers.pop("Authorization", None)
        request.headers.pop("Cookie", None)
        return request


def _public_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        if "." not in host:
            return False
    try:
        addresses = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(address[4][0]).is_global for address in addresses)
    except (OSError, ValueError):
        return False


def _label(url: str) -> str:
    try:
        parts = urlsplit(url)
        return f"{parts.hostname or '无效地址'}{parts.path}"
    except ValueError:
        return "无效地址"


def _error_reason(error: Exception) -> str:
    return str(error) if isinstance(error, DocumentReadError) else type(error).__name__


def _fetch(url: str, timeout_seconds: int) -> tuple[bytes, str, str]:
    current = url
    for _ in range(6):
        if not _public_url(current):
            raise DocumentReadError("仅支持公开HTTP(S)地址")
        response = requests.get(
            current,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout_seconds,
            stream=True,
            allow_redirects=False,
            auth=_AnonymousAuth(),
        )
        try:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise DocumentReadError("重定向缺少目标地址")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            size = 0
            chunks: list[bytes] = []
            for chunk in response.iter_content(chunk_size=64 * 1024):
                size += len(chunk)
                if size > MAX_DOCUMENT_BYTES:
                    raise DocumentReadError("文档超过10MB限制")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("Content-Type", ""), current
        finally:
            response.close()
    raise DocumentReadError("重定向次数超过限制")


def _decode(data: bytes, content_type: str) -> str:
    declared = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.IGNORECASE)
    meta = re.search(rb"charset\s*=\s*[\"']?([\w-]+)", data[:4096], re.IGNORECASE)
    encodings = [declared.group(1) if declared else "", meta.group(1).decode("ascii") if meta else "", "utf-8", "gb18030"]
    for encoding in encodings:
        if not encoding:
            continue
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


class _NoticeHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: dict[str, list[str]] = {key: [] for key in ("article", "main", "body", "fallback")}
        self.depth = {key: 0 for key in ("article", "main", "body")}
        self.ignored = 0
        self.links: list[tuple[str, str]] = []
        self.anchor: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.depth:
            self.depth[tag] += 1
        if tag in {"script", "style", "header", "footer", "nav", "noscript"}:
            self.ignored += 1
        if tag == "a" and not self.ignored:
            values = dict(attrs)
            self.anchor = (values.get("href") or "", [values.get("title") or ""])
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.handle_data("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor is not None:
            self.links.append((self.anchor[0], " ".join(self.anchor[1]).strip()))
            self.anchor = None
        if tag in self.depth:
            self.depth[tag] = max(0, self.depth[tag] - 1)
        if tag in {"script", "style", "header", "footer", "nav", "noscript"}:
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, data: str) -> None:
        if self.ignored:
            return
        self.sections["fallback"].append(data)
        for key, depth in self.depth.items():
            if depth:
                self.sections[key].append(data)
        if self.anchor is not None:
            self.anchor[1].append(data)

    def notice_text(self) -> str:
        for key in ("article", "main", "body", "fallback"):
            text = " ".join(self.sections[key]).strip()
            if text:
                return re.sub(r"\s+", " ", text)
        return ""


def _extension(url: str) -> str:
    match = ATTACHMENT_PATTERN.search(unquote(url))
    return match.group(1).lower() if match else ""


def _binary_text(data: bytes, content_type: str, url: str) -> tuple[str, list[str]]:
    content_type = content_type.split(";", 1)[0].strip().lower()
    extension = _extension(url) or DOCUMENT_MIME_TYPES.get(content_type, "")
    if data.startswith(b"%PDF-") or "application/pdf" in content_type or extension == "pdf":
        try:
            from pypdf import PdfReader
            from pypdf.errors import PyPdfError
        except ImportError:
            return "", ["未安装PDF解析依赖pypdf，需人工核验"]

        try:
            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                return "", ["PDF已加密，未提取正文"]
            pages: list[str] = []
            warnings = ["PDF超过200页，正文提取已截断"] if len(reader.pages) > MAX_PDF_PAGES else []
            for page in reader.pages[:MAX_PDF_PAGES]:
                pages.append(page.extract_text() or "")
                if sum(map(len, pages)) > MAX_TEXT_CHARS:
                    warnings.append("PDF正文超过100000字符，已截断")
                    break
            text = "\n".join(pages).strip()
            return text[:MAX_TEXT_CHARS], warnings or ([] if text else ["PDF未提取到文字，可能为扫描件，需人工核验"])
        except (PyPdfError, OSError, ValueError, RuntimeError, TypeError) as exc:
            return "", [f"PDF正文提取失败（{type(exc).__name__}），需人工核验"]
    if extension == "docx" or "wordprocessingml" in content_type or (data.startswith(b"PK") and not extension):
        try:
            with ZipFile(BytesIO(data)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > MAX_DOCUMENT_BYTES:
                    raise DocumentReadError("DOCX解压正文超过10MB限制")
                root = ElementTree.fromstring(archive.read(info))
            return " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t")), []
        except (BadZipFile, KeyError, ElementTree.ParseError, DocumentReadError, OSError, RuntimeError) as exc:
            return "", [f"DOCX正文提取失败（{type(exc).__name__}），需人工核验"]
    return "", [f"不支持直接提取此格式：{extension or content_type or '未知格式'}，需人工核验"]


def _attachment_priority(candidate: tuple[str, str], title: str) -> int:
    url, label = candidate
    text = unquote(f"{url} {label}")
    return sum(term in text for term in ("专家", "采购", "招标", "公告", "正文", "附件")) + int(bool(title and title in label))


def enrich_document(url: str, title: str, timeout_seconds: int) -> DocumentResult:
    """Read a public notice and its explicitly linked PDF/DOCX attachments without crawling further."""
    try:
        data, content_type, final_url = _fetch(url, timeout_seconds)
    except (requests.RequestException, DocumentReadError, ValueError) as exc:
        return DocumentResult("", warnings=(f"公告读取失败：{_label(url)}（{_error_reason(exc)}）",))
    mime_type = content_type.split(";", 1)[0].strip().lower()
    if _extension(final_url) or mime_type in DOCUMENT_MIME_TYPES or data.startswith((b"%PDF-", b"PK")):
        text, warnings = _binary_text(data, content_type, final_url)
        if len(text) > MAX_TEXT_CHARS:
            warnings.append("正文超过100000字符，已截断")
        return DocumentResult(text[:MAX_TEXT_CHARS], warnings=tuple(warnings))
    if "text/plain" in content_type:
        text = _decode(data, content_type)
        warnings = ("正文超过100000字符，已截断",) if len(text) > MAX_TEXT_CHARS else ()
        return DocumentResult(text[:MAX_TEXT_CHARS], warnings=warnings)

    parser = _NoticeHTML()
    try:
        parser.feed(_decode(data, content_type))
    except (ValueError, AssertionError) as exc:
        return DocumentResult("", warnings=(f"HTML正文解析失败（{type(exc).__name__}），需人工核验",))
    texts = [parser.notice_text()]
    warnings: list[str] = []
    candidates: dict[str, str] = {}
    for href, label in parser.links:
        attachment_url = urljoin(final_url, href)
        parts = urlsplit(attachment_url)
        attachment_url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        if _extension(attachment_url):
            if _public_url(attachment_url):
                candidates.setdefault(attachment_url, label)
            else:
                warnings.append(f"非公开或含凭据的附件地址未抓取：{_label(attachment_url)}，需人工核验")
    allowed: list[tuple[str, str]] = []
    for attachment_url, label in candidates.items():
        if _extension(attachment_url) not in {"pdf", "docx"}:
            warnings.append(f"附件格式暂不支持：{_label(attachment_url)}，需人工核验")
        else:
            allowed.append((attachment_url, label))
    allowed.sort(key=lambda candidate: _attachment_priority(candidate, title), reverse=True)
    if len(allowed) > MAX_ATTACHMENTS:
        warnings.append(f"可读取附件共{len(allowed)}个，仅提取前{MAX_ATTACHMENTS}个，余下需人工核验")
    for attachment_url, _ in allowed[:MAX_ATTACHMENTS]:
        try:
            attachment_data, attachment_type, redirected_url = _fetch(attachment_url, timeout_seconds)
            text, attachment_warnings = _binary_text(attachment_data, attachment_type, redirected_url)
            texts.append(text)
            warnings.extend(f"{_label(attachment_url)}：{warning}" for warning in attachment_warnings)
        except (requests.RequestException, DocumentReadError, ValueError) as exc:
            warnings.append(f"附件读取失败：{_label(attachment_url)}（{_error_reason(exc)}），需人工核验")
    text = "\n".join(texts).strip()
    if len(text) > MAX_TEXT_CHARS:
        warnings.append("正文超过100000字符，已截断")
    if not text:
        warnings.append("未读取到公告正文，可能需要动态加载或登录，需人工核验")
    return DocumentResult(text[:MAX_TEXT_CHARS], tuple(candidates), tuple(warnings))
