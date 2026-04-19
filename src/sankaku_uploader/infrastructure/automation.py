from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from datetime import UTC, datetime
import re
import time
from typing import Any, Callable, Iterable, Literal, Protocol

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from sankaku_uploader.domain import UploadItem

TagDecision = Literal["confirm", "skip", "retry", "sync", "wait"]
ReviewRequestMode = Literal["probe", "decide"]


@dataclass(slots=True)
class ReviewDecision:
    action: TagDecision
    tags_override: list[str] | None = None
    pending_syncs: list[dict[str, Any]] = field(default_factory=list)


ReviewDecisionProvider = Callable[
    [UploadItem, list[str], bool, ReviewRequestMode],
    ReviewDecision | TagDecision | None,
]
TraceHook = Callable[[str], None]

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
    ai_timeout_seconds: float = 45.0
    submit_timeout_seconds: float = 60.0
    confirmation_timeout_seconds: float = 1800.0
    run_mode: str = "manual_assist"  # manual_assist | auto_submit
    debug_dir: Path | None = None
    max_concurrent_pages: int = 8
    proxy_server: str = ""


@dataclass(slots=True)
class AutomationUploadResult:
    item_id: str
    success: bool
    ai_tags: list[str] = field(default_factory=list)
    post_id: str = ""
    uploaded_url: str = ""
    tag_state: str = "ok"
    error: str = ""
    is_duplicate: bool = False


@dataclass(slots=True)
class PreparedUpload:
    item: UploadItem
    page: object
    known_post_ids: set[str]
    tags: list[str] = field(default_factory=list)
    available: bool = False
    response_post_ids: list[str] = field(default_factory=list)
    response_handler: object | None = None
    error: str = ""
    prepare_time: float = 0.0  # monotonic timestamp when file was selected


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
    # Block known UI route segments that are not real post IDs
    _BLOCKED_URL_SEGMENTS = {
        "upload", "create", "keyset", "new", "edit",
        "index", "search", "random", "settings", "help",
        "popular", "hot", "recommended",
    }
    if post_id.lower() in _BLOCKED_URL_SEGMENTS:
        return ""
    # Real post IDs are typically 8-24 alphanumeric characters.
    # Reject anything with a hyphen/underscore or outside that range.
    if not re.fullmatch(r"[A-Za-z0-9]{5,32}", post_id):
        return ""
    return post_id


def extract_ai_tags(page, selectors: Iterable[str] = AI_TAG_SELECTORS) -> list[str]:
    editor_tags, tagging_in_progress = _extract_tags_from_editor_section(page)
    if editor_tags:
        return editor_tags
    if tagging_in_progress:
        return []

    dom_tags = _extract_ai_tags_from_dom(page, selectors)
    if dom_tags:
        return dom_tags

    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() <= 0:
                continue
            text = locator.first.text_content() or ""
        except Exception:
            continue
        tags = _normalize_tags([part.strip() for part in re.split(r"[\n,;]+", text) if part.strip()])
        if tags:
            return tags

    button_tags = _extract_ai_tags_from_button_controls(page)
    if button_tags:
        return button_tags
    return []


def _extract_ai_tags_from_dom(page, selectors: Iterable[str]) -> list[str]:
    selector_list = list(selectors)
    try:
        raw_tags = page.evaluate(
            """
            (selectors) => {
              const clean = (text) =>
                String(text || "")
                  .replace(/\\s+/g, " ")
                  .trim();

              const tags = [];
              for (const selector of selectors) {
                const roots = Array.from(document.querySelectorAll(selector));
                for (const root of roots) {
                  const chips = root.querySelectorAll("a, button, span, li, .tag, [data-tag]");
                  if (chips.length > 0) {
                    for (const chip of chips) {
                      const txt = clean(chip.textContent);
                      if (txt) tags.push(txt);
                    }
                  } else {
                    const txt = clean(root.textContent);
                    if (txt) tags.push(...txt.split(/[\\n,;]+/g).map((x) => clean(x)));
                  }
                }
              }
              return tags.filter(Boolean);
            }
            """,
            selector_list,
        )
    except Exception:
        return []
    if not isinstance(raw_tags, list):
        return []
    return _normalize_tags([str(x) for x in raw_tags if str(x).strip()])


def _normalize_tags(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = str(value).strip().replace(" ", "_")
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
    return normalized


def _extract_ai_tags_from_button_controls(page) -> list[str]:
    try:
        raw_tags = page.evaluate(
            """
            () => {
              const clean = (text) =>
                String(text || "")
                  .replace(/\\s+/g, " ")
                  .trim();

              const blocked = new Set([
                "clear metadata",
                "create post",
                "advanced",
                "upload",
                "submit",
                "close",
              ]);

              const tags = [];
              for (const node of document.querySelectorAll("button, [role='button'], a")) {
                const text = clean(node.textContent || node.getAttribute("aria-label") || node.getAttribute("title"));
                if (!text) continue;
                if (blocked.has(text.toLowerCase())) continue;
                tags.push(text);
              }
              return tags;
            }
            """
        )
    except Exception:
        return []
    if not isinstance(raw_tags, list):
        return []
    tags = _normalize_tags([str(x) for x in raw_tags if str(x).strip()])
    blocked_controls = {
        "clear metadata",
        "create post",
        "advanced",
        "upload",
        "submit",
        "close",
    }
    filtered: list[str] = []
    for tag in tags:
        if tag.lower().replace("_", " ") in blocked_controls:
            continue
        filtered.append(tag)
    return filtered


def _extract_tags_from_editor_section(page) -> tuple[list[str], bool]:
    try:
        result = page.evaluate(
            """
            () => {
              const clean = (value) =>
                String(value || "")
                  .replace(/\\s+/g, " ")
                  .trim();

              const input =
                document.querySelector("#autocomplete") ||
                document.querySelector("input[name='tags']") ||
                document.querySelector("input[placeholder*='标签']") ||
                document.querySelector("input[placeholder*='tag' i]");
              if (!input) {
                return { tags: [], inProgress: false };
              }

              const section =
                input.closest(".MuiGrid-root") ||
                input.closest("form") ||
                input.parentElement;
              if (!section) {
                return { tags: [], inProgress: false };
              }

              const inProgress = Boolean(
                section.querySelector(".MuiLinearProgress-indeterminate,[role='progressbar']")
              );

              const candidates = [];
              const chipLike = section.querySelectorAll(
                ".MuiChip-root .MuiChip-label, .MuiChip-label, [class*='MuiChip-label'], .MuiAutocomplete-tag, [data-tag], [data-testid*='tag' i], [data-tag-index]"
              );
              for (const node of chipLike) {
                const text = clean(node.getAttribute("data-tag") || node.getAttribute("title") || node.textContent);
                if (text) candidates.push(text);
              }

              return { tags: candidates, inProgress };
            }
            """
        )
    except Exception:
        return [], False

    if not isinstance(result, dict):
        return [], False
    raw_tags = result.get("tags")
    in_progress = bool(result.get("inProgress", False))
    if not isinstance(raw_tags, list):
        return [], in_progress

    blocked_controls = {
        "清除元数据",
        "clear metadata",
        "创建帖子",
        "取消自动标记",
        "advanced",
        "高级",
        "提交",
        "upload",
        "create post",
    }
    blocked_controls_lower = {value.lower() for value in blocked_controls}
    filtered = []
    for value in raw_tags:
        tag = str(value).strip()
        if not tag:
            continue
        if tag.lower() in blocked_controls_lower:
            continue
        if tag in {"R15+", "R18+"}:
            continue
        filtered.append(tag)
    return _normalize_tags(filtered), in_progress





def wait_for_ai_tags(page, timeout_seconds: float, poll_interval_seconds: float) -> tuple[list[str], bool]:
    base_deadline = time.monotonic() + timeout_seconds
    extended_deadline = base_deadline + max(timeout_seconds * 2.0, 60.0)
    while time.monotonic() < extended_deadline:
        editor_tags, in_progress = _extract_tags_from_editor_section(page)
        if editor_tags:
            return editor_tags, True

        if not in_progress:
            tags = extract_ai_tags(page)
            if tags:
                return tags, True

        if time.monotonic() >= base_deadline and not in_progress:
            break
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
        # Search for buttons, links, and divs with role=button
        locators = page.locator("button, a, [role='button']")
        for idx in range(locators.count()):
            loc = locators.nth(idx)
            try:
                # Check inner text
                text = re.sub(r"\s+", "", loc.inner_text()).lower()
                # Check aria-label and title
                aria_label = (loc.get_attribute("aria-label") or "").lower().strip()
                title = (loc.get_attribute("title") or "").lower().strip()

                if any(candidate in text or candidate in aria_label or candidate in title
                       for candidate in normalized_candidates) and loc.is_enabled():
                    return loc
            except Exception:
                continue
    except Exception:
        return None
    return None


class SankakuAutomationClient:
    def __init__(
        self,
        config: AutomationConfig,
        review_decision_provider: ReviewDecisionProvider | None = None,
        trace_hook: TraceHook | None = None,
    ) -> None:
        self.config = config
        self.review_decision_provider = review_decision_provider
        self.trace_hook = trace_hook

    def _trace(self, message: str) -> None:
        if self.trace_hook is None:
            return
        try:
            self.trace_hook(message)
        except Exception:
            return

    def upload_items(
        self,
        items: list[UploadItem],
        *,
        diff_mode: bool = False,
        manual_root_post_id: str = "",
        item_result_callback: Callable[[AutomationUploadResult], None] | None = None,
    ) -> list[AutomationUploadResult]:
        results: list[AutomationUploadResult] = []
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        if self.config.debug_dir is not None:
            self.config.debug_dir.mkdir(parents=True, exist_ok=True)
        self._trace(
            f"automation start: items={len(items)} diff_mode={diff_mode} headless={self.config.headless} "
            f"channel={self.config.browser_channel} proxy={self.config.proxy_server} profile_dir={self.config.profile_dir}"
        )

        with sync_playwright() as p:
            launch_kwargs = {
                "user_data_dir": str(self.config.profile_dir),
                "headless": self.config.headless,
                "locale": "en-US",
                "args": ["--lang=en-US", "--accept-lang=en-US"],
            }
            if self.config.browser_channel:
                launch_kwargs["channel"] = self.config.browser_channel
            if self.config.proxy_server:
                launch_kwargs["proxy"] = {"server": self.config.proxy_server}
            context = p.chromium.launch_persistent_context(**launch_kwargs)
            self._force_english_settings(context)
            try:
                if diff_mode and len(items) > 1:
                    return self._upload_diff_group_concurrent(
                        context,
                        items,
                        item_result_callback,
                        manual_root_post_id=manual_root_post_id,
                    )
                if not diff_mode and len(items) > 1:
                    return self._upload_normal_batch_concurrent(context, items, item_result_callback)

                page = self._select_working_page(context)
                self._close_extra_pages(context, keep_page=page)
                # Use the user-supplied root post id if provided; otherwise derive
                # it from the first uploaded item in the batch.
                root_post_id = manual_root_post_id.strip()
                if root_post_id:
                    self._trace(f"diff mode: using manual root_post_id={root_post_id}")
                for idx, item in enumerate(items):
                    self._trace(f"[{idx+1}/{len(items)}] open upload page for item={item.file_name} item_id={item.item_id}")
                    page.goto(self.config.upload_url, wait_until="domcontentloaded")
                    self._dismiss_common_overlays(page)
                    ready, reason = self._wait_until_upload_surface_ready(page)
                    if not ready:
                        self._trace(f"upload surface not ready for {item.file_name}: {reason}")
                        results.append(
                            AutomationUploadResult(
                                item_id=item.item_id,
                                success=False,
                                tag_state="failed",
                                error=reason,
                            )
                        )
                        continue
                    known_post_ids = self._collect_known_post_ids(context)

                    # Determine parent post ID for this item in diff mode:
                    # - Item 0 is always the root (no parent).
                    # - Others use root_post_id (which could be the passed-in manual ID or derived from item 0).
                    item_parent_id = ""
                    if diff_mode:
                        if item.order_index == 0:
                            item_parent_id = ""
                        else:
                            item_parent_id = root_post_id

                    if item_parent_id:
                        self._trace(f"{item.file_name}: using parent_post_id={item_parent_id}")

                    result = self._upload_one(
                        page,
                        context,
                        item,
                        parent_post_id=item_parent_id,
                        known_post_ids=known_post_ids,
                    )
                    results.append(result)
                    if item_result_callback:
                        try:
                            item_result_callback(result)
                        except Exception:
                            pass

                    # If this was item 0 and it succeeded, establish it as the root for the rest of this session
                    if diff_mode and item.order_index == 0 and result.success and result.post_id:
                        root_post_id = result.post_id
                        self._trace(f"root post id established from upload (order_index 0): {root_post_id}")
                    elif diff_mode and item.order_index > 0 and not root_post_id:
                        # Should normally not happen if the runner passes the established root ID
                        result.success = False
                        result.error = "root post id missing in diff mode"
                    self._close_extra_pages(context, keep_page=page)
            finally:
                context.close()
        return results

    def _upload_normal_batch_concurrent(
        self,
        context,
        items: list[UploadItem],
        item_result_callback: Callable[[AutomationUploadResult], None] | None,
    ) -> list[AutomationUploadResult]:
        results: list[AutomationUploadResult] = []
        chunk_size = max(1, min(int(self.config.max_concurrent_pages or 1), len(items)))
        self._trace(f"normal batch concurrent mode: pages={chunk_size} items={len(items)}")

        for start in range(0, len(items), chunk_size):
            chunk = items[start : start + chunk_size]
            prepared: list[PreparedUpload] = []
            keep_page = self._select_working_page(context)
            self._close_extra_pages(context, keep_page=keep_page)

            for offset, item in enumerate(chunk):
                page = keep_page if offset == 0 else context.new_page()
                self._trace(
                    f"[{start+offset+1}/{len(items)}] prepare upload page for item={item.file_name} item_id={item.item_id}"
                )
                prepared_upload = self._prepare_upload_page(page, context, item)
                prepared.append(prepared_upload)

                # Stagger requests to prevent "extreme volume" or "please slowdown" anti-bot rate limits
                if offset < len(chunk) - 1:
                    import random
                    stagger_delay = random.uniform(2.0, 4.0)
                    self._trace(f"staggering next request by {stagger_delay:.1f}s to avoid rate limits...")
                    time.sleep(stagger_delay)

            self._wait_for_prepared_tags(prepared)

            for prepared_upload in prepared:
                if prepared_upload.error:
                    res = AutomationUploadResult(
                        item_id=prepared_upload.item.item_id,
                        success=False,
                        tag_state="failed",
                        error=prepared_upload.error,
                    )
                    results.append(res)
                    if item_result_callback:
                        try:
                            item_result_callback(res)
                        except Exception:
                            pass
                    self._detach_prepared_upload(prepared_upload)
                    continue

                # Create review cards for all files as soon as their tags are ready.
                # This is a passive probe; it must never consume queued decisions.
                if self.review_decision_provider is not None:
                    self._trace(
                        f"{prepared_upload.item.file_name}: review_request mode=probe context=prepared-card "
                        f"item_id={prepared_upload.item.item_id}"
                    )
                    self.review_decision_provider(
                        prepared_upload.item,
                        list(prepared_upload.tags),
                        prepared_upload.available,
                        "probe",
                    )

            for prepared_upload in prepared:
                if prepared_upload.error:
                    continue
                # Refresh known_post_ids right before each submit so that
                # post IDs created by earlier submissions in this chunk are
                # excluded from the detection of the current item's post ID.
                prepared_upload.known_post_ids = self._collect_known_post_ids(context)
                result = self._review_and_submit_prepared(prepared_upload, context, all_prepared=prepared)
                results.append(result)
                if item_result_callback:
                    try:
                        item_result_callback(result)
                    except Exception:
                        pass
                self._detach_prepared_upload(prepared_upload)

            self._close_extra_pages(context, keep_page=keep_page)

        return results

    def _upload_diff_group_concurrent(
        self,
        context,
        items: list[UploadItem],
        item_result_callback: Callable[[AutomationUploadResult], None] | None,
        *,
        manual_root_post_id: str = "",
    ) -> list[AutomationUploadResult]:
        results: list[AutomationUploadResult] = []
        chunk_size = max(1, min(int(self.config.max_concurrent_pages or 1), len(items)))
        self._trace(f"diff group concurrent prep mode: pages={chunk_size} items={len(items)}")

        root_post_id = manual_root_post_id.strip()
        if root_post_id:
            self._trace(f"diff mode: using manual root_post_id={root_post_id}")
        root_failed = False

        for start in range(0, len(items), chunk_size):
            chunk = items[start : start + chunk_size]
            prepared: list[PreparedUpload] = []
            keep_page = self._select_working_page(context)
            self._close_extra_pages(context, keep_page=keep_page)

            for offset, item in enumerate(chunk):
                page = keep_page if offset == 0 else context.new_page()
                self._trace(
                    f"[{start+offset+1}/{len(items)}] prepare upload page for item={item.file_name} item_id={item.item_id}"
                )
                prepared_upload = self._prepare_upload_page(page, context, item)
                prepared.append(prepared_upload)

                if offset < len(chunk) - 1:
                    import random

                    stagger_delay = random.uniform(2.0, 4.0)
                    self._trace(f"staggering next request by {stagger_delay:.1f}s to avoid rate limits...")
                    time.sleep(stagger_delay)

            self._wait_for_prepared_tags(prepared)

            for prepared_upload in prepared:
                if prepared_upload.error:
                    res = AutomationUploadResult(
                        item_id=prepared_upload.item.item_id,
                        success=False,
                        tag_state="failed",
                        error=prepared_upload.error,
                    )
                    results.append(res)
                    if item_result_callback:
                        try:
                            item_result_callback(res)
                        except Exception:
                            pass
                    self._detach_prepared_upload(prepared_upload)
                    continue

                if self.review_decision_provider is not None:
                    self._trace(
                        f"{prepared_upload.item.file_name}: review_request context=prepared-card "
                        f"item_id={prepared_upload.item.item_id}"
                    )
                    self.review_decision_provider(prepared_upload.item, list(prepared_upload.tags), prepared_upload.available)

            for prepared_upload in prepared:
                if prepared_upload.error:
                    continue

                prepared_upload.known_post_ids = self._collect_known_post_ids(context)

                if root_failed and prepared_upload.item.order_index > 0:
                    res = AutomationUploadResult(
                        item_id=prepared_upload.item.item_id,
                        success=False,
                        tag_state="failed",
                        error="root post id missing in diff mode",
                    )
                    results.append(res)
                    if item_result_callback:
                        try:
                            item_result_callback(res)
                        except Exception:
                            pass
                    self._detach_prepared_upload(prepared_upload)
                    continue

                parent_post_id = root_post_id if prepared_upload.item.order_index > 0 else ""
                result = self._review_and_submit_prepared(
                    prepared_upload,
                    context,
                    all_prepared=prepared,
                    parent_post_id=parent_post_id,
                )
                results.append(result)
                if item_result_callback:
                    try:
                        item_result_callback(result)
                    except Exception:
                        pass

                if prepared_upload.item.order_index == 0:
                    if result.success and result.post_id:
                        root_post_id = result.post_id
                        self._trace(f"root post id established from upload (order_index 0): {root_post_id}")
                    else:
                        root_failed = True
                elif not root_post_id:
                    root_failed = True
                self._detach_prepared_upload(prepared_upload)

            self._close_extra_pages(context, keep_page=keep_page)

        return results

    def _prepare_upload_page(self, page, context, item: UploadItem) -> PreparedUpload:
        known_post_ids = self._collect_known_post_ids(context)
        response_post_ids, response_handler = self._attach_response_capture(page, item, known_post_ids)
        prepared = PreparedUpload(
            item=item,
            page=page,
            known_post_ids=known_post_ids,
            response_post_ids=response_post_ids,
            response_handler=response_handler,
        )
        try:
            # Robust retry loop: handle transient VPN/Proxy HTTP/2 or Cloudflare ERR_EMPTY_RESPONSE limits
            # when massive background payloads (like long GIFs) overlap with a new page.goto negotiation.
            goto_success = False
            last_err = None
            for attempt in range(3):
                try:
                    page.goto(self.config.upload_url, wait_until="domcontentloaded", timeout=30000)
                    goto_success = True
                    break
                except Exception as e:
                    last_err = e
                    self._trace(f"{item.file_name}: goto failed (attempt {attempt+1}/3): {e}, retrying in 2s...")
                    time.sleep(2.0)

            if not goto_success:
                raise RuntimeError(f"failed to load upload page after 3 attempts: {last_err}")

            self._dismiss_common_overlays(page)
            ready, reason = self._wait_until_upload_surface_ready(page)
            if not ready:
                prepared.error = reason
                self._trace(f"upload surface not ready for {item.file_name}: {reason}")
                return prepared

            selected_by = self._select_file(page, Path(item.file_path))
            if not selected_by:
                prepared.error = "cannot set file"
                self._trace(f"{item.file_name}: file selection failed")
                return prepared
            self._trace(f"{item.file_name}: file selected via {selected_by}")
            # Give the SPA time to register the file and start the AI tagging
            # pipeline before we begin polling for tags.
            time.sleep(1.0)
            prepared.prepare_time = time.monotonic()
        except Exception as exc:
            prepared.error = str(exc)
            self._trace(f"{item.file_name}: prepare exception: {exc}")
        return prepared

    def _wait_for_prepared_tags(self, prepared_uploads: list[PreparedUpload]) -> None:
        pending = [item for item in prepared_uploads if not item.error]

        # Wake up background tabs to prevent Chromium from throttling AI tag requests
        if len(pending) > 1:
            for prepared in pending:
                try:
                    prepared.page.bring_to_front()
                except Exception:
                    pass
            # Restore the first page back to front
            try:
                pending[0].page.bring_to_front()
            except Exception:
                pass

        deadline = time.monotonic() + self.config.ai_timeout_seconds + max(self.config.ai_timeout_seconds * 2.0, 60.0)
        # Per-item grace period: do not give up on a page until at least this
        # many seconds have elapsed since file selection (prepare_time).  The
        # SPA may need a few seconds to even *start* the AI tagging pipeline,
        # during which _extract_tags_from_editor_section returns
        # in_progress=False, tags=[] — which previously caused the page to be
        # prematurely abandoned.
        grace_seconds = 45.0

        while pending and time.monotonic() < deadline:
            still_pending: list[PreparedUpload] = []
            for prepared in pending:
                tags = extract_ai_tags(prepared.page)
                _, still_tagging = _extract_tags_from_editor_section(prepared.page)
                if tags:
                    prepared.tags = tags
                    prepared.available = True
                    self._trace(
                        f"{prepared.item.file_name}: tag detect available=True count={len(tags)} "
                        f"still_tagging={still_tagging} tags={tags[:8]}"
                    )
                    continue

                # Determine whether this page is still within its grace period.
                item_grace_remaining = (
                    (prepared.prepare_time + grace_seconds) - time.monotonic()
                    if prepared.prepare_time > 0
                    else 0.0
                )
                within_grace = item_grace_remaining > 0

                if still_tagging or within_grace:
                    # Either the SPA is actively tagging, or we haven't waited
                    # long enough for it to start — keep polling.
                    still_pending.append(prepared)
                else:
                    prepared.tags = []
                    prepared.available = False
                    self._trace(
                        f"{prepared.item.file_name}: tag detect available=False count=0 "
                        f"still_tagging=False within_grace=False tags=[]"
                    )
            pending = still_pending
            if pending:
                time.sleep(self.config.poll_interval_seconds)

        for prepared in pending:
            prepared.tags = []
            prepared.available = False
            self._trace(f"{prepared.item.file_name}: tag detect timed out in concurrent mode")
            self._trace_tag_surface(prepared.page, prepared.item.file_name)
            self._save_debug_artifact(prepared.page, prepared.item, reason="empty-tags")

    def _review_and_submit_prepared(
        self,
        prepared: PreparedUpload,
        context,
        *,
        all_prepared: list[PreparedUpload] | None = None,
        parent_post_id: str = "",
    ) -> AutomationUploadResult:
        try:
            return self._review_and_submit(
                prepared.page,
                context,
                prepared.item,
                tags=prepared.tags,
                available=prepared.available,
                known_post_ids=prepared.known_post_ids,
                response_post_ids=prepared.response_post_ids,
                parent_post_id=parent_post_id,
                all_prepared=all_prepared,
            )
        except Exception as exc:
            self._trace(f"{prepared.item.file_name}: exception during prepared submit: {exc}")
            self._save_debug_artifact(prepared.page, prepared.item, reason=f"exception-{type(exc).__name__}")
            return AutomationUploadResult(prepared.item.item_id, False, tag_state="failed", error=str(exc))

    def _detach_prepared_upload(self, prepared: PreparedUpload) -> None:
        if prepared.response_handler is not None:
            self._detach_response_listener(prepared.page, prepared.response_handler)

    def _upload_one(self, page, context, item: UploadItem, *, parent_post_id: str, known_post_ids: set[str]) -> AutomationUploadResult:
        response_post_ids, on_response = self._attach_response_capture(page, item, known_post_ids)
        try:
            self._dismiss_common_overlays(page)
            selected_by = self._select_file(page, Path(item.file_path))
            if not selected_by:
                self._trace(f"{item.file_name}: file selection failed")
                return AutomationUploadResult(item_id=item.item_id, success=False, tag_state="failed", error="cannot set file")
            self._trace(f"{item.file_name}: file selected via {selected_by}")

            if parent_post_id:
                self._fill_parent_id_robustly(page, parent_post_id)

            tags, available = wait_for_ai_tags(
                page,
                timeout_seconds=self.config.ai_timeout_seconds,
                poll_interval_seconds=self.config.poll_interval_seconds,
            )
            _, still_tagging = _extract_tags_from_editor_section(page)
            self._trace(
                f"{item.file_name}: tag detect available={available} count={len(tags)} "
                f"still_tagging={still_tagging} tags={tags[:8]}"
            )
            if not tags:
                self._trace_tag_surface(page, item.file_name)
                self._save_debug_artifact(page, item, reason="empty-tags")

            return self._review_and_submit(
                page,
                context,
                item,
                tags=tags,
                available=available,
                known_post_ids=known_post_ids,
                response_post_ids=response_post_ids,
                parent_post_id=parent_post_id,
            )
        except Exception as exc:
            self._trace(f"{item.file_name}: exception during upload: {exc}")
            self._save_debug_artifact(page, item, reason=f"exception-{type(exc).__name__}")
            return AutomationUploadResult(item_id=item.item_id, success=False, tag_state="failed", error=str(exc))
        finally:
            self._detach_response_listener(page, on_response)

    def _attach_response_capture(self, page, item: UploadItem, known_post_ids: set[str]):
        response_post_ids: list[str] = []

        def on_response(response) -> None:
            ids = self._extract_post_ids_from_response(response)
            for post_id in ids:
                if post_id in known_post_ids:
                    continue
                if post_id in response_post_ids:
                    continue
                response_post_ids.append(post_id)
                self._trace(f"{item.file_name}: post id captured from response={post_id}")

        page.on("response", on_response)
        return response_post_ids, on_response

    def _review_and_submit(
        self,
        page,
        context,
        item: UploadItem,
        *,
        tags: list[str],
        available: bool,
        known_post_ids: set[str],
        response_post_ids: list[str],
        parent_post_id: str = "",
        attempt: int = 0,
        all_prepared: list[PreparedUpload] | None = None,
    ) -> AutomationUploadResult:
            if self.config.run_mode == "manual_assist":
                uploaded_url, post_id = self._wait_for_uploaded_post(
                    page,
                    context=context,
                    ignore_post_ids=known_post_ids,
                )
                if not post_id and response_post_ids:
                    post_id = response_post_ids[-1]
                    uploaded_url = self._build_post_url(post_id)
                    self._trace(f"{item.file_name}: manual mode fallback to response post id={post_id}")
                if not post_id:
                    self._trace(f"{item.file_name}: manual assist timed out waiting post id, last_url={uploaded_url}")
                    self._save_debug_artifact(page, item, reason="manual-timeout-no-post-id")
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

            decision = self._review_decision(page, item, tags, available, all_prepared=all_prepared)
            self._trace(f"{item.file_name}: review decision={decision.action}")
            if decision.action == "skip":
                return AutomationUploadResult(item_id=item.item_id, success=False, ai_tags=tags, tag_state="skipped", error="skipped by user")
            if decision.action == "retry":
                return AutomationUploadResult(item_id=item.item_id, success=False, ai_tags=tags, tag_state="failed", error="retry requested")

            if decision.tags_override is not None:
                applied = self._apply_tags_override(page, decision.tags_override)
                tags = list(decision.tags_override)
                self._trace(
                    f"{item.file_name}: applied tags override count={len(tags)} success={applied}"
                )
            else:
                synced_tags = self._sync_tags_after_review(page, baseline_tags=tags)
                tags = synced_tags
                self._trace(f"{item.file_name}: synced edited tags count={len(tags)}")

            submit = self._wait_for_submit(page)
            if submit is None:
                self._trace(f"{item.file_name}: submit button unavailable")
                self._save_debug_artifact(page, item, reason="submit-unavailable")
                return AutomationUploadResult(item_id=item.item_id, success=False, ai_tags=tags, tag_state="failed", error="submit button unavailable")
            # Redundant check for parent ID just before clicking submit (especially for manual review mode)
            if parent_post_id:
                try:
                    current_val = page.locator("input[name='parent']").first.get_attribute("value") or ""
                    if current_val.strip() != parent_post_id.strip():
                        self._trace(f"{item.file_name}: parent ID went missing or changed, refilling before submit...")
                        self._fill_parent_id_robustly(page, parent_post_id)
                except Exception:
                    pass

            submit.click()
            self._trace(f"{item.file_name}: submit clicked")
            try:
                page.wait_for_load_state("networkidle", timeout=10_000)
            except PlaywrightTimeoutError:
                pass
            uploaded_url, post_id = self._wait_for_uploaded_post(
                page,
                context=context,
                timeout_seconds=15.0,
                ignore_post_ids=known_post_ids,
            )

            if post_id:
                # Give a tiny bit of time for the "Already exists" snackbar to appear after redirect
                time.sleep(0.5)

            # ALWAYS check for alerts, even if post_id was found, to catch duplicates that redirect
            alert_type, alert_text = self._detect_page_alerts(page)

            if alert_type == "duplicate":
                existing_id = post_id # Fallback to detected ID if extraction fails
                # Try to extract ID from alert text
                match_id = re.search(r"#( [A-Za-z0-9]{5,32}|[A-Za-z0-9]{5,32})", alert_text)
                if not match_id:
                    # Try searching for numeric ID or common post ID patterns in the text
                    match_id = re.search(r"(?:posts/|post #|post |#)(\w+)", alert_text, re.I)

                if match_id:
                    existing_id = match_id.group(1).strip()
                    self._trace(f"{item.file_name}: extracted existing post_id={existing_id} from alert")

                if not existing_id and post_id:
                    existing_id = post_id
                    self._trace(f"{item.file_name}: using redirected post_id={existing_id} as duplicate ID")

                self._trace(f"{item.file_name}: Sankaku reported file already exists (duplicate)")
                return AutomationUploadResult(
                    item_id=item.item_id,
                    success=True, # We consider it "success" because it's on the site
                    ai_tags=tags,
                    tag_state="duplicate",
                    post_id=existing_id,
                    uploaded_url=self._build_post_url(existing_id) if existing_id else "",
                    is_duplicate=True,
                )

            if not post_id:
                if alert_type == "tag_check_required" and attempt < 3:
                    self._trace(
                        f"{item.file_name}: Sankaku requires tag review before submit (attempt {attempt+1})"
                    )
                    # Keep the page alive and return to review state instead of treating this as an upload failure.
                    return self._review_and_submit(
                        page,
                        context,
                        item,
                        tags=tags,
                        available=False,
                        known_post_ids=known_post_ids,
                        response_post_ids=response_post_ids,
                        attempt=attempt + 1,
                        all_prepared=all_prepared,
                    )
                tag_fix = self._try_apply_minimum_tag(page, tags)
                self._trace(f"{item.file_name}: first submit got no post_id, tag_fix_applied={tag_fix}")
                if tag_fix:
                    submit_retry = self._wait_for_submit(page)
                    if submit_retry is not None:
                        submit_retry.click()
                        self._trace(f"{item.file_name}: retry submit clicked after tag fix")
                        try:
                            page.wait_for_load_state("networkidle", timeout=10_000)
                        except PlaywrightTimeoutError:
                            pass
                        uploaded_url, post_id = self._wait_for_uploaded_post(
                            page,
                            context=context,
                            timeout_seconds=15.0,
                            ignore_post_ids=known_post_ids,
                        )
                if not post_id and response_post_ids:
                    post_id = response_post_ids[-1]
                    uploaded_url = self._build_post_url(post_id)
                    self._trace(f"{item.file_name}: fallback to response-captured post id={post_id}")
                if not post_id:
                    alert_type, alert_text = self._detect_page_alerts(page)
                    error_code = "tag_check_required" if alert_type == "tag_check_required" else "submit completed but no post id detected; site may require manual tag selection/edit before posting"
                    tag_state_code = "tag_error" if alert_type == "tag_check_required" else "failed"

                    self._trace(f"{item.file_name}: failed to detect post_id after retry, last_url={uploaded_url}. alert_type={alert_type}")
                    self._save_debug_artifact(page, item, reason="submit-no-post-id")
                    return AutomationUploadResult(
                        item_id=item.item_id,
                        success=False,
                        ai_tags=tags,
                        tag_state=tag_state_code,
                        uploaded_url=uploaded_url,
                        error=error_code,
                    )
            self._trace(f"{item.file_name}: upload success post_id={post_id} url={uploaded_url}")
            return AutomationUploadResult(
                item_id=item.item_id,
                success=True,
                ai_tags=tags,
                tag_state="ok" if available else "unavailable",
                uploaded_url=uploaded_url,
                post_id=post_id,
            )

    def _wait_until_upload_surface_ready(self, page) -> tuple[bool, str]:
        deadline = time.monotonic() + self.config.submit_timeout_seconds
        auth_detected_time = None

        while time.monotonic() < deadline:
            self._dismiss_common_overlays(page)

            if self._selector_count(page, FILE_INPUT_SELECTORS) > 0:
                return True, ""

            if self._selector_count(page, AUTH_HINT_SELECTORS) > 0:
                if self.config.headless:
                    return False, "login required before upload (auth/2FA screen detected)"
                if auth_detected_time is None:
                    auth_detected_time = time.monotonic()
                    deadline = time.monotonic() + 300.0  # Give human 5 minutes to login
                    self._trace("login required. WAITING for human to login in the opened browser...")
            else:
                if auth_detected_time is not None:
                    auth_detected_time = None

            time.sleep(self.config.poll_interval_seconds)

        if auth_detected_time is not None:
             return False, "login timeout (user did not login within 5 minutes)"
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

    def _force_english_settings(self, context) -> None:
        """Forces English locale by setting persistent storage and cookies."""
        try:
            # Force localStorage values before any page navigation starts
            context.add_init_script("""
                try {
                    localStorage.setItem('language', 'en');
                    localStorage.setItem('locale', 'en');
                    localStorage.setItem('i18nextLng', 'en');
                    localStorage.setItem('lang', 'en');
                } catch (e) {}
            """)
            # Overwrite cookies for the domain
            domain = ".sankakucomplex.com"
            context.add_cookies([
                {"name": "lang", "value": "en", "domain": domain, "path": "/"},
                {"name": "language", "value": "en", "domain": domain, "path": "/"},
                {"name": "locale", "value": "en", "domain": domain, "path": "/"},
            ])
            self._trace("forced English language settings (localStorage + Cookies)")
        except Exception as e:
            self._trace(f"failed to force English settings: {e}")

    def _fill_parent_id_robustly(self, page, parent_post_id: str) -> bool:
        """Fills the parent ID using a more robust sequence to ensure React state update."""
        if not parent_post_id:
            return False

        self._ensure_advanced_panel_open(page)
        parent_input = find_first_locator(page, PARENT_ID_SELECTORS)
        if parent_input is None:
            self._trace("parent input not found for robust fill")
            return False

        try:
            # 1. Focus the element
            parent_input.click()
            # 2. Clear existing value (select all + backspace)
            page.keyboard.down("Control")
            page.keyboard.press("a")
            page.keyboard.up("Control")
            page.keyboard.press("Backspace")
            # 3. Type characters with a small delay for realistic input
            parent_input.type(parent_post_id, delay=50)
            # 4. Blur/Commit by pressing Enter or clicking elsewhere
            parent_input.press("Enter")
            self._trace(f"parent ID {parent_post_id} filled robustly")
            return True
        except Exception as e:
            self._trace(f"failed robust parent fill: {e}")
            return False

    def _ensure_advanced_panel_open(self, page) -> None:
        # Check whether the parent input is already visible (panel open)
        for selector in PARENT_ID_SELECTORS:
            try:
                loc = page.locator(selector)
                if loc.count() > 0 and loc.first.is_visible():
                    return
            except Exception:
                continue

        # Try specifically by aria-label first (most stable)
        advanced = page.locator("button[aria-label='advanced'], [aria-label='advanced']").first
        if advanced.count() == 0 or not advanced.is_visible():
            # Fallback to text search
            advanced = find_button_by_text(page, ("advanced", "高级", "高级选项"))

        if advanced is None or (hasattr(advanced, "count") and advanced.count() == 0):
            self._trace("advanced expansion button not found")
            return

        try:
            advanced.click()
            self._trace("advanced panel expansion clicked")
            # Give the panel time to animate open and elements to become visible
            time.sleep(1.0)
        except Exception as e:
            self._trace(f"failed to click advanced button: {e}")
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
        file_input = find_first_locator(page, FILE_INPUT_SELECTORS)
        if file_input is not None:
            file_input.set_input_files(str(file_path))
            return "input_file"

        upload_button = find_button_by_text(page, ("上传文件", "Upload file", "Choose file", "选择文件"))
        if upload_button is not None:
            try:
                with page.expect_file_chooser(timeout=3000) as chooser_info:
                    upload_button.click()
                chooser_info.value.set_files(str(file_path))
                return "file_chooser"
            except Exception:
                pass

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

    @staticmethod
    def _detect_page_alerts(page) -> tuple[str, str]:
        try:
            text = page.evaluate(
                """
                () => {
                  const selectors = [
                    "[role='alert']",
                    ".MuiAlert-message",
                    ".MuiSnackbarContent-message",
                    ".toast",
                    ".notification",
                    ".error",
                    ".warning",
                    "body"
                  ];
                  const chunks = [];
                  for (const selector of selectors) {
                    for (const node of Array.from(document.querySelectorAll(selector)).slice(0, 6)) {
                      const txt = String(node.textContent || "").replace(/\\s+/g, " ").trim();
                      if (txt) chunks.push(txt);
                    }
                    if (chunks.length >= 3) break;
                  }
                  return chunks.join(" | ").slice(0, 1200);
                }
                """
            )
        except Exception:
            return "", ""
        if not isinstance(text, str) or not text.strip():
            return "", ""
        lowered = text.lower()

        duplicate_terms = (
            "已存在", "合并到", "已经存在", "already exists", "merged",
            "has been merged", "作为编辑", "exists", "合并"
        )
        if any(term in lowered for term in duplicate_terms):
            return "duplicate", text

        tag_terms = ("tag", "tags", "标签", "標籤", "tagging")
        review_terms = ("check", "review", "edit", "modify", "change", "確認", "检查", "檢查", "修改", "更改", "确认")
        blocking_terms = ("required", "must", "need", "需要", "必须", "必須", "无法", "不能", "can't", "cannot", "at least 20")
        if any(term in lowered for term in tag_terms) and (
            any(term in lowered for term in review_terms) or any(term in lowered for term in blocking_terms)
        ):
            return "tag_check_required", text
        return "unknown", text

    def _wait_for_uploaded_post(
        self,
        page,
        *,
        context=None,
        timeout_seconds: float | None = None,
        ignore_post_ids: set[str] | None = None,
    ) -> tuple[str, str]:
        timeout = self.config.confirmation_timeout_seconds if timeout_seconds is None else timeout_seconds
        deadline = time.monotonic() + timeout
        ignore = ignore_post_ids or set()
        last_url = self._safe_url(page)
        while time.monotonic() < deadline:
            last_url = self._safe_url(page)
            post_id = extract_post_id(last_url)
            if post_id and post_id not in ignore:
                self._trace(f"post detected on active page: post_id={post_id} url={last_url}")
                return last_url, post_id
            if context is not None:
                ctx_url, ctx_post_id = self._find_post_in_context(context, ignore_post_ids=ignore)
                if ctx_post_id:
                    self._trace(f"post detected on context page: post_id={ctx_post_id} url={ctx_url}")
                    return ctx_url, ctx_post_id
            time.sleep(self.config.poll_interval_seconds)
        return last_url, ""

    @staticmethod
    def _collect_known_post_ids(context) -> set[str]:
        post_ids: set[str] = set()
        try:
            pages = list(context.pages)
        except Exception:
            return post_ids
        for candidate in pages:
            try:
                post_id = extract_post_id(str(candidate.url))
            except Exception:
                continue
            if post_id:
                post_ids.add(post_id)
        return post_ids

    @staticmethod
    def _select_working_page(context):
        try:
            pages = list(context.pages)
        except Exception:
            pages = []
        if not pages:
            return context.new_page()
        for page in pages:
            try:
                url = str(page.url)
            except Exception:
                url = ""
            if "/posts/upload" in url:
                return page
        return pages[0]

    @staticmethod
    def _close_extra_pages(context, *, keep_page) -> None:
        try:
            pages = list(context.pages)
        except Exception:
            return
        for page in pages:
            if page is keep_page:
                continue
            try:
                page.close()
            except Exception:
                continue

    @staticmethod
    def _find_post_in_context(context, *, ignore_post_ids: set[str]) -> tuple[str, str]:
        try:
            pages = list(context.pages)
        except Exception:
            return "", ""
        for candidate in reversed(pages):
            try:
                url = str(candidate.url)
            except Exception:
                continue
            post_id = extract_post_id(url)
            if post_id and post_id not in ignore_post_ids:
                return url, post_id
        return "", ""

    def _extract_post_ids_from_response(self, response) -> list[str]:
        try:
            status = int(response.status)
            if status >= 400:
                return []
        except Exception:
            pass

        candidates: list[str] = []
        try:
            response_url = str(response.url)
        except Exception:
            response_url = ""
        if response_url:
            url_post_id = extract_post_id(response_url)
            if url_post_id:
                candidates.append(url_post_id)

        payload_text = ""
        try:
            content_type = str((response.headers or {}).get("content-type", "")).lower()
        except Exception:
            content_type = ""

        if "json" in content_type:
            try:
                data = response.json()
                candidates.extend(self._extract_post_ids_from_payload(data))
            except Exception:
                pass

        if not candidates:
            try:
                payload_text = response.text()
            except Exception:
                payload_text = ""
            if payload_text:
                candidates.extend(re.findall(r"/posts/([A-Za-z0-9_-]+)", payload_text))
                candidates.extend(
                    re.findall(r'"post(?:_id|Id)?"\s*:\s*"([A-Za-z0-9_-]+)"', payload_text, flags=re.I)
                )

        return self._normalize_post_ids(candidates)

    def _extract_post_ids_from_payload(self, payload) -> list[str]:
        found: list[str] = []

        def walk(node, parent_key: str = "") -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    key_str = str(key).lower()
                    if key_str in {"post_id", "postid"} and isinstance(value, (str, int)):
                        found.append(str(value))
                    if key_str == "id" and parent_key in {"post", "created_post", "upload", "result"} and isinstance(
                        value, (str, int)
                    ):
                        found.append(str(value))
                    if key_str == "post" and isinstance(value, dict):
                        inner_id = value.get("id") or value.get("post_id")
                        if isinstance(inner_id, (str, int)):
                            found.append(str(inner_id))
                    walk(value, key_str)
            elif isinstance(node, list):
                for child in node:
                    walk(child, parent_key)

        walk(payload)
        return found

    @staticmethod
    def _normalize_post_ids(values: Iterable[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        blocked = {
            "taggingimage",
            "tagging_image",
            "subtitles",
            "thumbnail",
            "images",
            "preview",
            "keyset",
            "upload",
            "create",
            "new",
            "edit",
            "index",
            "search",
            "random",
            "settings",
            "help",
            "popular",
            "hot",
        }
        for value in values:
            post_id = str(value).strip()
            if not post_id:
                continue
            if post_id.lower() in {"upload", "create"}:
                continue
            if post_id.lower() in blocked:
                continue
            if not re.fullmatch(r"[A-Za-z0-9]{5,64}", post_id):
                continue
            if post_id in seen:
                continue
            seen.add(post_id)
            normalized.append(post_id)
        return normalized

    def _build_post_url(self, post_id: str) -> str:
        try:
            match = re.match(r"^(https?://[^/]+)", self.config.upload_url)
            base = match.group(1) if match else "https://www.sankakucomplex.com"
        except Exception:
            base = "https://www.sankakucomplex.com"
        return f"{base}/posts/{post_id}"

    @staticmethod
    def _detach_response_listener(page, handler) -> None:
        for method_name in ("remove_listener", "off"):
            try:
                method = getattr(page, method_name)
            except Exception:
                continue
            try:
                method("response", handler)
                return
            except Exception:
                continue

    def _trace_tag_surface(self, page, file_name: str) -> None:
        parts: list[str] = []
        for selector in AI_TAG_SELECTORS:
            count = 0
            text = ""
            try:
                loc = page.locator(selector)
                count = loc.count()
                if count > 0:
                    text = " ".join((loc.first.text_content() or "").split())[:120]
            except Exception:
                pass
            parts.append(f"{selector}:count={count}:text={text!r}")
        self._trace(f"{file_name}: tag surface snapshot => " + " | ".join(parts))

        try:
            btn_samples = page.evaluate(
                """
                () => Array.from(document.querySelectorAll("button"))
                  .map((el) => String(el.textContent || "").replace(/\\s+/g, " ").trim())
                  .filter(Boolean)
                  .slice(0, 24)
                """
            )
        except Exception:
            btn_samples = []
        if isinstance(btn_samples, list) and btn_samples:
            self._trace(f"{file_name}: first button texts => {btn_samples}")

    def _save_debug_artifact(self, page, item: UploadItem, *, reason: str) -> None:
        if self.config.debug_dir is None:
            return
        try:
            timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
            stem = f"{timestamp}-{item.item_id}-{reason}"
            txt_path = self.config.debug_dir / f"{stem}.txt"
            html_path = self.config.debug_dir / f"{stem}.html"
            png_path = self.config.debug_dir / f"{stem}.png"
            txt_path.write_text(
                f"reason={reason}\nitem_id={item.item_id}\nfile={item.file_name}\nurl={self._safe_url(page)}\n",
                encoding="utf-8",
            )
            html_path.write_text(page.content(), encoding="utf-8")
            page.screenshot(path=str(png_path), full_page=True)
            self._trace(f"{item.file_name}: debug artifact saved => {txt_path}")
        except Exception:
            return

    def _review_decision(self, page, item: UploadItem, tags: list[str], available: bool, all_prepared: list[PreparedUpload] | None = None) -> ReviewDecision:
        if self.review_decision_provider is None:
            return ReviewDecision("confirm")

        # Helper to apply syncs to other pages in the same batch
        def process_background_syncs(syncs: list[dict[str, Any]]):
            if not syncs or not all_prepared:
                return
            for sync in syncs:
                target_id = sync.get("item_id")
                new_tags = sync.get("tags")
                if not target_id or target_id == item.item_id or not isinstance(new_tags, list):
                    continue
                # Find the page for this item_id in the current batch
                target_prepared = next((p for p in all_prepared if p.item.item_id == target_id), None)
                if target_prepared and target_prepared.page:
                    self._apply_tags_override(target_prepared.page, new_tags)

        current_tags = _normalize_tags(tags)
        started = time.monotonic()
        last_bg_poll = 0.0
        self._trace(f"{item.file_name}: review_request mode=decide context=active-loop item_id={item.item_id}")
        while time.monotonic() - started < self.config.confirmation_timeout_seconds:
            # Scrape active page
            try:
                edited_tags, tagging_in_progress = _extract_tags_from_editor_section(page)
                if edited_tags:
                    current_tags = _normalize_tags(edited_tags)
                elif not tagging_in_progress:
                    current_tags = []
            except Exception:
                pass

            # Throttled Background Scraper: check other tabs every 1.5s
            now = time.monotonic()
            if all_prepared and (now - last_bg_poll > 1.5):
                last_bg_poll = now
                for p in all_prepared:
                    # Skip the active one (already scraped above) and missing pages
                    if p.item.item_id == item.item_id or not p.page:
                        continue
                    try:
                        bg_tags, bg_tagging = _extract_tags_from_editor_section(p.page)
                        if bg_tags:
                             # Report back to the runner, which will emit item_review_update if changed.
                             # This is a passive probe; it must never consume queued decisions.
                             self._trace(
                                 f"{p.item.file_name}: review_request mode=probe context=background-tags "
                                 f"item_id={p.item.item_id}"
                             )
                             bg_decision = self.review_decision_provider(
                                 p.item,
                                 list(_normalize_tags(bg_tags)),
                                 p.available,
                                 "probe",
                             )
                             bg_parsed = self._normalize_review_decision(bg_decision)
                             if bg_parsed is not None:
                                 process_background_syncs(bg_parsed.pending_syncs)
                                 if bg_parsed.action == "sync" and bg_parsed.tags_override is not None:
                                     applied = self._apply_tags_override(p.page, bg_parsed.tags_override)
                                     self._trace(
                                         f"{p.item.file_name}: background live sync applied "
                                         f"count={len(bg_parsed.tags_override)} success={applied}"
                                     )
                    except Exception:
                        # Dead or restricted background tab, ignore
                        pass

            decision = self.review_decision_provider(item, list(current_tags), available, "decide")
            parsed = self._normalize_review_decision(decision)
            if parsed is not None:
                # Always check for background syncs for other items
                process_background_syncs(parsed.pending_syncs)

                if parsed.action == "sync" and parsed.tags_override is not None:
                    applied = self._apply_tags_override(page, parsed.tags_override)
                    self._trace(
                        f"{item.file_name}: live sync applied count={len(parsed.tags_override)} success={applied}"
                    )
                    continue
                if parsed.action == "wait":
                    continue
                return parsed
            time.sleep(min(self.config.poll_interval_seconds, 0.2))

        return ReviewDecision("skip")

    @staticmethod
    def _normalize_review_decision(decision) -> ReviewDecision | None:
        if decision is None:
            return None
        if isinstance(decision, ReviewDecision):
            if decision.action in {"confirm", "skip", "retry", "sync", "wait"}:
                return decision
            return ReviewDecision("confirm", pending_syncs=decision.pending_syncs)
        if decision in {"confirm", "skip", "retry", "sync", "wait"}:
            return ReviewDecision(decision)
        return None

    def _apply_tags_override(self, page, tags: list[str]) -> bool:
        tag_input = find_first_locator(page, TAG_INPUT_SELECTORS)
        if tag_input is None:
            return False

        self._clear_current_tags_from_editor(page)

        for tag in tags:
            clean = str(tag).strip()
            if not clean:
                continue
            try:
                tag_input.click()
                tag_input.fill(clean)
                tag_input.press("Enter")
                time.sleep(min(self.config.poll_interval_seconds, 0.2))
            except Exception:
                return False
        return True

    def _sync_tags_after_review(self, page, *, baseline_tags: list[str]) -> list[str]:
        deadline = time.monotonic() + 2.5
        latest = list(baseline_tags)
        while time.monotonic() < deadline:
            edited_tags, tagging_in_progress = _extract_tags_from_editor_section(page)
            if edited_tags:
                latest = edited_tags
                if not tagging_in_progress:
                    return latest
            else:
                if not tagging_in_progress:
                    return []
            time.sleep(min(self.config.poll_interval_seconds, 0.2))
        return latest

    @staticmethod
    def _clear_current_tags_from_editor(page) -> None:
        try:
            page.evaluate(
                """
                () => {
                  const input =
                    document.querySelector("#autocomplete") ||
                    document.querySelector("input[name='tags']") ||
                    document.querySelector("input[placeholder*='标签']") ||
                    document.querySelector("input[placeholder*='tag' i]");
                  if (!input) return 0;
                  const root =
                    input.closest(".MuiAutocomplete-root") ||
                    input.closest(".MuiGrid-root") ||
                    input.parentElement;
                  if (!root) return 0;

                  const fireClick = (el) => {
                    if (!el) return;
                    const target = el.closest("button") || el;
                    target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
                  };

                  const clear = root.querySelector(
                    ".MuiAutocomplete-clearIndicator,button[aria-label*='clear' i],button[title*='clear' i]"
                  );
                  fireClick(clear);

                  const selectors = [
                    ".MuiChip-deleteIcon",
                    "[data-testid='CancelIcon']",
                    "svg[data-testid='CancelIcon']",
                  ];
                  let guard = 0;
                  while (guard++ < 128) {
                    let found = false;
                    for (const selector of selectors) {
                      const node = root.querySelector(selector);
                      if (!node) continue;
                      fireClick(node);
                      found = true;
                      break;
                    }
                    if (!found) break;
                  }
                }
                """
            )
        except Exception:
            return

    @staticmethod
    def _safe_url(page) -> str:
        try:
            return str(page.url)
        except Exception:
            return ""
