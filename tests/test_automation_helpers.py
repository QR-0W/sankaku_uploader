from sankaku_uploader.infrastructure.automation import extract_ai_tags, extract_post_id


class FakeLocator:
    def __init__(self, text: str | None, count: int) -> None:
        self._text = text
        self._count = count

    def count(self) -> int:
        return self._count

    @property
    def first(self):
        return self

    def text_content(self) -> str | None:
        return self._text


class FakePage:
    def __init__(
        self,
        mapping: dict[str, str | None],
        eval_tags: list[str] | None = None,
        eval_buttons: list[str] | None = None,
        eval_editor: dict | None = None,
    ) -> None:
        self.mapping = mapping
        self.eval_tags = eval_tags
        self.eval_buttons = eval_buttons
        self.eval_editor = eval_editor

    def locator(self, selector: str):
        text = self.mapping.get(selector)
        return FakeLocator(text, 1 if text is not None else 0)

    def evaluate(self, _script: str, *args):
        if args:
            if self.eval_tags is None:
                raise RuntimeError("evaluate not supported")
            return self.eval_tags
        if self.eval_editor is not None:
            return self.eval_editor
        if self.eval_buttons is None:
            raise RuntimeError("evaluate not supported")
        return self.eval_buttons


def test_extract_post_id() -> None:
    assert extract_post_id("https://www.sankakucomplex.com/posts/123") == "123"
    assert extract_post_id("https://www.sankakucomplex.com/zh-CN/posts/WKaoQdVKKRJ") == "WKaoQdVKKRJ"
    assert extract_post_id("https://www.sankakucomplex.com/zh-CN/posts/upload") == ""


def test_extract_ai_tags() -> None:
    page = FakePage({"#ai-tags": "tag-a, tag-b\n tag-c"})
    assert extract_ai_tags(page) == ["tag-a", "tag-b", "tag-c"]


def test_extract_ai_tags_prefers_dom_scan_and_dedupes() -> None:
    page = FakePage({}, eval_tags=["tag-a", "Tag-A", "tag-b", " "])
    assert extract_ai_tags(page) == ["tag-a", "tag-b"]


def test_extract_ai_tags_fallback_to_button_candidates() -> None:
    page = FakePage({}, eval_buttons=["Create post", "multiple_views", "1girl", "Advanced"])
    assert extract_ai_tags(page) == ["multiple_views", "1girl"]


def test_extract_ai_tags_ignores_rating_controls_when_editor_tags_exist() -> None:
    page = FakePage(
        {},
        eval_editor={"tags": ["R15+", "R18+", "clear metadata", "1girl"], "inProgress": False},
    )
    assert extract_ai_tags(page) == ["1girl"]


def test_extract_ai_tags_waits_when_editor_in_progress() -> None:
    page = FakePage(
        {},
        eval_buttons=["R15+", "R18+", "清除元数据"],
        eval_editor={"tags": [], "inProgress": True},
    )
    assert extract_ai_tags(page) == []
