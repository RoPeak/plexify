from plexify.util import sanitise_name


def test_sanitize_name_removes_windows_invalid_chars():
    assert sanitise_name('Bad<>:"/\\|?*Name') == "Bad - Name"


def test_sanitize_name_trims_trailing_dots_spaces():
    assert sanitise_name("Title. ") == "Title"


def test_sanitize_name_reserved_windows_names():
    assert sanitise_name("CON") == "CON_"
