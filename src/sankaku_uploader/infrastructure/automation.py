from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from datetime import UTC, datetime
import re
import time
from typing import Callable, Iterable, Literal, Protocol

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from sankaku_uploader.domain import UploadItem

TagDecision = Literal["confirm", "skip", "retry", "sync"]
@dataclass(slots=True)
class ReviewDecision:
    action: TagDecision
    tags_override: list[str] | None = None


ReviewDecisionProvider = Callable[[UploadItem, list[str], bool], ReviewDecision | TagDecision | None]
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
    button_tags = _extract_tag_candidates_from_buttons(page)
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
        tag = str(value).strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
    return normalized


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

              if (candidates.length === 0 && !inProgress) {
                const buttons = section.querySelectorAll("button");
                for (const button of buttons) {
                  const text = clean(button.textContent);
                  if (text) candidates.push(text);
                }
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


def _extract_tag_candidates_from_buttons(page) -> list[str]:
    try:
        raw = page.evaluate(
            """
            () => Array.from(document.querySelectorAll("button"))
              .map((el) => String(el.textContent || "").trim())
              .filter(Boolean)
            """
        )
    except Exception:
        return []

    if not isinstance(raw, list):
        return []

    blocked = {
        "upload",
        "upload file",
        "create post",
        "submit",
        "advanced",
        "close",
        "retry",
        "try again",
        "cancel",
        "skip",
        "confirm",
        "manual",
        "auto",
        "open",
        "创建帖子",
        "上传",
        "高级",
        "关闭",
        "重试",
        "跳过",
        "确认",
        "提交",
        "r15+",
        "r18+",
        "清除元数据",
        "取消自动标记",
    }
    candidates: list[str] = []
    for entry in raw:
        text = str(entry).strip()
        low = text.lower()
        if low in blocked:
            continue
        if len(text) < 2 or len(text) > 64:
            continue
        if re.fullmatch(r"\d+", text):
            continue
        if any(x in low for x in ("http://", "https://")):
            continue
        if " " in text:
            continue
        candidates.append(text)
    return _normalize_tags(candidates)


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

    def upload_items(self, items: list[UploadItem], *, diff_mode: bool = False) -> list[AutomationUploadResult]:
        results: list[AutomationUploadResult] = []
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        if self.config.debug_dir is not None:
            self.config.debug_dir.mkdir(parents=True, exist_ok=True)
        self._trace(
            f"automation start: items={len(items)} diff_mode={diff_mode} headless={self.config.headless} "
            f"channel={self.config.browser_channel} profile_dir={self.config.profile_dir}"
        )

        with sync_playwright() as p:
            launch_kwargs = {
                "user_data_dir": str(self.config.profile_dir),
                "headless": self.config.headless,
            }
            if self.config.browser_channel:
                launch_kwargs["channel"] = self.config.browser_channel
            context = p.chromium.launch_persistent_context(**launch_kwargs)
            try:
                page = self._select_working_page(context)
                self._close_extra_pages(context, keep_page=page)
                root_post_id = ""
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
                    parent_post_id = root_post_id if diff_mode and idx > 0 else ""
                    if parent_post_id:
                        self._trace(f"{item.file_name}: using parent_post_id={parent_post_id}")
                    result = self._upload_one(
                        page,
                        context,
                        item,
                        parent_post_id=parent_post_id,
                        known_post_ids=known_post_ids,
                    )
                    results.append(result)
                    if diff_mode and idx == 0 and result.post_id:
                        root_post_id = result.post_id
                        self._trace(f"root post id established: {root_post_id}")
                    if diff_mode and idx > 0 and not root_post_id:
                        result.success = False
                        result.error = "root post id missing in diff mode"
                    self._close_extra_pages(context, keep_page=page)
            finally:
                context.close()
        return results

    def _upload_one(self, page, context, item: UploadItem, *, parent_post_id: str, known_post_ids: set[str]) -> AutomationUploadResult:
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
        try:
            self._dismiss_common_overlays(page)
            selected_by = self._select_file(page, Path(item.file_path))
            if not selected_by:
                self._trace(f"{item.file_name}: file selection failed")
                return AutomationUploadResult(item_id=item.item_id, success=False, tag_state="failed", error="cannot set file")
            self._trace(f"{item.file_name}: file selected via {selected_by}")

            if parent_post_id:
                self._ensure_advanced_panel_open(page)
                parent_input = find_first_locator(page, PARENT_ID_SELECTORS)
                if parent_input is not None:
                    parent_input.fill(parent_post_id)
                    self._trace(f"{item.file_name}: parent input filled")
                else:
                    self._trace(f"{item.file_name}: parent input not found")

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

            decision = self._review_decision(page, item, tags, available)
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
            if not post_id:
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
                    self._trace(f"{item.file_name}: failed to detect post_id after retry, last_url={uploaded_url}")
                    self._save_debug_artifact(page, item, reason="submit-no-post-id")
                    return AutomationUploadResult(
                        item_id=item.item_id,
                        success=False,
                        ai_tags=tags,
                        tag_state="failed",
                        uploaded_url=uploaded_url,
                        error="submit completed but no post id detected; site may require manual tag selection/edit before posting",
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
        except Exception as exc:
            self._trace(f"{item.file_name}: exception during upload: {exc}")
            self._save_debug_artifact(page, item, reason=f"exception-{type(exc).__name__}")
            return AutomationUploadResult(item_id=item.item_id, success=False, tag_state="failed", error=str(exc))
        finally:
            self._detach_response_listener(page, on_response)

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

    def _review_decision(self, page, item: UploadItem, tags: list[str], available: bool) -> ReviewDecision:
        if self.review_decision_provider is None:
            return ReviewDecision("confirm")

        current_tags = _normalize_tags(tags)
        started = time.monotonic()
        while time.monotonic() - started < self.config.confirmation_timeout_seconds:
            edited_tags, tagging_in_progress = _extract_tags_from_editor_section(page)
            if edited_tags:
                current_tags = edited_tags
            elif not tagging_in_progress:
                current_tags = []

            decision = self.review_decision_provider(item, list(current_tags), available)
            parsed = self._normalize_review_decision(decision)
            if parsed is not None:
                if parsed.action == "sync" and parsed.tags_override is not None:
                    applied = self._apply_tags_override(page, parsed.tags_override)
                    self._trace(
                        f"{item.file_name}: live sync applied count={len(parsed.tags_override)} success={applied}"
                    )
                    continue
                return parsed
            time.sleep(min(self.config.poll_interval_seconds, 0.2))

        return ReviewDecision("skip")

    @staticmethod
    def _normalize_review_decision(decision) -> ReviewDecision | None:
        if decision is None:
            return None
        if isinstance(decision, ReviewDecision):
            if decision.action in {"confirm", "skip", "retry", "sync"}:
                return decision
            return ReviewDecision("confirm")
        if decision in {"confirm", "skip", "retry", "sync"}:
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
