from pathlib import Path

from sankaku_uploader.infrastructure.automation import AutomationConfig, SankakuAutomationClient, wait_for_ai_tags


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


class FakePage:
    def __init__(self, counts: dict[str, int], url: str = "https://example.com/upload"):
        self.counts = counts
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.presses: list[tuple[str, str]] = []
        self.url = url

    def locator(self, selector: str):
        normalized = selector
        if selector == "button":
            # not needed for these tests
            return FakeLocator(self, selector)
        return FakeLocator(self, normalized)


class FakeContext:
    def __init__(self, pages):
        self.pages = pages


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
    ready, reason = client._wait_until_upload_surface_ready(page)
    assert ready is False
    assert "login required" in reason


def test_wait_until_upload_surface_ready_detects_otp_inputs() -> None:
    page = FakePage({"input[role='spinbutton']": 6})
    client = _build_client()
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
