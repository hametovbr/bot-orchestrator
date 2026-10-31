import pytest

from bot_orchestrator.security import Cipher, mask, short_token
from bot_orchestrator.updates import describe, parse_allowed

from .conftest import TOKEN


def test_mask_hides_tokens():
    assert mask(f"url https://api/bot{TOKEN}/getMe") == "url https://api/bot<token>/getMe"
    assert mask("12:short") == "12:short"


def test_short_token():
    assert short_token(TOKEN) == "777000111:…BBBB"


def test_cipher_roundtrip_and_wrong_key():
    value = Cipher("k" * 32).encrypt("secret")
    assert Cipher("k" * 32).decrypt(value) == "secret"
    with pytest.raises(ValueError, match="SECRET_KEY"):
        Cipher("z" * 32).decrypt(value)
    with pytest.raises(ValueError):
        Cipher("short")


def test_describe_document_and_callback():
    doc = describe(
        {
            "update_id": 1,
            "message": {
                "from": {"id": 5, "first_name": "Ann", "last_name": "Lee"},
                "chat": {"id": 5},
                "document": {"file_name": "a.pdf"},
                "caption": "see",
            },
        }
    )
    assert doc == {
        "kind": "message",
        "from_id": 5,
        "from_name": "Ann Lee",
        "summary": "[document] a.pdf see",
    }
    cb = describe(
        {
            "update_id": 2,
            "callback_query": {"id": "q", "data": "yes", "from": {"id": 6, "username": "bob"}},
        }
    )
    assert cb["summary"] == "[button] yes" and cb["from_name"] == "@bob"


def test_parse_allowed():
    assert parse_allowed("1, 2;3 x") == {1, 2, 3}
    assert parse_allowed("") == set()
