import app


def test_version_present():
    assert isinstance(app.__version__, str)
    assert app.__version__
