from core.credential_fields import looks_like_credential


def test_password_type_flagged():
    assert looks_like_credential({"type": "password"}) is True


def test_autocomplete_tokens_flagged():
    assert looks_like_credential({"autocomplete": "username"}) is True
    assert looks_like_credential({"autocomplete": "current-password"}) is True


def test_label_matches_flutter_signals():
    assert looks_like_credential({"aria_label": "User ID"}) is True
    assert looks_like_credential({"text_content": "Email"}) is True
    assert looks_like_credential({"nearest_label_text": "Password"}) is True


def test_ordinary_fields_not_flagged():
    assert looks_like_credential({"aria_label": "Annual Income", "name": "income"}) is False
    assert looks_like_credential({"text_content": "PAN"}) is False
    assert looks_like_credential({}) is False
