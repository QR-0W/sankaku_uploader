from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import time
from typing import Callable, Iterable, Literal, Protocol

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from sankaku_uploader.domain import UploadItem

TagDecision = Literal["confirm", "skip", "retry"]
ReviewDecisionProvider = Callable[[UploadItem, list[str], bool], TagDecision]

FILE_INPUT_SELECTORS: tuple[str, ...] = (
    "input[type='file']",
    "input[type=file]",
    "form input[type='file']",
    "form input[type=file]",
    "input[accept]",
)

SUBMIT_SELECTORS: tuple[str, ...] = (
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Upload')",
    "button:has-text('上传')",
    "button:has-text('Create post')",
    "button:has-text('创建帖子')",
    "[data-test='create-post']",
)

AI_TAG_SELECTORS: tuple[str, ...] = (
    "#ai-tags",
    "[data-testid='ai-tags']",
    "[data-role='ai-tags']",
    ".ai-tags",
    ".tag-list",
    "#tags",
)

PARENT_ID_SELECTORS: tuple[str, ...] = (
    "input[name='parent']",
    "input[name='parent_id']",
    "input[name='parentId']",
    "input[placeholder*='parent' i]",
)

PASSWORD_SELECTORS: tuple[str, ...] = (
    "input[type='password']",
    "input[name='password']",
)

AUTH_HINT_SELECTORS: tuple[str, ...] = (
    "input[name='email']",
    "input[type='password']",
    "input[name='password']",
    "input[role='spinbutton']",
    "input[inputmode='numeric']",
)

OVERLAY_CLOSE_SELECTORS: tuple[str, ...] = (
    "[data-testid='incognito-warning-close-button']",
    "[data-test='btn-end-onboarding']",
    "button:has-text('关闭')",
    "button:has-text('Close')",
)

TAG_INPUT_SELECTORS: tuple[str, ...] = (
    "#autocomplete",
    "input[placeholder*='tag' i]",
    "input[name='tags']",
)


@dataclass(slots=True)
class AutomationConfig:
    upload_url: str
    profile_dir: Path
    browser_channel: str = "msedge"
    headless: bool = False
    poll_interval_seconds: float = 0.5
    ai_timeout_seconds: float = 20.0
    submit_timeout_seconds: float = 60.0
    confirmation_timeout_seconds: float = 1800.0
    run_mode: str = "manual_assist"  # manual_assist | auto_submit


@dataclass(slots=True)
class AutomationUploadResult:
    item_id: str
    success: bool
    ai_tags: list[str] = field(default_factory=list)
    post_id: str = ""
    uploaded_url: str = ""
    tag_state: str = "ok"
    error: str = ""


class LocatorLike(Protocol):
    def count(self) -> int: ...

    @property
    def first(self): ...


def extract_post_id(url: str) -> str:
    if not url:
        return ""
    matched = re.search(r"/posts/([A-Za-z0-9_-]+)", url)
    if not matched:
        return ""
    post_id = matched.group(1)
    if post_id.lower() in {"upload", "create"}:
        return ""
    return post_id


def extract_ai_tags(page, selectors: Iterable[str] = AI_TAG_SELECTORS) -> list[str]:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() <= 0:
                continue
            text = locator.first.text_content() or ""
        except Exception:
            continue
        tags = [part.strip() for part in re.split(r"[\n,;]+", text) if part.strip()]
        if tags:
            return tags
    return []


def wait_for_ai_tags(page, timeout_seconds: float, poll_interval_seconds: float) -> tuple[list[str], bool]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        tags = extract_ai_tags(page)
        if tags:
            return tags, True
        time.sleep(poll_interval_seconds)
    return [], False


def find_first_locator(page, selectors: tuple[str, ...]):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue
    return None


def find_button_by_text(page, candidates: tuple[str, ...]):
    normalized_candidates = [re.sub(r"\s+", "", candidate).lower() for candidate in candidates]
    try:
        buttons = page.locator("button")
        for idx in range(buttons.count()):
            loc = buttons.nth(idx)
            try:
                text = re.sub(r"\s+", "", loc.inner_text()).lower()
            except Exception:
                continue
            if any(candidate in text for candidate in normalized_candidates) and loc.is_enabled():
                return loc
    except Exception:
        return None
    return None


class SankakuAutomationClient:
    def __init__(self, config: AutomationConfig, review_decision_provider: ReviewDecisionProvider | None = None) -> None:
        self.config = config
        self.review_decision_provider = review_decision_provider

    def upload_items(self, items: list[UploadItem], *, diff_mode: bool = False) -> list[AutomationUploadResult]:
        results: list[AutomationUploadResult] = []
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            launch_kwargs = {
                "user_data_dir": str(self.config.profile_dir),
                "headless": self.config.headless,
            }
            if self.config.browser_channel:
                launch_kwargs["channel"] = self.config.browser_channel
            context = p.chromium.launch_persistent_context(**launch_kwargs)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                root_post_id = ""
                for idx, item in enumerate(items):
                    page.goto(self.config.upload_url, wait_until="domcontentloaded")
                    self._dismiss_common_overlays(page)
                    ready, reason = self._wait_until_upload_surface_ready(page)
                    if not ready:
                        results.append(
                            AutomationUploadResult(
                                item_id=item.item_id,
                                success=False,
                                tag_state="failed",
                                error=reason,
                            )
                        )
                        continue
                    parent_post_id = root_post_id if diff_mode and idx > 0 else ""
                    result = self._upload_one(page, item, parent_post_id=parent_post_id)
                    results.append(result)
                    if diff_mode and idx == 0 and result.post_id:
                        root_post_id = result.post_id
                    if diff_mode and idx > 0 and not root_post_id:
                        result.success = False
                        result.error = "root post id missing in diff mode"
            finally:
                context.close()
        return results

    def _upload_one(self, page, item: UploadItem, *, parent_post_id: str) -> AutomationUploadResult:
        try:
            self._dismiss_common_overlays(page)
            selected_by = self._select_file(page, Path(item.file_path))
            if not selected_by:
                return AutomationUploadResult(item_id=item.item_id, success=False, tag_state="failed", error="cannot set file")

            if parent_post_id:
                self._ensure_advanced_panel_open(page)
                parent_input = find_first_locator(page, PARENT_ID_SELECTORS)
                if parent_input is not None:
                    parent_input.fill(parent_post_id)

            tags, available = wait_for_ai_tags(
                page,
                timeout_seconds=self.config.ai_timeout_seconds,
                poll_interval_seconds=self.config.poll_interval_seconds,
            )

            if self.config.run_mode == "manual_assist":
                uploaded_url, post_id = self._wait_for_uploaded_post(page)
                if not post_id:
                    return AutomationUploadResult(
                        item_id=item.item_id,
                        success=False,
                        ai_tags=tags,
                        tag_state="failed",
                        uploaded_url=uploaded_url,
                        error="manual upload not completed in time",
                    )
                return AutomationUploadResult(
                    item_id=item.item_id,
                    success=True,
                    ai_tags=tags,
                    tag_state="ok" if available else "unavailable",
                    post_id=post_id,
                    uploaded_url=uploaded_url,
                )

            decision = self._review_decision(item, tags, available)
            if decision == "skip":
                return AutomationUploadResult(item_id=item.item_id, success=False, ai_tags=tags, tag_state="skipped", error="skipped by user")
            if decision == "retry":
                return AutomationUploadResult(item_id=item.item_id, success=False, ai_tags=tags, tag_state="failed", error="retry requested")

            submit = self._wait_for_submit(page)
            if submit is None:
                return AutomationUploadResult(item_id=item.item_id, success=False, ai_tags=tags, tag_state="failed", error="submit button unavailable")
            submit.click()
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            uploaded_url, post_id = self._wait_for_uploaded_post(page, timeout_seconds=15.0)
            if not post_id:
                tag_fix = self._try_apply_minimum_tag(page, tags)
                if tag_fix:
                    submit_retry = self._wait_for_submit(page)
                    if submit_retry is not None:
                        submit_retry.click()
                        try:
                            page.wait_for_load_state("networkidle", timeout=10_000)
                        except PlaywrightTimeoutError:
                            pass
                        uploaded_url, post_id = self._wait_for_uploaded_post(page, timeout_seconds=15.0)
                if not post_id:
                    return AutomationUploadResult(
                        item_id=item.item_id,
                        success=False,
                        ai_tags=tags,
                        tag_state="failed",
                        uploaded_url=uploaded_url,
                        error="submit completed but no post id detected; site may require manual tag selection/edit before posting",
                    )
            return AutomationUploadResult(
                item_id=item.item_id,
                success=True,
                ai_tags=tags,
                tag_state="ok" if available else "unavailable",
                uploaded_url=uploaded_url,
                post_id=post_id,
            )
        except Exception as exc:
            return AutomationUploadResult(item_id=item.item_id, success=False, tag_state="failed", error=str(exc))

    def _wait_until_upload_surface_ready(self, page) -> tuple[bool, str]:
        deadline = time.monotonic() + self.config.submit_timeout_seconds
        while time.monotonic() < deadline:
            self._dismiss_common_overlays(page)

            if self._selector_count(page, FILE_INPUT_SELECTORS) > 0:
                return True, ""

            if self._selector_count(page, AUTH_HINT_SELECTORS) > 0:
                return False, "login required before upload (auth/2FA screen detected)"

            time.sleep(self.config.poll_interval_seconds)

        return False, "upload surface not ready (file input not found within timeout)"

    def _dismiss_common_overlays(self, page) -> None:
        for selector in OVERLAY_CLOSE_SELECTORS:
            try:
                locator = page.locator(selector)
                if locator.count() <= 0:
                    continue
                button = locator.first
                if button.is_visible() and button.is_enabled():
                    button.click(timeout=500)
            except Exception:
                continue

    def _ensure_advanced_panel_open(self, page) -> None:
        parent_input = find_first_locator(page, PARENT_ID_SELECTORS)
        if parent_input is not None:
            return
        advanced = find_button_by_text(page, ("advanced", "高级", "高级选项"))
        if advanced is None:
            return
        try:
            advanced.click()
        except Exception:
            return

    def _try_apply_minimum_tag(self, page, tags: list[str]) -> bool:
        if not tags:
            return False
        tag_input = find_first_locator(page, TAG_INPUT_SELECTORS)
        if tag_input is None:
            return False
        candidate = str(tags[0]).strip()
        if not candidate:
            return False
        try:
            tag_input.click()
        except Exception:
            pass
        try:
            tag_input.fill(candidate)
            tag_input.press("Enter")
            time.sleep(self.config.poll_interval_seconds)
            return True
        except Exception:
            return False

    @staticmethod
    def _selector_count(page, selectors: tuple[str, ...]) -> int:
        count = 0
        for selector in selectors:
            try:
                count += max(page.locator(selector).count(), 0)
            except Exception:
                continue
        return count

    def _select_file(self, page, file_path: Path) -> str:
        upload_button = find_button_by_text(page, ("上传文件", "Upload file", "Choose file", "选择文件"))
        if upload_button is not None:
            try:
                with page.expect_file_chooser(timeout=3000) as chooser_info:
                    upload_button.click()
                chooser_info.value.set_files(str(file_path))
                return "file_chooser"
            except Exception:
                pass

        file_input = find_first_locator(page, FILE_INPUT_SELECTORS)
        if file_input is not None:
            file_input.set_input_files(str(file_path))
            return "input_file"
        return ""

    def _wait_for_submit(self, page):
        deadline = time.monotonic() + self.config.submit_timeout_seconds
        while time.monotonic() < deadline:
            submit = find_button_by_text(page, ("创建帖子", "Create post", "提交", "Submit", "上传"))
            if submit is None:
                submit = find_first_locator(page, SUBMIT_SELECTORS)
            if submit is not None:
                try:
                    if submit.is_enabled():
                        return submit
                except Exception:
                    pass
            time.sleep(self.config.poll_interval_seconds)
        return None

    def _wait_for_uploaded_post(self, page, timeout_seconds: float | None = None) -> tuple[str, str]:
        timeout = self.config.confirmation_timeout_seconds if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + timeout
        last_url = self._safe_url(page)
        while time.monotonic() < deadline:
            last_url = self._safe_url(page)
            post_id = extract_post_id(last_url)
            if post_id:
                return last_url, post_id
            time.sleep(self.config.poll_interval_seconds)
        return last_url, ""

    def _review_decision(self, item: UploadItem, tags: list[str], available: bool) -> TagDecision:
        if self.review_decision_provider is None:
            return "confirm"
        decision = self.review_decision_provider(item, tags, available)
        if decision in {"confirm", "skip", "retry"}:
            return decision
        return "confirm"

    @staticmethod
    def _safe_url(page) -> str:
        try:
            return str(page.url)
        except Exception:
            return ""
