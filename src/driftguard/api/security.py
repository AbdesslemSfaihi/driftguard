import hashlib
import hmac


def verify_signature(payload: bytes, signature_header: str | None, secret: str) -> bool:
    """Check GitHub's X-Hub-Signature-256 header against the shared webhook secret.

    GitHub signs the raw request body with HMAC-SHA256 and sends the result as
    "sha256=<hex>". Recomputing it proves both authenticity (only GitHub and we
    know the secret) and integrity (the body was not modified in transit).

    hmac.compare_digest is constant-time: a plain == comparison would leak, byte
    by byte, how much of a forged signature is correct (a timing attack).
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))
