from plexify.ui import rich_escape


def test_rich_escape_brackets() -> None:
    text = "06 - Oasis - [untitled].flac"
    escaped = rich_escape(text)
    assert "\\[" in escaped
    assert escaped != text
