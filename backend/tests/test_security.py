from app.core import security


def test_hash_is_argon2id_and_verifies():
    h = security.hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert security.verify_password(h, "correct horse battery")
    assert not security.verify_password(h, "wrong password!!")


def test_verify_rejects_garbage_hash():
    assert not security.verify_password("not-a-hash", "whatever")


def test_session_tokens_are_unique_and_long():
    a, b = security.new_session_token(), security.new_session_token()
    assert a != b and len(a) >= 40


def test_token_hash_is_deterministic_and_not_the_token():
    t = security.new_session_token()
    assert security.hash_token(t) == security.hash_token(t)
    assert security.hash_token(t) != t