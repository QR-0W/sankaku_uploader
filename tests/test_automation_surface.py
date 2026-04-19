from pathlib import Path
from types import SimpleNamespace

from sankaku_uploader.infrastructure.automation import (
    AutomationConfig,
    ReviewDecision,
    SankakuAutomationClient,
    wait_for_ai_tags,
)


class FakeLocator:
    def __init__(self, page, selector: str):
        self.page = page
        self.selector = selector

    def count(self) -> int:
        return int(self.page.counts.get(self.selector, 0))

    @property
    def first(self):
        return self

    def is_visible(self) -> bool:
        return self.count() > 0

    def is_enabled(self) -> bool:
        return self.count() > 0

    def click(self, timeout=None):
        self.page.clicks.append(self.selector)
        if self.selector == "button:advanced":
            self.page.counts["input[name='parent']"] = 1

    def fill(self, value: str):
        self.page.fills.append((self.selector, value))

    def press(self, key: str):
        self.page.presses.append((self.selector, key))

    def set_input_files(self, value: str):
        self.page.fills.append((self.selector, value))


class FakePage:
    def __init__(self, counts: dict[str, int], url: str = "https://example.com/upload", evaluate_result=None):
        self.counts = counts
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.presses: list[tuple[str, str]] = []
        self.url = url
        self.closed = False
        self.evaluate_result = evaluate_result

    def locator(self, selector: str):
        normalized = selector
        if selector == "button":
            # not needed for these tests
            return FakeLocator(self, selector)
        return FakeLocator(self, normalized)

    def on(self, _event: str, _handler):
        return None

    def remove_listener(self, _event: str, _handler):
        return None

    def wait_for_load_state(self, _state: str, timeout: int = 0):
        return None

    def close(self):
        self.closed = True

    def evaluate(self, *_args, **_kwargs):
        if self.evaluate_result is not None:
            return self.evaluate_result
        return ""


class FakeContext:
    def __init__(self, pages):
        self.pages = pages

    def close(self):
        return None

    def new_page(self):
        page = FakePage({})
        self.pages.append(page)
        return page


class FakeResponse:
    def __init__(self, *, url: str, status: int = 200, headers: dict | None = None, json_payload=None, text_payload: str = ""):
        self.url = url
        self.status = status
        self.headers = headers or {}
        self._json_payload = json_payload
        self._text_payload = text_payload

    def json(self):
        if self._json_payload is None:
            raise RuntimeError("no json")
        return self._json_payload

    def text(self):
        return self._text_payload


def _build_client() -> SankakuAutomationClient:
    return SankakuAutomationClient(
        AutomationConfig(
            upload_url="https://example.com/upload",
            profile_dir=Path("."),
            submit_timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        )
    )


def test_wait_until_upload_surface_ready_success() -> None:
    page = FakePage({"input[type='file']": 1})
    client = _build_client()
    ready, reason = client._wait_until_upload_surface_ready(page)
    assert ready is True
    assert reason == ""


def test_wait_until_upload_surface_ready_login_required() -> None:
    page = FakePage({"input[type='password']": 1})
    client = _build_client()
    client.config.headless = True
    ready, reason = client._wait_until_upload_surface_ready(page)
    assert ready is False
    assert "login required" in reason


def test_wait_until_upload_surface_ready_detects_otp_inputs() -> None:
    page = FakePage({"input[role='spinbutton']": 6})
    client = _build_client()
    client.config.headless = True
    ready, reason = client._wait_until_upload_surface_ready(page)
    assert ready is False
    assert "login required" in reason


def test_ensure_advanced_panel_open_clicks_when_parent_not_visible(monkeypatch) -> None:
    page = FakePage({"button:advanced": 1})
    client = _build_client()

    monkeypatch.setattr(
        "sankaku_uploader.infrastructure.automation.find_button_by_text",
        lambda _page, _candidates: FakeLocator(page, "button:advanced"),
    )
    monkeypatch.setattr(
        "sankaku_uploader.infrastructure.automation.find_first_locator",
        lambda _page, selectors: FakeLocator(page, "input[name='parent']") if page.counts.get("input[name='parent']", 0) else None,
    )

    client._ensure_advanced_panel_open(page)
    assert "button:advanced" in page.clicks
    assert page.counts["input[name='parent']"] == 1


def test_try_apply_minimum_tag_uses_autocomplete(monkeypatch) -> None:
    page = FakePage({"#autocomplete": 1})
    client = _build_client()
    monkeypatch.setattr(
        "sankaku_uploader.infrastructure.automation.find_first_locator",
        lambda _page, _selectors: FakeLocator(page, "#autocomplete"),
    )
    ok = client._try_apply_minimum_tag(page, ["multiple_views", "1girl"])
    assert ok is True
    assert ("#autocomplete", "multiple_views") in page.fills
    assert ("#autocomplete", "Enter") in page.presses


def test_select_file_prefers_direct_input_for_parallel_pages() -> None:
    page = FakePage({"input[type='file']": 1, "button": 1})
    client = _build_client()
    selected_by = client._select_file(page, Path("a.png"))
    assert selected_by == "input_file"


def test_wait_for_uploaded_post_reads_new_tab_post_url() -> None:
    page = FakePage({}, url="https://www.sankakucomplex.com/zh-CN/posts/upload")
    context = FakeContext(
        [
            FakePage({}, url="https://www.sankakucomplex.com/zh-CN/posts/upload"),
            FakePage({}, url="https://www.sankakucomplex.com/zh-CN/posts/abc123"),
        ]
    )
    client = _build_client()
    url, post_id = client._wait_for_uploaded_post(
        page,
        context=context,
        timeout_seconds=0.01,
        ignore_post_ids=set(),
    )
    assert post_id == "abc123"
    assert "abc123" in url


def test_wait_for_uploaded_post_ignores_known_ids() -> None:
    page = FakePage({}, url="https://www.sankakucomplex.com/zh-CN/posts/upload")
    context = FakeContext([FakePage({}, url="https://www.sankakucomplex.com/zh-CN/posts/known1")])
    client = _build_client()
    url, post_id = client._wait_for_uploaded_post(
        page,
        context=context,
        timeout_seconds=0.01,
        ignore_post_ids={"known1"},
    )
    assert post_id == ""
    assert "upload" in url


def test_extract_post_ids_from_response_json_payload() -> None:
    client = _build_client()
    response = FakeResponse(
        url="https://www.sankakucomplex.com/api/v1/posts",
        headers={"content-type": "application/json"},
        json_payload={"post": {"id": "xyz987"}},
    )
    assert client._extract_post_ids_from_response(response) == ["xyz987"]


def test_extract_post_ids_from_response_text_payload() -> None:
    client = _build_client()
    response = FakeResponse(
        url="https://www.sankakucomplex.com/api/v1/upload",
        headers={"content-type": "text/plain"},
        text_payload='{"post_id":"abc123"}',
    )
    assert client._extract_post_ids_from_response(response) == ["abc123"]


def test_extract_post_ids_filters_non_post_tokens() -> None:
    client = _build_client()
    response = FakeResponse(
        url="https://www.sankakucomplex.com/api/v1/upload",
        headers={"content-type": "application/json"},
        json_payload={"post_id": "tagging_image", "alt": "subtitles"},
    )
    assert client._extract_post_ids_from_response(response) == []


def test_wait_for_ai_tags_extends_while_editor_progress(monkeypatch) -> None:
    states = [([], True), ([], True), (["late-tag"], False)]

    def fake_editor(_page):
        return states.pop(0) if states else (["late-tag"], False)

    monkeypatch.setattr("sankaku_uploader.infrastructure.automation._extract_tags_from_editor_section", fake_editor)
    monkeypatch.setattr("sankaku_uploader.infrastructure.automation.extract_ai_tags", lambda _page: [])
    monkeypatch.setattr("sankaku_uploader.infrastructure.automation.time.sleep", lambda *_: None)

    tags, available = wait_for_ai_tags(object(), timeout_seconds=0.001, poll_interval_seconds=0.0)
    assert available is True
    assert tags == ["late-tag"]


def test_upload_one_syncs_web_edited_tags_before_submit(monkeypatch) -> None:
    page = FakePage({})
    context = FakeContext([page])
    client = _build_client()
    client.config.run_mode = "auto_submit"

    monkeypatch.setattr(client, "_dismiss_common_overlays", lambda *_: None)
    monkeypatch.setattr(client, "_select_file", lambda *_: "input_file")
    monkeypatch.setattr("sankaku_uploader.infrastructure.automation.wait_for_ai_tags", lambda *_args, **_kwargs: (["old-tag"], True))
    monkeypatch.setattr(client, "_review_decision", lambda *_, **__: ReviewDecision("confirm"))
    monkeypatch.setattr(
        client,
        "_sync_tags_after_review",
        lambda _page, baseline_tags: ["edited-tag", "edited-2"],
    )
    monkeypatch.setattr(client, "_wait_for_submit", lambda _page: FakeLocator(page, "submit"))
    monkeypatch.setattr(client, "_wait_for_uploaded_post", lambda *_args, **_kwargs: ("https://www.sankakucomplex.com/posts/abc", "abc"))

    result = client._upload_one(page, context, SimpleNamespace(item_id="i1", file_name="x.png", file_path="x.png"), parent_post_id="", known_post_ids=set())
    assert result.success is True
    assert result.ai_tags == ["edited-tag", "edited-2"]


def test_upload_one_syncs_manual_clear_to_empty_tags(monkeypatch) -> None:
    page = FakePage({})
    context = FakeContext([page])
    client = _build_client()
    client.config.run_mode = "auto_submit"

    monkeypatch.setattr(client, "_dismiss_common_overlays", lambda *_: None)
    monkeypatch.setattr(client, "_select_file", lambda *_: "input_file")
    monkeypatch.setattr("sankaku_uploader.infrastructure.automation.wait_for_ai_tags", lambda *_args, **_kwargs: (["old-tag"], True))
    monkeypatch.setattr(client, "_review_decision", lambda *_, **__: ReviewDecision("confirm"))
    monkeypatch.setattr(
        client,
        "_sync_tags_after_review",
        lambda _page, baseline_tags: [],
    )
    monkeypatch.setattr(client, "_wait_for_submit", lambda _page: FakeLocator(page, "submit"))
    monkeypatch.setattr(client, "_wait_for_uploaded_post", lambda *_args, **_kwargs: ("https://www.sankakucomplex.com/posts/abc", "abc"))

    result = client._upload_one(page, context, SimpleNamespace(item_id="i1", file_name="x.png", file_path="x.png"), parent_post_id="", known_post_ids=set())
    assert result.success is True
    assert result.ai_tags == []


def test_sync_tags_after_review_waits_for_final_editor_state(monkeypatch) -> None:
    client = _build_client()
    states = [(["a", "b"], True), (["a"], False)]

    def fake_extract(_page):
        return states.pop(0)

    monkeypatch.setattr("sankaku_uploader.infrastructure.automation._extract_tags_from_editor_section", fake_extract)
    monkeypatch.setattr("sankaku_uploader.infrastructure.automation.time.sleep", lambda *_: None)
    tags = client._sync_tags_after_review(object(), baseline_tags=["a", "b", "c"])
    assert tags == ["a"]


def test_sync_tags_after_review_returns_empty_when_editor_cleared(monkeypatch) -> None:
    client = _build_client()
    monkeypatch.setattr(
        "sankaku_uploader.infrastructure.automation._extract_tags_from_editor_section",
        lambda _page: ([], False),
    )
    monkeypatch.setattr("sankaku_uploader.infrastructure.automation.time.sleep", lambda *_: None)
    tags = client._sync_tags_after_review(object(), baseline_tags=["a", "b"])
    assert tags == []


def test_review_decision_applies_live_sync_then_waits_for_confirm(monkeypatch) -> None:
    client = _build_client()
    page = FakePage({})
    decisions = [
        ReviewDecision(action="sync", tags_override=["a", "b"]),
        ReviewDecision(action="confirm"),
    ]
    applied: list[list[str]] = []
    monkeypatch.setattr(client, "_apply_tags_override", lambda _page, tags: applied.append(list(tags)) or True)
    monkeypatch.setattr(
        "sankaku_uploader.infrastructure.automation._extract_tags_from_editor_section",
        lambda _page: (["web-tag"], False),
    )
    monkeypatch.setattr("sankaku_uploader.infrastructure.automation.time.sleep", lambda *_: None)
    client.review_decision_provider = lambda *_: decisions.pop(0) if decisions else None

    result = client._review_decision(page, SimpleNamespace(item_id="i1", file_name="x.png"), ["base"], True)
    assert result.action == "confirm"
    assert applied == [["a", "b"]]


def test_review_decision_marks_active_and_background_request_modes(monkeypatch) -> None:
    client = _build_client()
    active_page = FakePage({})
    background_page = FakePage({})
    active_item = SimpleNamespace(item_id="i1", file_name="active.png")
    background_item = SimpleNamespace(item_id="i2", file_name="background.png")
    all_prepared = [
        SimpleNamespace(item=active_item, page=active_page, available=True),
        SimpleNamespace(item=background_item, page=background_page, available=True),
    ]
    calls: list[tuple[str, str]] = []

    def provider(item, _tags, _available, mode):
        calls.append((item.item_id, mode))
        if item.item_id == active_item.item_id and mode == "decide":
            return ReviewDecision(action="confirm")
        return None

    monkeypatch.setattr(
        "sankaku_uploader.infrastructure.automation._extract_tags_from_editor_section",
        lambda _page: (["web-tag"], False),
    )
    client.review_decision_provider = provider

    result = client._review_decision(active_page, active_item, ["base"], True, all_prepared=all_prepared)

    assert result.action == "confirm"
    assert (background_item.item_id, "probe") in calls
    assert calls[-1] == (active_item.item_id, "decide")


def test_select_working_page_prefers_upload_url() -> None:
    upload_page = FakePage({}, url="https://www.sankakucomplex.com/zh-CN/posts/upload")
    other_page = FakePage({}, url="https://www.sankakucomplex.com/zh-CN/posts/abc")
    context = FakeContext([other_page, upload_page])
    client = _build_client()
    assert client._select_working_page(context) is upload_page


def test_close_extra_pages_closes_all_but_kept_page() -> None:
    keep = FakePage({})
    extra = FakePage({})
    context = FakeContext([keep, extra])
    client = _build_client()
    client._close_extra_pages(context, keep_page=keep)
    assert keep.closed is False
    assert extra.closed is True


def test_detect_tag_check_required_from_alert_text() -> None:
    page = FakePage({}, evaluate_result="需要检查标签")
    client = _build_client()
    alert_type, _alert_text = client._detect_page_alerts(page)
    assert alert_type == "tag_check_required"


def test_detect_tag_check_required_ignores_unrelated_text() -> None:
    page = FakePage({}, evaluate_result="上传完成，没有图片")
    client = _build_client()
    assert client._detect_page_alerts(page) == ("unknown", "上传完成，没有图片")


def test_extract_post_id_from_alert_text_rejects_short_tokens() -> None:
    client = _build_client()
    assert client._extract_post_id_from_alert_text("duplicate post id=as") == ""


def test_extract_post_id_from_alert_text_accepts_explicit_long_ids() -> None:
    client = _build_client()
    assert client._extract_post_id_from_alert_text("duplicate post id=abc1234") == "abc1234"


def test_upload_items_uses_concurrent_path_for_normal_batches(monkeypatch) -> None:
    client = _build_client()
    calls: list[int] = []

    monkeypatch.setattr(
        client,
        "_upload_normal_batch_concurrent",
        lambda _context, items, _callback: calls.append(len(items)) or [],
    )

    class FakeChromium:
        def launch_persistent_context(self, **_kwargs):
            return FakeContext([FakePage({})])

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("sankaku_uploader.infrastructure.automation.sync_playwright", lambda: FakePlaywright())
    items = [
        SimpleNamespace(item_id="1", file_name="a.png", file_path="a.png"),
        SimpleNamespace(item_id="2", file_name="b.png", file_path="b.png"),
    ]
    client.upload_items(items, diff_mode=False)
    assert calls == [2]
