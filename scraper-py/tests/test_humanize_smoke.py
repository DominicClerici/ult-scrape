from app.browser import humanize


def test_rand_int_in_range():
    for _ in range(100):
        v = humanize._rand_int(5, 10)
        assert 5 <= v <= 10


def test_rand_float_in_range():
    for _ in range(100):
        v = humanize._rand_float(0.35, 0.65)
        assert 0.35 <= v <= 0.65


def test_cloudflare_timeout_message():
    assert "Cloudflare" in str(humanize.CloudflareTimeout())
