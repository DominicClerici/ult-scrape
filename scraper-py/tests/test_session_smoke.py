import inspect

from app.browser.session import CamoufoxBrowserSession


def test_session_implements_protocol_shape():
    for name in ("start", "ensure_logged_in", "is_logged_in", "scrape", "close"):
        assert inspect.iscoroutinefunction(getattr(CamoufoxBrowserSession, name))
