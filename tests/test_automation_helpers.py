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
    def __init__(self, mapping: dict[str, str | None], eval_tags: list[str] | None = None) -> None:
        self.mapping = mapping
        self.eval_tags = eval_tags

    def locator(self, selector: str):
        text = self.mapping.get(selector)
        return FakeLocator(text, 1 if text is not None else 0)

    def evaluate(self, _script: str, _selectors):
        if self.eval_tags is None:
            raise RuntimeError("evaluate not supported")
        return self.eval_tags


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
