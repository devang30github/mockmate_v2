class ProviderError(Exception):
    """Base class. `retryable` means: a different credential may succeed."""

    retryable = False


class RateLimited(ProviderError):
    retryable = True

    def __init__(self, retry_after: float | None = None):
        super().__init__("rate limited")
        self.retry_after = retry_after


class AuthFailed(ProviderError):
    retryable = True  # this key is bad; another key may work


class TransientError(ProviderError):
    retryable = True  # 5xx, timeout, connection reset


class BadRequest(ProviderError):
    retryable = False  # our request is wrong; retrying cannot help


class NoCredentialAvailable(ProviderError):
    retryable = False

class PaymentRequired(ProviderError):
    """402: billing, quota, or check-in needed. A key on another account may still work."""

    retryable = True