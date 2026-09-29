from tk_workspace.modules.identity import security


def test_password_hash_roundtrip():
    h = security.hash_password("s3cret-pass")
    assert h.startswith("$argon2id$")
    assert security.verify_password(h, "s3cret-pass")
    assert not security.verify_password(h, "wrong")


def test_missing_hash_never_verifies():
    assert not security.verify_password(None, "anything")


def test_token_digest_is_stable_and_not_plaintext():
    t = security.new_token()
    assert security.token_digest(t) == security.token_digest(t)
    assert t not in security.token_digest(t)
    assert security.digest_matches(t, security.token_digest(t))
    assert not security.digest_matches(t + "x", security.token_digest(t))
