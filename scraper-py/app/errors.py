class ScrapeError(Exception):
    """Base class for scrape failures."""


class TransientScrapeError(ScrapeError):
    """Retryable failure (cloudflare/navigation timeout, no XTZ captured)."""


class RateLimitScrapeError(TransientScrapeError):
    """Blocked/rate-limited by UG or Cloudflare (HTTP 403/429). Retryable, but the
    worker logs it and backs off harder before the next job."""


class PermanentScrapeError(ScrapeError):
    """Non-retryable failure (tab 404 / invalid route)."""


class SessionExpiredError(ScrapeError):
    """Logged-out state detected; re-login and resume without consuming a retry."""


class DiscoveryParseError(Exception):
    """Raised when the explore page's embedded js-store cannot be located or parsed."""
