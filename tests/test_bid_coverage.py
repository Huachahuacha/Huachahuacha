from collections import Counter
from dataclasses import replace
from html import escape
import json
from pathlib import Path
from urllib.parse import unquote_plus

import pytest
import requests

from src.bid_alerts import (
    AlertItem,
    BidAlertFetchError,
    BidAlertSettings,
    build_queries,
    collect_alert_items,
    dedupe_items,
    filter_items_by_relevance,
    fetch_page,
    item_content_hash,
    item_id,
    load_state,
    mark_seen,
    parse_galaxy_records,
    parse_public_html_items,
    run_alerts,
    save_state,
    select_new_items,
)
from src.bid_coverage import BROKER_SEARCH_TOPICS, DEFAULT_REGISTRY, build_broker_queries, load_brokers, next_list_page
from src.bid_documents import DocumentResult


@pytest.fixture(autouse=True)
def block_live_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must not make live network or email requests")

    monkeypatch.setattr("src.bid_alerts.requests.get", forbidden)
    monkeypatch.setattr("src.bid_alerts._send_message", forbidden)


def settings_for(tmp_path: Path, **changes) -> BidAlertSettings:
    settings = BidAlertSettings(
        keywords=("专家咨询", "专家网络"),
        site_filters=("search.example",),
        extra_queries=(),
        source_urls=(),
        query_suffix="after:2025-01-01",
        broad_search=True,
        state_file=tmp_path / "state.json",
        search_url_template="https://search.example/rss?q={query}",
        max_results_per_query=20,
        min_relevance_score=5,
        max_email_items=50,
        request_timeout_seconds=3,
        recipient_email=None,
        sender_email=None,
        smtp_host=None,
        smtp_port=587,
        smtp_username=None,
        smtp_password=None,
        smtp_use_ssl=False,
        smtp_use_tls=True,
        subject_prefix="招标公告提醒",
        ctbpsp_mirror_search=False,
    )
    return replace(settings, **changes)


def registry_file(tmp_path: Path) -> Path:
    path = tmp_path / "brokers.json"
    path.write_text(
        json.dumps(
            {
                "brokers": [
                    {"name": "广发证券股份有限公司", "aliases": ["广发证券"], "procurement_urls": []},
                    {"name": "中信证券股份有限公司", "aliases": ["中信证券"], "procurement_urls": []},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def item(title: str = "广发证券专家咨询服务采购公告", *, index: int = 1, body: str = "", summary: str = "") -> AlertItem:
    return AlertItem(title, f"https://broker.example/notices/{index}", summary, "2026-10-29", "broker.example", "专家咨询", "q", body=body)


def rss(items: list[AlertItem]) -> str:
    entries = "".join(
        f"<item><title>{escape(row.title)}</title><link>{escape(row.link)}</link>"
        f"<description>{escape(row.summary)}</description></item>"
        for row in items
    )
    return f"<rss><channel>{entries}</channel></rss>"


def test_official_registry_has_all_150_entries_and_each_gets_every_search_topic() -> None:
    payload = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    brokers = load_brokers(DEFAULT_REGISTRY)
    names = {broker.name for broker in brokers}

    assert len(brokers) == len(names) == payload["provenance"]["entry_count"] == 150
    assert payload["as_of"] == "2026-09"
    assert "www.csrc.gov.cn" in payload["source_url"]
    assert "中国国际金融股份有限公司" in names
    queries = build_broker_queries(brokers, "after:2025-01-01")
    counts = Counter(label for label, _, _ in queries)
    assert {"专家", "投研 咨询", "智库"}.issubset(BROKER_SEARCH_TOPICS)
    for broker in brokers:
        assert counts[f"券商覆盖:{broker.name}"] == len(BROKER_SEARCH_TOPICS) * len(broker.search_names)
        actual_queries = {query for label, _, query in queries if label == f"券商覆盖:{broker.name}"}
        for name in broker.search_names:
            for topic in BROKER_SEARCH_TOPICS:
                assert f'"{name}" {topic} after:2025-01-01' in actual_queries
    assert any('"中国国际金融" 专家 ' in query for _, _, query in queries)


def test_without_registry_existing_query_plan_is_unchanged(tmp_path) -> None:
    settings = settings_for(tmp_path)

    assert build_queries(settings) == [
        ("专家咨询", "search.example", 'site:search.example "专家咨询" after:2025-01-01'),
        ("专家网络", "search.example", 'site:search.example "专家网络" after:2025-01-01'),
        ("专家咨询", "", '"专家咨询" after:2025-01-01'),
        ("专家网络", "", '"专家网络" after:2025-01-01'),
    ]


def test_official_notice_wording_and_body_only_expert_interviews_survive_filtering() -> None:
    guangfa = item("广发证券发展研究中心专家库服务供应商采购项目公告")
    citic = item("中信证券投资2026年度服务采购公告", index=2, body="采购内容：专家访谈服务；采购方式：竞争性磋商。")

    assert set(filter_items_by_relevance([guangfa, citic], min_score=5)) == {guangfa, citic}


def test_broker_research_procurement_is_retained_as_review_candidate(monkeypatch, tmp_path) -> None:
    candidate = item("广发证券外部研究平台供应商征集公告")
    settings = settings_for(tmp_path, broker_registry_file=registry_file(tmp_path))
    monkeypatch.setattr("src.bid_alerts.fetch_feed", lambda *args, **kwargs: rss([candidate]))

    results = collect_alert_items(settings)

    assert len(results) == 1
    assert results[0].review_required
    assert results[0].matched_brokers == ("广发证券股份有限公司",)
    assert filter_items_by_relevance(results, min_score=5) == results


def test_single_query_failure_produces_partial_report_and_per_broker_coverage(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "coverage.json"
    settings = settings_for(tmp_path, broker_registry_file=registry_file(tmp_path), audit_report_file=report_path)

    def fake_fetch(url: str, **kwargs) -> str:
        if '"广发证券" 专家 ' in unquote_plus(url):
            raise BidAlertFetchError("simulated unavailable feed")
        return rss([item()])

    monkeypatch.setattr("src.bid_alerts.fetch_feed", fake_fetch)

    assert collect_alert_items(settings)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["partial"] is True
    assert report["broker_count"] == len(report["brokers"]) == 2
    guangfa = next(row for row in report["brokers"] if row["name"] == "广发证券股份有限公司")
    assert guangfa["planned_queries"] == len(BROKER_SEARCH_TOPICS)
    assert guangfa["successful_queries"] == len(BROKER_SEARCH_TOPICS) - 1
    assert guangfa["source_status"] == "search_only_pending_source_verification"
    assert any(row["status"] == "error" for row in report["queries"])


def test_http_html_response_is_reported_as_feed_error_not_successful_empty_results(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "coverage.json"
    settings = settings_for(tmp_path, audit_report_file=report_path)

    def fake_fetch(url: str, **kwargs) -> str:
        if 'site:search.example "专家咨询"' in unquote_plus(url):
            return "<html><body>Access denied</body></html>"
        return rss([item()])

    monkeypatch.setattr("src.bid_alerts.fetch_feed", fake_fetch)

    collect_alert_items(settings)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failures = [row for row in report["queries"] if row["status"] == "error"]
    assert report["partial"]
    assert len(failures) == 1
    assert "non-feed" in failures[0]["error"]


def test_same_url_body_change_is_new_but_whitespace_change_is_not() -> None:
    original = item(body="报名截止：2026-10-30")
    state = {"seen_ids": {}}
    mark_seen(state, [original])

    assert not select_new_items([original], state)
    assert not select_new_items([replace(original, body="报名截止：2026-10-30  \n")], state)
    changed = replace(original, body="报名截止：2026-11-05")
    assert select_new_items([changed], state) == [changed]


def test_failed_body_read_does_not_downgrade_saved_body_fingerprint(monkeypatch, tmp_path) -> None:
    settings = settings_for(tmp_path)
    original = item(body="专家访谈服务；报名截止2026-10-30")
    state = {"seen_ids": {}}
    mark_seen(state, [original])
    save_state(settings.state_file, state)
    sent = []
    failed_read = replace(original, body="", summary="搜索摘要有波动", review_required=True)
    monkeypatch.setattr("src.bid_alerts.collect_alert_items", lambda *args, **kwargs: [failed_read])
    monkeypatch.setattr("src.bid_alerts.send_email", lambda settings, items, **kwargs: sent.append(items))

    assert run_alerts(settings) == (1, 0)
    saved = load_state(settings.state_file)["seen_ids"][item_id(original)]
    assert saved["content_basis"] == "body"
    assert saved["content_hash"] == item_content_hash(original)
    assert sent == [[]]


def test_legacy_url_only_seen_record_gets_baseline_without_resending(monkeypatch, tmp_path) -> None:
    settings = settings_for(tmp_path)
    original = item(body="专家咨询服务；报名截止2026-10-30")
    state = {"seen_ids": {item_id(original): {"title": original.title, "link": original.link, "first_seen_at": "2026-09-01T00:00:00Z"}}}
    save_state(settings.state_file, state)
    sent = []
    monkeypatch.setattr("src.bid_alerts.collect_alert_items", lambda *args, **kwargs: [original])
    monkeypatch.setattr("src.bid_alerts.send_email", lambda settings, items, **kwargs: sent.append(items))

    assert run_alerts(settings) == (1, 0)
    migrated = load_state(settings.state_file)
    record = migrated["seen_ids"][item_id(original)]
    assert record["content_basis"] == "body"
    assert record["first_seen_at"] == "2026-09-01T00:00:00Z"
    assert record["content_hash"] == item_content_hash(original)
    assert sent == [[]]
    changed = replace(original, body="专家咨询服务；报名截止2026-11-05")
    assert select_new_items([changed], migrated) == [changed]


def test_batches_persist_only_successfully_sent_items_when_second_batch_fails(monkeypatch, tmp_path) -> None:
    settings = settings_for(tmp_path, max_email_items=2)
    items = [item(index=index) for index in range(5)]
    attempts = []
    monkeypatch.setattr("src.bid_alerts.collect_alert_items", lambda *args, **kwargs: items)

    def fake_send(settings, batch, **kwargs):
        attempts.append(list(batch))
        if len(attempts) == 2:
            raise BidAlertFetchError("simulated SMTP failure")

    monkeypatch.setattr("src.bid_alerts.send_email", fake_send)

    with pytest.raises(BidAlertFetchError, match="SMTP failure"):
        run_alerts(settings)

    assert [len(batch) for batch in attempts] == [2, 2]
    saved = load_state(settings.state_file)
    assert set(saved["seen_ids"]) == {item_id(row) for row in attempts[0]}
    assert {item_id(row) for row in select_new_items(items, saved)} == {item_id(row) for row in items[2:]}


def test_document_warning_keeps_broker_research_candidate_and_marks_partial(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "coverage.json"
    candidate = item("广发证券外部研究服务供应商征集公告")
    settings = settings_for(tmp_path, broker_registry_file=registry_file(tmp_path), audit_report_file=report_path, fetch_details=True)
    monkeypatch.setattr("src.bid_alerts.fetch_feed", lambda *args, **kwargs: rss([candidate]))
    monkeypatch.setattr("src.bid_documents.enrich_document", lambda *args, **kwargs: DocumentResult("", warnings=("PDF为扫描件，需要人工复核",)))

    results = collect_alert_items(settings)

    assert len(results) == 1
    assert results[0].review_required
    assert filter_items_by_relevance(results, min_score=5) == results
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["partial"]
    assert report["details"][0]["warnings"]


def test_deferred_detail_is_retried_from_report_when_search_no_longer_returns_it(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "coverage.json"
    settings = settings_for(tmp_path, audit_report_file=report_path, fetch_details=True, max_detail_pages=1)
    items = [item(index=1), item(index=2)]
    phase = {"day": 1}
    requests = []
    monkeypatch.setattr("src.bid_alerts.fetch_feed", lambda *args, **kwargs: rss(items if phase["day"] == 1 else []))

    def fake_enrich(url, title, timeout_seconds):
        requests.append(url)
        return DocumentResult("采购内容为证券研究所专家访谈服务")

    monkeypatch.setattr("src.bid_documents.enrich_document", fake_enrich)

    collect_alert_items(settings)
    first_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert first_report["details_deferred"] == [items[1].link]
    assert first_report["partial"]
    phase["day"] = 2
    second_results = collect_alert_items(settings)
    second_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert requests == [items[0].link, items[1].link]
    assert any(row.link == items[1].link and row.body for row in second_results)
    assert not second_report["details_deferred"]


def test_failed_email_batch_is_delivered_next_run_even_if_search_returns_nothing(monkeypatch, tmp_path) -> None:
    settings = settings_for(tmp_path, max_email_items=2)
    items = [item(index=index, body=f"专家访谈服务采购，项目序号{index}") for index in range(5)]
    attempts = []
    monkeypatch.setattr("src.bid_alerts.collect_alert_items", lambda *args, **kwargs: items)

    def fail_second_batch(settings, batch, **kwargs):
        attempts.append(list(batch))
        if len(attempts) == 2:
            raise BidAlertFetchError("simulated SMTP failure")

    monkeypatch.setattr("src.bid_alerts.send_email", fail_second_batch)
    with pytest.raises(BidAlertFetchError, match="SMTP failure"):
        run_alerts(settings)

    saved = load_state(settings.state_file)
    pending = saved["pending_delivery"]
    assert len(pending) == 3
    assert {row["link"] for row in pending} == {row.link for row in items[2:]}
    assert all(row["body"] for row in pending)
    delivered_next_run = []
    monkeypatch.setattr("src.bid_alerts.collect_alert_items", lambda *args, **kwargs: [])
    monkeypatch.setattr("src.bid_alerts.send_email", lambda settings, batch, **kwargs: delivered_next_run.append(list(batch)))

    assert run_alerts(settings)[1] == 3
    assert [len(batch) for batch in delivered_next_run] == [2, 1]
    assert {row for batch in delivered_next_run for row in batch} == set(items[2:])
    final_state = load_state(settings.state_file)
    assert not final_state["pending_delivery"]
    assert set(final_state["seen_ids"]) == {item_id(row) for row in items}


def test_bounded_source_pagination_resumes_next_day_and_rechecks_first_page(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "coverage.json"
    first_url = "https://broker.example/list"
    second_url = first_url + "?page=2"
    settings = settings_for(tmp_path, audit_report_file=report_path, source_urls=(first_url,), source_max_pages=1)
    page_calls = []
    pages = {
        first_url: '<main><a href="/notices/1">广发证券专家咨询服务采购公告</a><a href="?page=2" rel="next">下一页</a></main>',
        second_url: '<main><a href="/notices/2">中信证券专家访谈服务供应商采购公告</a></main>',
    }

    def fake_page(url, **kwargs):
        page_calls.append(url)
        return pages[url]

    monkeypatch.setattr("src.bid_alerts.fetch_feed", lambda *args, **kwargs: rss([]))
    monkeypatch.setattr("src.bid_alerts.fetch_page", fake_page)

    collect_alert_items(settings)
    first_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert first_report["list_pages_deferred"] == [second_url]
    assert first_report["partial"]
    second_results = collect_alert_items(settings)
    second_report = json.loads(report_path.read_text(encoding="utf-8"))

    assert page_calls == [first_url, first_url, second_url]
    assert any(row.link == "https://broker.example/notices/2" for row in second_results)
    assert not second_report["list_pages_deferred"]


def test_selected_but_failed_document_is_retried_after_disappearing_from_search(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "coverage.json"
    settings = settings_for(tmp_path, audit_report_file=report_path, fetch_details=True)
    candidate = item()
    phase = {"day": 1}
    document_calls = []
    monkeypatch.setattr("src.bid_alerts.fetch_feed", lambda *args, **kwargs: rss([candidate] if phase["day"] == 1 else []))

    def fake_document(url, title, timeout_seconds):
        document_calls.append(url)
        if phase["day"] == 1:
            return DocumentResult("", warnings=("源站暂时无法访问",))
        return DocumentResult("证券研究所专家访谈服务采购，报名截止2026-11-05")

    monkeypatch.setattr("src.bid_documents.enrich_document", fake_document)

    first_results = collect_alert_items(settings)
    first_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert first_results[0].review_required
    assert first_report["details_deferred"] == [candidate.link]
    assert first_report["document_retry_counts"][candidate.link] == 1
    phase["day"] = 2
    second_results = collect_alert_items(settings)
    second_report = json.loads(report_path.read_text(encoding="utf-8"))

    assert document_calls == [candidate.link, candidate.link]
    assert len(second_results) == 1
    assert "报名截止2026-11-05" in second_results[0].body
    assert not second_report["details_deferred"]
    assert candidate.link not in second_report["document_retry_counts"]


def test_generic_broker_procurement_with_failed_body_is_retained_for_review(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "coverage.json"
    candidate = item("中信证券年度服务采购公告")
    settings = settings_for(tmp_path, broker_registry_file=registry_file(tmp_path), audit_report_file=report_path, fetch_details=True)
    monkeypatch.setattr("src.bid_alerts.fetch_feed", lambda *args, **kwargs: rss([candidate]))
    monkeypatch.setattr("src.bid_documents.enrich_document", lambda *args, **kwargs: DocumentResult("", warnings=("正文无法读取",)))

    results = collect_alert_items(settings)

    assert len(results) == 1
    assert results[0].matched_brokers == ("中信证券股份有限公司",)
    assert results[0].review_required
    assert filter_items_by_relevance(results, min_score=5) == results
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["partial"]
    assert report["details_deferred"] == [candidate.link]


def test_first_successful_body_after_summary_baseline_is_an_update(monkeypatch, tmp_path) -> None:
    settings = settings_for(tmp_path)
    summary_only = item(summary="专家咨询服务采购，详情待获取")
    state = {"seen_ids": {}}
    mark_seen(state, [summary_only])
    save_state(settings.state_file, state)
    with_body = replace(summary_only, body="采购专家访谈服务，报名截止2026-11-05，服务期一年。")
    sent = []
    monkeypatch.setattr("src.bid_alerts.collect_alert_items", lambda *args, **kwargs: [with_body])
    monkeypatch.setattr("src.bid_alerts.send_email", lambda settings, batch, **kwargs: sent.extend(batch))

    assert select_new_items([with_body], state) == [with_body]
    assert run_alerts(settings) == (1, 1)
    assert sent == [with_body]
    saved = load_state(settings.state_file)
    assert saved["seen_ids"][item_id(with_body)]["content_basis"] == "body"
    assert not select_new_items([with_body], saved)


def test_galaxy_records_on_one_list_url_keep_distinct_ids() -> None:
    source_url = "https://www.chinastock.com.cn/website2020/announcement/queryPurchaseReqList?pageNo=1"
    payload = {
        "ret": {"error": 0},
        "data": {
            "pages": 2,
            "list": [
                {"id": "record-1", "projectName": "专家访谈服务供应商采购", "projectNo": "CG-2026-01", "showTime": "2026-10-28"},
                {"id": "record-2", "projectName": "专家咨询服务补充采购", "projectNo": "CG-2026-02", "showTime": "2026-10-29"},
            ],
        },
    }

    records, next_page = parse_galaxy_records(json.dumps(payload, ensure_ascii=False), source_url)

    assert len(records) == 2
    assert records[0].link == records[1].link
    assert records[0].record_id != records[1].record_id
    assert item_id(records[0]) != item_id(records[1])
    assert dedupe_items(records) == records
    state = {"seen_ids": {}}
    mark_seen(state, [records[0]])
    assert select_new_items(records, state) == [records[1]]
    assert next_page == source_url.replace("pageNo=1", "pageNo=2")


def test_unquoted_postal_announcement_link_is_parsed() -> None:
    source_url = "https://www.chinapost.com.cn/cn/zt1/index.html"
    page = "<li><a href=/cn/report/2401/50455-1.htm target=_blank>中邮证券2024年专家咨询采购项目招标公告</a></li>"

    records = parse_public_html_items(page, source_url=source_url)

    assert len(records) == 1
    assert records[0].title == "中邮证券2024年专家咨询采购项目招标公告"
    assert records[0].link == "https://www.chinapost.com.cn/cn/report/2401/50455-1.htm"


@pytest.mark.parametrize("meta, encoding", [("<meta charset=gb2312>", "gb2312"), ("", "utf-8")])
def test_chinese_html_without_http_charset_does_not_keep_requests_latin1_default(monkeypatch, meta, encoding) -> None:
    title = "中邮证券2024年专家咨询采购项目招标公告"
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "text/html"
    response.encoding = "ISO-8859-1"
    response._content = f"<html><head>{meta}</head><body><a href=/cn/report/2401/50455-1.htm>{title}</a></body></html>".encode(encoding)
    # A declared GB2312 meta must win even when the fallback detector says UTF-8.
    monkeypatch.setattr(requests.Response, "apparent_encoding", property(lambda self: "utf-8"))
    monkeypatch.setattr("src.bid_alerts.requests.get", lambda *args, **kwargs: response)

    html_text = fetch_page("https://www.chinapost.com.cn/cn/zt1/index.html", timeout_seconds=3)
    records = parse_public_html_items(html_text, source_url="https://www.chinapost.com.cn/cn/zt1/index.html")

    assert title in html_text
    assert response.encoding == encoding
    assert records[0].title == title


@pytest.mark.parametrize(
    "link",
    [
        '<a href="page2.html" title="下一页 >"></a>',
        '<a href="page2.html"><img src="next.png" alt="下一页 >"></a>',
    ],
)
def test_next_page_is_recognized_from_title_or_image_alt_with_arrow(link) -> None:
    assert next_list_page(link, "https://broker.example/notices/index.html") == "https://broker.example/notices/page2.html"


def test_exhausted_scan_budget_defers_queries_without_network_or_false_success(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "coverage.json"
    settings = settings_for(tmp_path, broker_registry_file=registry_file(tmp_path), audit_report_file=report_path, scan_budget_seconds=1)
    expected_queries = build_queries(settings)
    calls = []
    clock_calls = {"count": 0}

    def fake_clock():
        clock_calls["count"] += 1
        return 0 if clock_calls["count"] == 1 else 2

    def forbidden_feed(*args, **kwargs):
        calls.append(args)
        raise AssertionError("The elapsed scan budget must be checked before network requests")

    monkeypatch.setattr("src.bid_alerts.time.monotonic", fake_clock)
    monkeypatch.setattr("src.bid_alerts.fetch_feed", forbidden_feed)

    assert collect_alert_items(settings) == []
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert not calls
    assert report["partial"]
    assert len(report["queries"]) == len(expected_queries)
    assert all(row["status"] == "deferred" for row in report["queries"])
    assert {row["query"] for row in report["queries"]} == {query for _, _, query in expected_queries}
    assert all(row["planned_queries"] == len(BROKER_SEARCH_TOPICS) and row["successful_queries"] == 0 for row in report["brokers"])


def test_official_sources_run_before_search_and_leave_time_for_documents(monkeypatch, tmp_path) -> None:
    report_path = tmp_path / "coverage.json"
    settings = settings_for(tmp_path, source_urls=("https://broker.example/list",), audit_report_file=report_path, fetch_details=True, scan_budget_seconds=100)
    clock = {"now": 0}
    events = []

    def source(url, *, timeout_seconds):
        events.append("source")
        clock["now"] = 50
        return '<a href="/notice/1">广发证券专家咨询服务采购公告</a>'

    def search(url, *, timeout_seconds):
        events.append("search")
        clock["now"] = 80
        return rss([])

    def document(url, title, timeout):
        events.append("document")
        return DocumentResult("广发证券采购专家访谈服务")

    monkeypatch.setattr("src.bid_alerts.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("src.bid_alerts.fetch_page", source)
    monkeypatch.setattr("src.bid_alerts.fetch_feed", search)
    monkeypatch.setattr("src.bid_documents.enrich_document", document)

    assert collect_alert_items(settings)
    assert events == ["source", "search", "document"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["sources"][0]["status"] == "ok"
    assert sum(row["status"] == "deferred" for row in report["queries"]) == 3
    assert report["details"][0]["text_characters"] > 0
