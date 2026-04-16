from pathlib import Path

from sankaku_uploader.infrastructure.automation import AutomationConfig, SankakuAutomationClient


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
    def __init__(self, counts: dict[str, int]):
        self.counts = counts
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.presses: list[tuple[str, str]] = []

    def locator(self, selector: str):
        normalized = selector
        if selector == "button":
            # not needed for these tests
            return FakeLocator(self, selector)
        return FakeLocator(self, normalized)


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
