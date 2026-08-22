from driftguard.api.security import verify_signature

SECRET = "test-secret"


def test_valid_signature_accepted():
    body = b'{"hello": "world"}'
    import hashlib
    import hmac

    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, SECRET) is True


def test_tampered_body_rejected():
    body = b'{"hello": "world"}'
    import hashlib
    import hmac

    sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(b'{"hello": "hacked"}', sig, SECRET) is False


def test_missing_header_rejected():
    assert verify_signature(b"{}", None, SECRET) is False


def test_wrong_scheme_rejected():
    assert verify_signature(b"{}", "sha1=abcdef", SECRET) is False
