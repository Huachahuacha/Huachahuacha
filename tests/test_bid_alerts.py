from dataclasses import replace
from pathlib import Path

import requests

from src.bid_alerts import (
    AlertItem,
    BidAlertFetchError,
    BidAlertSettings,
    DEFAULT_KEYWORDS,
    DEFAULT_SITE_FILTERS,
    DEFAULT_SOURCE_URLS,
    build_ctbpsp_mirror_queries,
    build_queries,
    canonical_link,
    collect_alert_items,
    dedupe_items,
    enrich_national_platform_items,
    extract_project_no,
    fetch_feed,
    filter_items_by_relevance,
    group_items_by_project,
    item_id,
    load_dotenv_values,
    load_state,
    main,
    mark_seen,
    normalize_project_title,
    notice_stage,
    parse_attachment_items,
    parse_public_html_items,
    parse_feed,
    priority_bucket,
    project_key,
    relevance_label,
    render_email_body,
    render_failure_email_body,
    run_alerts,
    send_email,
    send_failure_email,
    send_test_email,
    source_display_name,
    sort_items_by_relevance,
    save_state,
    select_new_items,
)


def _settings(tmp_path: Path) -> BidAlertSettings:
    return BidAlertSettings(
        keywords=("专家咨询", "专家网络"),
        site_filters=("example.com",),
        extra_queries=(),
        source_urls=(),
        query_suffix="after:2026-01-01 before:2027-01-01",
        broad_search=True,
        state_file=tmp_path / "state.json",
        search_url_template="https://search.example/rss?q={query}",
        max_results_per_query=10,
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
    )


def test_build_queries_separates_keywords_and_sites(tmp_path) -> None:
    queries = build_queries(_settings(tmp_path))

    assert queries == [
        (
            "专家咨询",
            "example.com",
            'site:example.com "专家咨询" after:2026-01-01 before:2027-01-01',
        ),
        (
            "专家网络",
            "example.com",
            'site:example.com "专家网络" after:2026-01-01 before:2027-01-01',
        ),
        (
            "专家咨询",
            "",
            '"专家咨询" after:2026-01-01 before:2027-01-01',
        ),
        (
            "专家网络",
            "",
            '"专家网络" after:2026-01-01 before:2027-01-01',
        ),
    ]


def test_build_queries_appends_extra_queries_with_suffix(tmp_path) -> None:
    settings = _settings(tmp_path)
    settings = replace(settings, extra_queries=('"专家咨询服务采购" "入围"',), broad_search=False)

    queries = build_queries(settings)

    assert queries[-1] == (
        '"专家咨询服务采购" "入围"',
        "",
        '"专家咨询服务采购" "入围" after:2026-01-01 before:2027-01-01',
    )


def test_load_dotenv_values_parses_without_shell_execution(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        """
        # comment
        BID_ALERT_QUERY_SUFFIX="after:2025-07-01 before:2027-01-01"
        export BID_ALERT_MAX_EMAIL_ITEMS=50
        BAD-NAME=ignored
        """,
        encoding="utf-8",
    )

    values = load_dotenv_values(env_path)

    assert values == {
        "BID_ALERT_QUERY_SUFFIX": "after:2025-07-01 before:2027-01-01",
        "BID_ALERT_MAX_EMAIL_ITEMS": "50",
    }


def test_parse_feed_extracts_rss_items() -> None:
    xml_text = """
    <rss>
      <channel>
        <item>
          <title>专家咨询服务采购公告</title>
          <link>https://example.com/notice/1?utm_source=x</link>
          <description><![CDATA[外部专家访谈服务采购。]]></description>
          <pubDate>Mon, 25 May 2026 09:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """

    items = parse_feed(xml_text, keyword="专家咨询", query='site:example.com "专家咨询"', max_items=10)

    assert len(items) == 1
    assert items[0].title == "专家咨询服务采购公告"
    assert items[0].summary == "外部专家访谈服务采购。"
    assert items[0].source == "example.com"
    assert items[0].published == "Mon, 25 May 2026 09:00:00 GMT"


def test_dedupe_items_uses_canonical_link() -> None:
    first = AlertItem(
        title="专家咨询服务采购公告",
        link="https://example.com/notice/1?utm_source=x",
        summary="",
        published="",
        source="example.com",
        keyword="专家咨询",
        query="q1",
    )
    second = AlertItem(
        title="专家咨询服务采购公告",
        link="https://example.com/notice/1?from=rss",
        summary="",
        published="",
        source="example.com",
        keyword="专家网络",
        query="q2",
    )

    assert dedupe_items([first, second]) == [first]
    assert item_id(first) == item_id(second)


def test_canonical_link_preserves_ctbpsp_hash_uuid() -> None:
    first = AlertItem(
        title="专家咨询服务采购公告",
        link="http://ctbpsp.com/#/bulletinDetail?uuid=0002fcd1-cae5-4937-a407-25428b1c4399&invalue=专家咨询",
        summary="",
        published="",
        source="ctbpsp.com",
        keyword="专家咨询",
        query="q",
    )
    second = AlertItem(
        title="专家咨询服务采购公告",
        link="http://ctbpsp.com/#/bulletinDetail?uuid=ed38005f-ca33-4a24-ad8d-80b3e3b66e36&invalue=专家咨询",
        summary="",
        published="",
        source="ctbpsp.com",
        keyword="专家咨询",
        query="q",
    )

    assert canonical_link(first.link).endswith("#/bulletinDetail?uuid=0002fcd1-cae5-4937-a407-25428b1c4399")
    assert item_id(first) != item_id(second)


def test_canonical_link_keeps_business_query_params() -> None:
    first = AlertItem(
        title="西部证券股份有限公司2026年度外部专家咨询服务选聘项目中标候选人公示",
        link="https://stbid.stdzzb.com/ggInfo.do?pkid=2966&typ=2",
        summary="",
        published="",
        source="stbid.stdzzb.com",
        keyword="外部专家咨询服务",
        query="q",
    )
    second = AlertItem(
        title="西部证券股份有限公司2026年度外部专家咨询服务选聘项目中标结果公告",
        link="https://stbid.stdzzb.com/ggInfo.do?pkid=2920&typ=3",
        summary="",
        published="",
        source="stbid.stdzzb.com",
        keyword="外部专家咨询服务",
        query="q",
    )

    assert canonical_link(first.link) == "https://stbid.stdzzb.com/ggInfo.do?pkid=2966&typ=2"
    assert canonical_link(second.link) == "https://stbid.stdzzb.com/ggInfo.do?pkid=2920&typ=3"
    assert item_id(first) != item_id(second)


def test_state_tracks_seen_items(tmp_path) -> None:
    path = tmp_path / "state.json"
    item = AlertItem(
        title="专家网络入围采购公告",
        link="https://example.com/notice/2",
        summary="",
        published="",
        source="example.com",
        keyword="专家网络",
        query="q",
    )
    state = load_state(path)

    assert select_new_items([item], state) == [item]
    mark_seen(state, [item])
    save_state(path, state)

    reloaded = load_state(path)
    assert select_new_items([item], reloaded) == []


def test_render_email_body_includes_no_paid_crawl_note() -> None:
    item = AlertItem(
        title="专家咨询库服务采购公告",
        link="https://example.com/notice/3",
        summary="专家资源服务。",
        published="2026-05-25",
        source="example.com",
        keyword="专家咨询库",
        query="q",
    )

    body = render_email_body([item])

    assert "不直接抓取付费或禁止自动抓取的网站页面" in body
    assert "## 候选机会" in body
    assert "阶段：机会公告；相关性：强相关" in body
    assert "专家咨询库服务采购公告" in body
    assert "https://example.com/notice/3" in body


def test_render_email_body_reports_no_new_targets() -> None:
    body = render_email_body([], total_found=12, eligible_count=0)

    assert "## 本次运行摘要" in body
    assert "- 原始抓取结果：12 条" in body
    assert "- 符合规则结果：0 条" in body
    assert "- 新增公告：0 条" in body
    assert "## 本次未发现新目标" in body
    assert "系统已正常运行" in body


def test_render_failure_email_body_distinguishes_scan_failures() -> None:
    body = render_failure_email_body(BidAlertFetchError("All search feeds failed"))

    assert "扫描过程失败" in body
    assert "不是“未发现新目标”的摘要" in body
    assert "- 错误类型：BidAlertFetchError" in body
    assert "- 错误信息：All search feeds failed" in body


def test_send_email_can_use_smtp_ssl(monkeypatch, tmp_path) -> None:
    calls = {"starttls": 0, "login": 0, "send": 0}

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            assert host == "smtp.gmail.com"
            assert port == 465
            assert timeout == 3

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self) -> None:
            calls["starttls"] += 1

        def login(self, username: str, password: str) -> None:
            assert username == "sender@example.com"
            assert password == "app-password"
            calls["login"] += 1

        def send_message(self, message) -> None:
            assert message["To"] == "receiver@example.com"
            calls["send"] += 1

    monkeypatch.setattr("src.bid_alerts.smtplib.SMTP_SSL", FakeSMTP)
    settings = replace(
        _settings(tmp_path),
        recipient_email="receiver@example.com",
        sender_email="sender@example.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="app-password",
        smtp_use_ssl=True,
        smtp_use_tls=False,
    )
    item = AlertItem(
        title="专家咨询服务采购公告",
        link="https://example.com/notice/ssl",
        summary="",
        published="2026-05-27",
        source="example.com",
        keyword="专家咨询服务",
        query="q",
    )

    send_email(settings, [item])

    assert calls == {"starttls": 0, "login": 1, "send": 1}


def test_send_email_falls_back_to_curl_without_password_in_args(monkeypatch, tmp_path) -> None:
    class FailingSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            raise OSError("dns")

    captured = {}

    def fake_run(args, *, input, check, capture_output, text):
        captured["args"] = args
        captured["input"] = input
        assert check is True
        assert capture_output is True
        assert text is True

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr("src.bid_alerts.smtplib.SMTP_SSL", FailingSMTP)
    monkeypatch.setattr("src.bid_alerts.subprocess.run", fake_run)
    settings = replace(
        _settings(tmp_path),
        recipient_email="receiver@example.com",
        sender_email="sender@example.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="app-password",
        smtp_use_ssl=True,
        smtp_use_tls=False,
    )
    item = AlertItem(
        title="专家咨询服务采购公告",
        link="https://example.com/notice/curl-smtp",
        summary="",
        published="2026-05-27",
        source="example.com",
        keyword="专家咨询服务",
        query="q",
    )

    send_email(settings, [item])

    assert captured["args"] == ["curl", "-K", "-"]
    assert "app-password" not in captured["args"]
    assert "smtps://smtp.gmail.com:465" in captured["input"]
    assert "sender@example.com:app-password" in captured["input"]


def test_send_failure_email_uses_failure_subject_and_body(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_send_message(settings, message, *, progress=None, smtp_attempts=3, curl_retries=2):
        captured["subject"] = message["Subject"]
        captured["body"] = message.get_content()
        if progress:
            progress("fake sent")

    monkeypatch.setattr("src.bid_alerts._send_message", fake_send_message)
    settings = replace(
        _settings(tmp_path),
        recipient_email="receiver@example.com",
        sender_email="sender@example.com",
        smtp_host="smtp.gmail.com",
        smtp_username="sender@example.com",
        smtp_password="app-password",
    )
    messages: list[str] = []

    send_failure_email(settings, BidAlertFetchError("temporary failure"), progress=messages.append)

    assert captured["subject"] == "招标公告提醒：扫描失败"
    assert "temporary failure" in captured["body"]
    assert messages == ["fake sent"]


def test_send_test_email_uses_short_timeout_and_progress(monkeypatch, tmp_path) -> None:
    calls = {}

    def fake_send_email(
        settings,
        items,
        *,
        total_found=None,
        eligible_count=None,
        progress=None,
        smtp_attempts=3,
        curl_retries=2,
    ):
        calls["timeout"] = settings.request_timeout_seconds
        calls["smtp_attempts"] = smtp_attempts
        calls["curl_retries"] = curl_retries
        calls["title"] = items[0].title
        calls["total_found"] = total_found
        calls["eligible_count"] = eligible_count
        if progress:
            progress("fake progress")

    monkeypatch.setattr("src.bid_alerts.send_email", fake_send_email)
    messages: list[str] = []
    settings = replace(
        _settings(tmp_path),
        request_timeout_seconds=30,
        recipient_email="receiver@example.com",
        sender_email="sender@example.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="app-password",
        smtp_use_ssl=True,
        smtp_use_tls=False,
    )

    send_test_email(settings, progress=messages.append)

    assert calls == {
        "timeout": 8,
        "smtp_attempts": 1,
        "curl_retries": 0,
        "title": "测试：证券公司专家咨询服务供应商招标项目招标公告",
        "total_found": 1,
        "eligible_count": 1,
    }
    assert messages == ["准备发送测试邮件...", "fake progress"]


def test_run_alerts_sends_summary_email_when_no_targets(monkeypatch, tmp_path) -> None:
    settings = replace(
        _settings(tmp_path),
        recipient_email="receiver@example.com",
        sender_email="sender@example.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="app-password",
        smtp_use_ssl=True,
        smtp_use_tls=False,
    )
    sent = {}

    monkeypatch.setattr("src.bid_alerts.collect_alert_items", lambda settings, progress=None: [])

    def fake_send_email(settings, items, *, total_found=None, eligible_count=None, progress=None, **kwargs):
        sent["items"] = items
        sent["total_found"] = total_found
        sent["eligible_count"] = eligible_count

    monkeypatch.setattr("src.bid_alerts.send_email", fake_send_email)

    total, new_count = run_alerts(settings)

    assert (total, new_count) == (0, 0)
    assert sent == {"items": [], "total_found": 0, "eligible_count": 0}
    assert settings.state_file.exists()


def test_run_alerts_dry_run_and_prime_do_not_send_summary(monkeypatch, tmp_path) -> None:
    settings = replace(
        _settings(tmp_path),
        recipient_email="receiver@example.com",
        sender_email="sender@example.com",
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="app-password",
        smtp_use_ssl=True,
        smtp_use_tls=False,
    )
    calls = {"send": 0}

    monkeypatch.setattr("src.bid_alerts.collect_alert_items", lambda settings, progress=None: [])

    def fake_send_email(*args, **kwargs):
        calls["send"] += 1

    monkeypatch.setattr("src.bid_alerts.send_email", fake_send_email)

    assert run_alerts(settings, dry_run=True) == (0, 0)
    assert run_alerts(settings, prime=True) == (0, 0)
    assert calls["send"] == 0


def test_main_sends_failure_email_when_scan_fails(monkeypatch, tmp_path, capsys) -> None:
    settings = replace(
        _settings(tmp_path),
        recipient_email="receiver@example.com",
        sender_email="sender@example.com",
        smtp_host="smtp.gmail.com",
        smtp_username="sender@example.com",
        smtp_password="app-password",
    )
    sent = {}

    monkeypatch.setattr("src.bid_alerts.load_settings", lambda: settings)

    def fake_collect_alert_items(settings, progress=None):
        raise BidAlertFetchError("All search feeds failed")

    def fake_send_failure_email(settings, error, *, progress=None):
        sent["error"] = str(error)

    monkeypatch.setattr("src.bid_alerts.collect_alert_items", fake_collect_alert_items)
    monkeypatch.setattr("src.bid_alerts.send_failure_email", fake_send_failure_email)

    assert main([]) == 1
    assert sent == {"error": "All search feeds failed"}
    assert "Bid alert error: All search feeds failed" in capsys.readouterr().err


def test_relevance_sort_pushes_strong_title_matches_first() -> None:
    weak = AlertItem(
        title="关于评审专家库抽取规则的通知",
        link="https://example.com/notice/weak",
        summary="评审专家从专家库随机抽取。",
        published="2026-05-25",
        source="example.com",
        keyword="专家库",
        query="q",
    )
    strong = AlertItem(
        title="筹建专家咨询库项目采购公告",
        link="https://example.com/notice/strong",
        summary="选聘咨询中介机构。",
        published="2026-05-18",
        source="example.com",
        keyword="专家咨询库",
        query="q",
    )

    assert relevance_label(strong) == "强相关"
    assert relevance_label(weak) == "弱相关"
    assert sort_items_by_relevance([weak, strong]) == [strong, weak]


def test_filter_items_by_relevance_removes_weak_context() -> None:
    weak = AlertItem(
        title="关于评审专家库抽取规则的通知",
        link="https://example.com/notice/weak",
        summary="评审专家从专家库随机抽取。",
        published="2026-05-25",
        source="example.com",
        keyword="专家库",
        query="q",
    )
    strong = AlertItem(
        title="国寿股权投资有限公司行业专家咨询服务采购项目招标公告",
        link="https://example.com/notice/strong",
        summary="行业专家咨询服务采购。",
        published="2026-04-02",
        source="example.com",
        keyword="行业专家咨询服务采购",
        query="q",
    )

    assert filter_items_by_relevance([weak, strong], min_score=5) == [strong]


def test_filter_items_by_relevance_removes_search_engine_noise() -> None:
    noisy = AlertItem(
        title="S15 全球总决赛入围赛 T1 淘汰 iG 晋级正赛",
        link="https://example.com/noise",
        summary="体育新闻。",
        published="2026-05-25",
        source="example.com",
        keyword="专家咨询服务",
        query="q",
    )

    assert filter_items_by_relevance([noisy], min_score=2) == []


def test_relevance_labels_industry_expert_consulting_result_as_strong() -> None:
    item = AlertItem(
        title="国投证券产业研究院2026年专家咨询服务采购入围项目候选人公示",
        link="https://example.com/notice/gt",
        summary="中标候选人公示。",
        published="2026-05-15",
        source="example.com",
        keyword="专家咨询服务采购",
        query="q",
    )

    assert relevance_label(item) == "强相关"


def test_relevance_labels_candidate_notice_as_strong() -> None:
    item = AlertItem(
        title="国投证券产业研究院2026年专家咨询服务采购入围项目候选人公示",
        link="https://example.com/notice/gt-candidate",
        summary="第1中标候选人。",
        published="2026-05-15",
        source="example.com",
        keyword="专家咨询服务采购",
        query="q",
    )

    assert relevance_label(item) == "强相关"


def test_relevance_labels_huaneng_result_as_strong() -> None:
    item = AlertItem(
        title="[华能资本公司长城证券2026年度专家咨询服务入围采购招标] 中标结果公示",
        link="https://example.com/notice/hn",
        summary="中标人信息。",
        published="2026-04-21",
        source="example.com",
        keyword="专家咨询服务入围采购",
        query="q",
    )

    assert relevance_label(item) == "强相关"


def test_relevance_labels_guotai_framework_notice_as_strong() -> None:
    item = AlertItem(
        title="国泰海通证券2025年专家咨询服务框架协议采购项目中标候选人公示",
        link="https://example.com/notice/gt",
        summary="专家咨询服务框架协议采购项目。",
        published="2026-01-21",
        source="example.com",
        keyword="专家咨询服务框架协议",
        query="q",
    )

    assert relevance_label(item) == "强相关"


def test_relevance_labels_expert_consulting_purchase_without_service_as_strong() -> None:
    item = AlertItem(
        title="专家咨询采购项目招标公告",
        link="https://example.com/notice/qingdao-guoxin",
        summary="招标编号：ZBXM-7310-2026-00017。",
        published="2026-07-01",
        source="example.com",
        keyword="专家咨询采购项目",
        query="q",
    )

    assert relevance_label(item) == "强相关"
    assert filter_items_by_relevance([item], min_score=5) == [item]
    assert notice_stage(item) == "opportunity"


def test_relevance_labels_h2_financial_expert_titles_as_strong() -> None:
    titles = [
        "国泰海通证券2025年专家咨询服务框架协议采购项目公开招标公告",
        "西部证券股份有限公司2026年度外部专家咨询服务选聘项目",
        "华源证券2026年度专家咨询服务供应商招标项目招标公告",
        "河南信息产业私募基金管理有限公司专家访谈咨询服务项目询比采购结果公告",
        "中信私募基金管理有限公司外部专家咨询服务采购项目结果公示",
        "财通证券股份有限公司2025年-2027年专家咨询服务供应商入围采购项目入围候选人公示",
        "华泰证券股份有限公司专家咨询服务招标项目中标候选人公示",
    ]

    items = [
        AlertItem(
            title=title,
            link=f"https://example.com/notice/{index}",
            summary="",
            published="2025-12-18",
            source="example.com",
            keyword="专家咨询服务",
            query="q",
        )
        for index, title in enumerate(titles)
    ]

    assert [relevance_label(item) for item in items] == ["强相关"] * len(items)
    assert {item.title for item in filter_items_by_relevance(items, min_score=5)} == set(titles)


def test_filter_items_by_relevance_removes_outdoor_ad_safety_noise() -> None:
    item = AlertItem(
        title="户外广告设施安全评估和专家咨询服务",
        link="https://example.com/notice/ad-safety",
        summary="户外广告设施安全评估。",
        published="2025-07-24",
        source="example.com",
        keyword="专家咨询服务",
        query="q",
    )

    assert relevance_label(item) == "强相关"
    assert filter_items_by_relevance([item], min_score=5) == []


def test_default_rules_include_h2_missing_counterexamples() -> None:
    assert "ctbpsp.com" in DEFAULT_SITE_FILTERS
    assert "stbid.stdzzb.com" in DEFAULT_SITE_FILTERS
    assert "bbda.com" in DEFAULT_SITE_FILTERS
    assert "外部专家咨询服务" in DEFAULT_KEYWORDS
    assert "专家咨询采购" in DEFAULT_KEYWORDS
    assert "专家咨询采购项目" in DEFAULT_KEYWORDS
    assert "专家访谈咨询服务" in DEFAULT_KEYWORDS
    assert "专家咨询服务选聘" in DEFAULT_KEYWORDS


def test_build_ctbpsp_mirror_queries_uses_title_uuid_and_source_sites(tmp_path) -> None:
    settings = replace(
        _settings(tmp_path),
        ctbpsp_mirror_site_filters=("gy.chinamae.com",),
        query_suffix="after:2025-07-01",
    )
    item = AlertItem(
        title="国泰海通证券2025年专家咨询服务框架协议采购项目公开招标公告",
        link="http://ctbpsp.com/#/bulletinDetail?uuid=c0404862-ca38-4dff-b191-25af1b35da00&invalue=专家咨询",
        summary="",
        published="2025-12-29",
        source="ctbpsp.com",
        keyword="专家咨询服务",
        query="q",
    )

    queries = build_ctbpsp_mirror_queries(item, settings)

    assert queries == [
        '"国泰海通证券2025年专家咨询服务框架协议采购项目公开招标公告" after:2025-07-01',
        '"国泰海通证券2025年专家咨询服务框架协议采购项目公开招标公告" "c0404862-ca38-4dff-b191-25af1b35da00" after:2025-07-01',
        'site:gy.chinamae.com "国泰海通证券2025年专家咨询服务框架协议采购项目公开招标公告" after:2025-07-01',
    ]


def test_enrich_national_platform_items_replaces_ctbpsp_with_public_source(monkeypatch, tmp_path) -> None:
    settings = replace(_settings(tmp_path), ctbpsp_mirror_site_filters=("gy.chinamae.com",))
    item = AlertItem(
        title="国泰海通证券2025年专家咨询服务框架协议采购项目公开招标公告",
        link="http://ctbpsp.com/#/bulletinDetail?uuid=c0404862-ca38-4dff-b191-25af1b35da00&invalue=专家咨询",
        summary="",
        published="2025-12-29",
        source="ctbpsp.com",
        keyword="专家咨询服务",
        query="q",
    )

    def fake_fetch(url: str, *, timeout_seconds: int) -> str:
        return """
        <rss>
          <channel>
            <item>
              <title>国泰海通证券2025年专家咨询服务框架协议采购项目公开招标公告</title>
              <link>https://gy.chinamae.com/news/8b33b1765c3b12473675c9bde710cde8.html</link>
              <description>专家咨询服务框架协议采购。</description>
              <pubDate>2025-12-29</pubDate>
            </item>
          </channel>
        </rss>
        """

    monkeypatch.setattr("src.bid_alerts.fetch_feed", fake_fetch)

    items = enrich_national_platform_items(settings, [item])

    assert len(items) == 1
    assert items[0].link == "https://gy.chinamae.com/news/8b33b1765c3b12473675c9bde710cde8.html"
    assert "国家平台聚合链接：http://ctbpsp.com/#/bulletinDetail" in items[0].summary
    assert "国家平台 UUID：c0404862-ca38-4dff-b191-25af1b35da00" in items[0].summary


def test_relevance_labels_huayuan_supplier_result_as_strong() -> None:
    item = AlertItem(
        title="华源证券2026年度专家咨询服务供应商招标项目评标结果公示",
        link="https://example.com/notice/hy",
        summary="中标候选人基本情况。",
        published="2026-01-06",
        source="example.com",
        keyword="专家咨询服务供应商招标",
        query="q",
    )

    assert relevance_label(item) == "强相关"


def test_relevance_labels_expert_service_selection_as_strong() -> None:
    item = AlertItem(
        title="中银国际证券股份有限公司专家服务选型供应商入围项目成交公告",
        link="https://example.com/notice/boci",
        summary="专家服务供应商入围项目。",
        published="2026-05-20",
        source="example.com",
        keyword="专家服务选型",
        query="q",
    )

    assert relevance_label(item) == "强相关"
    assert filter_items_by_relevance([item], min_score=5) == [item]


def test_parse_public_html_items_extracts_sdicc_rows() -> None:
    html_text = """
    <table>
      <tr onclick="urlChange('f3b00b23-c19a-4def-a74c-1284aed90ce8','abc')">
        <td><span>[公开招标] 国投证券产业研究院2026年专家咨询服务采购入围项目公开招标公告</span></td>
        <td><span>2026-03-24 20:00</span></td>
        <td><span>2026-03-20</span></td>
      </tr>
      <tr onclick="urlChangeHxr('bd-guid','hxr-guid')">
        <td><span>国投证券产业研究院2026年专家咨询服务采购入围项目候选人公示</span></td>
        <td><span>2026-05-15</span></td>
      </tr>
    </table>
    """

    items = parse_public_html_items(html_text, source_url="https://www.sdicc.com.cn/")

    assert len(items) == 2
    assert items[0].title == "[公开招标] 国投证券产业研究院2026年专家咨询服务采购入围项目公开招标公告"
    assert items[0].link == (
        "https://www.sdicc.com.cn/cgxx/ggDetail?"
        "gcGuid=abc&ggGuid=f3b00b23-c19a-4def-a74c-1284aed90ce8"
    )
    assert items[0].published == "2026-03-20"
    assert items[1].link == "https://www.sdicc.com.cn/cgxx/zbhxrDetail?bdGuid=bd-guid&guid=hxr-guid"
    assert filter_items_by_relevance(items, min_score=5) == items


def test_parse_public_html_items_extracts_chinamae_list_dates() -> None:
    html_text = """
    <ul>
      <li>
        <div>
          <a href="/purchases/city/2337" class="bold">[广元市]</a>
          <a href="/news/8b33b1765c3b12473675c9bde710cde8.html" title="国泰海通证券2025年专家咨询服务框架协议采购项目">
            国泰海通证券2025年专家咨询服务框架协议采购项目
          </a>
        </div>
        <div class="source"><div></div><div style="color: red">2026-01-21</div></div>
      </li>
    </ul>
    """

    items = parse_public_html_items(html_text, source_url="https://gy.chinamae.com/purchases/type/3")

    assert items[0].title == "国泰海通证券2025年专家咨询服务框架协议采购项目"
    assert items[0].link == "https://gy.chinamae.com/news/8b33b1765c3b12473675c9bde710cde8.html"
    assert items[0].published == "2026-01-21"
    assert filter_items_by_relevance(items, min_score=5) == [items[0]]


def test_parse_public_html_items_extracts_ctsec_bidding_titles() -> None:
    html_text = """
    <ul>
      <li>
        <a href="/res/pdf/1663486736.pdf" target="_blank">
          <span class="hots_tit">财通证券专家服务选型供应商入围项目成交公告</span>
          <span class="time" time="yyyy-MM-dd">Tue May 26 2026 15:11:55 GMT+0800 (CST)</span>
        </a>
      </li>
    </ul>
    """

    items = parse_public_html_items(html_text, source_url="https://www.ctsec.com/bidding")

    assert items[0].title == "财通证券专家服务选型供应商入围项目成交公告"
    assert items[0].link == "https://www.ctsec.com/res/pdf/1663486736.pdf"
    assert items[0].published == "Tue May 26 2026 15:11:55 GMT+0800 (CST)"
    assert filter_items_by_relevance(items, min_score=5) == items


def test_parse_public_html_items_extracts_qingdao_guoxin_notice() -> None:
    html_text = """
    <ul>
      <li name="li_name">
        <a href="/cms/channel/xmgg1fw/14022.htm" title="专家咨询服务项目公告" target="_blank">
          <span><i class="iconfont"></i>专家咨询服务项目公告</span>
          <em>2026-07-01</em>
        </a>
      </li>
    </ul>
    """

    items = parse_public_html_items(
        html_text,
        source_url="https://eps.qdgxjt.com/cms/channel/xmgg1qb/index.htm",
    )

    assert "eps.qdgxjt.com" in DEFAULT_SITE_FILTERS
    assert any("eps.qdgxjt.com/cms/channel/xmgg1qb" in url for url in DEFAULT_SOURCE_URLS)
    assert len(items) == 1
    assert items[0].title == "专家咨询服务项目公告"
    assert items[0].link == "https://eps.qdgxjt.com/cms/channel/xmgg1fw/14022.htm"
    assert items[0].published == "2026-07-01"
    assert relevance_label(items[0]) == "强相关"
    assert filter_items_by_relevance(items, min_score=5) == items


def test_collect_alert_items_continues_when_one_query_fails(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    calls = {"count": 0}

    def fake_fetch(url: str, *, timeout_seconds: int) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise BidAlertFetchError("temporary failure")
        return """
        <rss>
          <channel>
            <item>
              <title>专家网络服务采购公告</title>
              <link>https://example.com/notice/4</link>
            </item>
          </channel>
        </rss>
        """

    monkeypatch.setattr("src.bid_alerts.fetch_feed", fake_fetch)

    items = collect_alert_items(settings)

    assert len(items) == 1
    assert items[0].title == "专家网络服务采购公告"


def test_fetch_feed_falls_back_to_curl_when_requests_dns_fails(monkeypatch) -> None:
    def fake_requests_get(*args, **kwargs):
        raise requests.ConnectionError("dns")

    class FakeCompleted:
        stdout = "<rss><channel></channel></rss>"

    def fake_run(args, *, check, capture_output, text):
        assert args[:2] == ["curl", "-L"]
        assert "--max-time" in args
        assert args[-1] == "https://example.com/rss"
        assert check is True
        assert capture_output is True
        assert text is True
        return FakeCompleted()

    monkeypatch.setattr("src.bid_alerts.requests.get", fake_requests_get)
    monkeypatch.setattr("src.bid_alerts.subprocess.run", fake_run)

    assert fetch_feed("https://example.com/rss", timeout_seconds=3) == "<rss><channel></channel></rss>"


def test_notice_stage_and_priority_bucket_focuses_open_financial_opportunities() -> None:
    open_item = AlertItem(
        title="国泰海通证券2025年专家咨询服务框架协议采购项目公开招标公告",
        link="https://example.com/notice/open",
        summary="为公募、保险、银行、私募及专业知识服务采购专家咨询服务。",
        published="2025-12-29",
        source="example.com",
        keyword="专家咨询服务框架协议",
        query="q",
    )
    candidate_item = AlertItem(
        title="国泰海通证券2025年专家咨询服务框架协议采购项目中标候选人公示",
        link="https://example.com/notice/candidate",
        summary="中标候选人基本情况。",
        published="2026-01-21",
        source="example.com",
        keyword="专家咨询服务框架协议",
        query="q",
    )
    change_item = AlertItem(
        title="华源证券2026年度专家咨询服务供应商招标项目更正公告",
        link="https://example.com/notice/change",
        summary="证券专家咨询服务供应商招标项目。",
        published="2025-12-16",
        source="example.com",
        keyword="专家咨询服务供应商招标",
        query="q",
    )
    excluded_item = AlertItem(
        title="户外广告设施安全评估和专家咨询服务采购公告",
        link="https://example.com/notice/noise",
        summary="广告设施安全评估。",
        published="2025-07-24",
        source="example.com",
        keyword="专家咨询服务",
        query="q",
    )

    assert notice_stage(open_item) == "opportunity"
    assert priority_bucket(open_item) == "立即关注"
    assert notice_stage(candidate_item) == "candidate"
    assert priority_bucket(candidate_item) == "结果复盘"
    assert notice_stage(change_item) == "change"
    assert priority_bucket(change_item) == "立即关注"
    assert priority_bucket(excluded_item) == "其他"


def test_project_key_groups_open_candidate_and_result_notices() -> None:
    open_item = AlertItem(
        title="[华能资本公司长城证券2026年度专家咨询服务入围采购招标] 招标公告",
        link="https://example.com/notice/open",
        summary="招标编号：HNZB2026-02-1-164",
        published="2026-02-25",
        source="example.com",
        keyword="专家咨询服务入围采购",
        query="q",
    )
    candidate_item = AlertItem(
        title="[华能资本公司长城证券2026年度专家咨询服务入围采购招标] 候选人公示",
        link="https://example.com/notice/candidate",
        summary="招标编号：HNZB2026-02-1-164",
        published="2026-04-17",
        source="example.com",
        keyword="专家咨询服务入围采购",
        query="q",
    )
    result_item = AlertItem(
        title="[华能资本公司长城证券2026年度专家咨询服务入围采购招标] 中标结果公示",
        link="https://example.com/notice/result",
        summary="招标编号：HNZB2026-02-1-164",
        published="2026-04-21",
        source="example.com",
        keyword="专家咨询服务入围采购",
        query="q",
    )

    assert extract_project_no(open_item.summary) == "HNZB2026-02-1-164"
    assert normalize_project_title(open_item.title) == "华能资本公司长城证券2026年度专家咨询服务入围采购招标"
    assert project_key(open_item) == project_key(candidate_item) == project_key(result_item)
    assert len(group_items_by_project([open_item, candidate_item, result_item])) == 1


def test_render_email_body_groups_by_priority_and_shows_project_stages() -> None:
    open_item = AlertItem(
        title="华源证券2026年度专家咨询服务供应商招标项目招标公告",
        link="https://example.com/notice/open",
        summary="项目编号：HBT-15224190-258792。证券专家咨询服务供应商招标项目。",
        published="2025-12-16",
        source="example.com",
        keyword="专家咨询服务供应商招标",
        query="q",
    )
    result_item = AlertItem(
        title="华源证券2026年度专家咨询服务供应商招标项目评标结果公示",
        link="https://example.com/notice/result",
        summary="项目编号：HBT-15224190-258792。中标候选人基本情况。",
        published="2026-01-06",
        source="example.com",
        keyword="专家咨询服务供应商招标",
        query="q",
    )

    body = render_email_body([result_item, open_item])

    assert body.index("## 立即关注") < body.index("## 结果复盘")
    assert "阶段：机会公告；相关性：强相关" in body
    assert "阶段：候选/评标；相关性：强相关" in body
    assert "同项目已发现阶段：机会公告1条、候选/评标1条" in body


def test_parse_attachment_items_uses_nearby_notice_title_for_generic_links() -> None:
    html_text = """
    <section>
      <h2>华泰证券股份有限公司专家咨询服务招标项目中标候选人公示</h2>
      <a href="/files/result.pdf">附件下载</a>
    </section>
    """

    items = parse_attachment_items(html_text, source_url="https://www.example.com/notices/")

    assert len(items) == 1
    assert items[0].title == "华泰证券股份有限公司专家咨询服务招标项目中标候选人公示"
    assert items[0].link == "https://www.example.com/files/result.pdf"
    assert filter_items_by_relevance(items, min_score=5) == items


def test_source_display_name_uses_source_registry() -> None:
    item = AlertItem(
        title="财通证券专家服务选型供应商入围项目成交公告",
        link="https://www.ctsec.com/res/pdf/1663486736.pdf",
        summary="",
        published="2026-05-26",
        source="www.ctsec.com",
        keyword="专家服务选型",
        query="q",
    )

    assert source_display_name(item) == "财通证券招标公告（hot）"
