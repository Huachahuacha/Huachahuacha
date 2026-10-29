from io import BytesIO
from zipfile import ZipFile

import pytest
import requests

from src.bid_documents import MAX_DOCUMENT_BYTES, MAX_TEXT_CHARS, enrich_document


@pytest.fixture(autouse=True)
def public_dns_without_network(monkeypatch):
    monkeypatch.setattr("src.bid_documents.socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))])


class FakeResponse:
    def __init__(self, data: bytes, content_type: str, *, status: int = 200, location: str = "") -> None:
        self.data = data
        self.headers = {"Content-Type": content_type, "Location": location}
        self.status_code = status
        self.closed = False

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.data), chunk_size):
            yield self.data[offset : offset + chunk_size]

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError("unavailable")

    def close(self) -> None:
        self.closed = True


def mock_pages(monkeypatch, pages):
    calls = []

    def fake_get(url, *, headers, timeout, stream, allow_redirects, auth):
        assert stream is True
        assert allow_redirects is False
        assert timeout == 3
        assert isinstance(auth, requests.auth.AuthBase)
        calls.append(url)
        response = pages[url]
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("src.bid_documents.requests.get", fake_get)
    return calls


def docx_bytes(text: str) -> bytes:
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return data.getvalue()


def pdf_bytes(*, with_text: bool = False) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    if with_text:
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 10 300 Td (Expert interviews procurement) Tj ET")
        page[NameObject("/Contents")] = stream
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def test_notice_body_and_explicit_cross_host_attachments_are_read_without_navigation_noise(monkeypatch) -> None:
    page_url = "https://broker.example/notice/1"
    pages = {
        page_url: FakeResponse(
            b'<body><header>header noise</header><nav>nav noise</nav><main>main extra'
            b'<article><h1>Procurement</h1><p>External research</p>'
            b'<script>script noise</script><style>style noise</style>'
            b'<a href="/files/spec.docx">Expert specification</a>'
            b'<a href="https://cdn.example/external.pdf">External</a>'
            b'<a href="/files/old.doc">Old format</a></article></main>'
            b'<footer>footer noise</footer></body>',
            "text/html; charset=utf-8",
        ),
        "https://broker.example/files/spec.docx": FakeResponse(
            docx_bytes("采购内容：证券研究所专家访谈及外部专家顾问服务"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        "https://cdn.example/external.pdf": FakeResponse(pdf_bytes(with_text=True), "application/pdf"),
    }
    calls = mock_pages(monkeypatch, pages)

    result = enrich_document(page_url, "Procurement", 3)

    assert "External research" in result.text
    assert "专家访谈" in result.text
    assert "Expert interviews procurement" in result.text
    assert all(noise not in result.text for noise in ("header noise", "nav noise", "script noise", "style noise", "footer noise", "main extra"))
    assert set(result.attachments) == {
        "https://broker.example/files/spec.docx",
        "https://cdn.example/external.pdf",
        "https://broker.example/files/old.doc",
    }
    assert calls == [page_url, "https://broker.example/files/spec.docx", "https://cdn.example/external.pdf"]
    assert any("格式暂不支持" in warning for warning in result.warnings)


def test_pdf_text_is_extracted_and_blank_pdf_is_reported_for_review(monkeypatch) -> None:
    mock_pages(
        monkeypatch,
        {
            "https://broker.example/text.pdf": FakeResponse(pdf_bytes(with_text=True), "application/pdf"),
            "https://broker.example/scan.pdf": FakeResponse(pdf_bytes(), "application/pdf"),
        },
    )

    text_result = enrich_document("https://broker.example/text.pdf", "Expert notice", 3)
    scan_result = enrich_document("https://broker.example/scan.pdf", "Scanned notice", 3)

    assert "Expert interviews procurement" in text_result.text
    assert not text_result.warnings
    assert not scan_result.text
    assert any("可能为扫描件" in warning for warning in scan_result.warnings)


def test_attachment_failure_preserves_body_and_reports_uncertainty(monkeypatch) -> None:
    page_url = "https://broker.example/notice"
    mock_pages(
        monkeypatch,
        {
            page_url: FakeResponse(b'<main>Expert notice<a href="/missing.pdf">PDF</a></main>', "text/html"),
            "https://broker.example/missing.pdf": requests.Timeout("timed out"),
        },
    )

    result = enrich_document(page_url, "Expert notice", 3)

    assert "Expert notice" in result.text
    assert any("附件读取失败" in warning for warning in result.warnings)


def test_document_and_text_limits_are_explicit(monkeypatch) -> None:
    oversized = FakeResponse(b"x" * (MAX_DOCUMENT_BYTES + 1), "text/html")
    mock_pages(
        monkeypatch,
        {
            "https://broker.example/large": oversized,
            "https://broker.example/long": FakeResponse(b"x" * (MAX_TEXT_CHARS + 1), "text/plain"),
        },
    )

    large_result = enrich_document("https://broker.example/large", "Large", 3)
    text_result = enrich_document("https://broker.example/long", "Long", 3)

    assert not large_result.text
    assert large_result.warnings
    assert oversized.closed
    assert len(text_result.text) == MAX_TEXT_CHARS
    assert any("已截断" in warning for warning in text_result.warnings)


def test_attachment_cap_does_not_silently_discard_remaining_links(monkeypatch) -> None:
    page_url = "https://broker.example/notice"
    body = "<main>Procurement" + "".join(f'<a href="/files/{n}.docx">附件{n}</a>' for n in range(12)) + "</main>"
    pages = {page_url: FakeResponse(body.encode(), "text/html")}
    pages.update({f"https://broker.example/files/{n}.docx": FakeResponse(docx_bytes(f"document {n}"), "application/octet-stream") for n in range(12)})
    calls = mock_pages(monkeypatch, pages)

    result = enrich_document(page_url, "Procurement", 3)

    assert len(calls) == 11
    assert len(result.attachments) == 12
    assert any("仅提取前10个" in warning for warning in result.warnings)


def test_private_url_and_redirect_are_rejected_before_private_request(monkeypatch) -> None:
    page_url = "https://broker.example/redirect"
    calls = mock_pages(monkeypatch, {page_url: FakeResponse(b"", "text/html", status=302, location="http://127.0.0.1/private")})

    direct_result = enrich_document("http://127.0.0.1/private", "Private", 3)
    redirect_result = enrich_document(page_url, "Redirect", 3)

    assert calls == [page_url]
    assert direct_result.warnings
    assert redirect_result.warnings


def test_extensionless_docx_and_unsupported_zip_are_distinguished(monkeypatch) -> None:
    mock_pages(
        monkeypatch,
        {
            "https://broker.example/download": FakeResponse(docx_bytes("行业专家访谈服务"), "application/octet-stream"),
            "https://broker.example/archive.zip": FakeResponse(docx_bytes("not a supported archive"), "application/zip"),
            "https://broker.example/legacy": FakeResponse(b"legacy Word", "application/msword"),
        },
    )

    docx_result = enrich_document("https://broker.example/download", "专家服务", 3)
    zip_result = enrich_document("https://broker.example/archive.zip", "附件", 3)
    legacy_result = enrich_document("https://broker.example/legacy", "旧版Word", 3)

    assert "行业专家访谈服务" in docx_result.text
    assert not docx_result.warnings
    assert not zip_result.text
    assert any("不支持直接提取此格式：zip" in warning for warning in zip_result.warnings)
    assert any("不支持直接提取此格式：doc" in warning for warning in legacy_result.warnings)


def test_explicit_attachment_can_redirect_to_public_cdn(monkeypatch) -> None:
    page_url = "https://broker.example/notice"
    calls = mock_pages(
        monkeypatch,
        {
            page_url: FakeResponse(b'<article>Notice<a href="/file.pdf">PDF</a></article>', "text/html"),
            "https://broker.example/file.pdf": FakeResponse(b"", "application/pdf", status=302, location="https://elsewhere.example/file.pdf"),
            "https://elsewhere.example/file.pdf": FakeResponse(pdf_bytes(with_text=True), "application/pdf"),
        },
    )

    result = enrich_document(page_url, "Notice", 3)

    assert calls == [page_url, "https://broker.example/file.pdf", "https://elsewhere.example/file.pdf"]
    assert "Expert interviews procurement" in result.text
    assert not result.warnings


def test_private_dns_private_ip_and_credentialed_attachments_are_not_requested(monkeypatch) -> None:
    page_url = "https://broker.example/notice"
    monkeypatch.setattr(
        "src.bid_documents.socket.getaddrinfo",
        lambda host, *args, **kwargs: [(2, 1, 6, "", ("10.0.0.1" if host == "private.example" else "93.184.216.34", 443))],
    )
    calls = mock_pages(
        monkeypatch,
        {
            page_url: FakeResponse(
                b'<article>Notice<a href="https://name:secret@cdn.example/file.pdf">Credentials</a>'
                b'<a href="http://10.0.0.2/private.pdf">Private IP</a>'
                b'<a href="https://private.example/file.pdf">Private DNS</a></article>',
                "text/html",
            )
        },
    )

    result = enrich_document(page_url, "Notice", 3)

    assert calls == [page_url]
    assert not result.attachments
    assert len(result.warnings) == 3
    assert "secret" not in str(result)


def test_public_requests_do_not_use_netrc_authentication(monkeypatch) -> None:
    sent = []

    def forbidden_netrc(*args, **kwargs):
        raise AssertionError("Public notice requests must not load authentication")

    def fake_send(self, prepared, **kwargs):
        sent.append(prepared)
        return FakeResponse(b"<article>Public notice</article>", "text/html")

    monkeypatch.setattr("requests.sessions.get_netrc_auth", forbidden_netrc)
    monkeypatch.setattr("requests.sessions.Session.send", fake_send)

    result = enrich_document("https://broker.example/notice", "Notice", 3)

    assert result.text == "Public notice"
    assert len(sent) == 1
    assert "Authorization" not in sent[0].headers
    assert "Cookie" not in sent[0].headers
