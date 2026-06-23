import inspect

from app.browser import login


def test_callables_present():
    assert inspect.iscoroutinefunction(login.login)
    assert inspect.iscoroutinefunction(login.is_logged_in)
    assert login.PROFILE_SELECTOR.startswith("[href=")
