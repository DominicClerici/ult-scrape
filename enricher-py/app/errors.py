class EnrichError(Exception):
    """Base class for enrichment failures."""


class TransientEnrichError(EnrichError):
    """Retryable failure (network error, rate-limit, download interrupted)."""


class PermanentEnrichError(EnrichError):
    """Non-retryable failure (unusable route / metadata)."""
