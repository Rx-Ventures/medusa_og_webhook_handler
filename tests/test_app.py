"""
Unit tests for the root app endpoints and webhook helper functions.
"""
import pytest


# ─── Root endpoints ─────────────────────────────────────────────────────────


def test_root(client):
    """GET / returns welcome message."""
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()


def test_health_check(client):
    """GET /health returns healthy status."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


# ─── Webhook helper: _get_solidgate_payment_token ────────────────────────────


class TestGetSolidgatePaymentToken:
    """Tests for the _get_solidgate_payment_token helper."""

    def _fn(self, payload):
        from app.api.v1.endpoints.webhooks import _get_solidgate_payment_token
        return _get_solidgate_payment_token(payload)

    def test_token_from_card_token_dict(self):
        """Extracts token from transactions[*].card_token.token."""
        payload = {
            "transactions": {
                "auth": {"card_token": {"token": "ct_abc123"}},
            },
        }
        assert self._fn(payload) == "ct_abc123"

    def test_token_from_card_token_string(self):
        """Extracts token when card_token is a plain string."""
        payload = {
            "transactions": {
                "auth": {"card_token": "ct_string"},
            },
        }
        assert self._fn(payload) == "ct_string"

    def test_token_from_pay_form(self):
        """Falls back to pay_form.token."""
        payload = {
            "transactions": {},
            "pay_form": {"token": "pf_tok_1"},
        }
        assert self._fn(payload) == "pf_tok_1"

    def test_token_from_payForm(self):
        """Falls back to payForm.token (camelCase variant)."""
        payload = {"payForm": {"token": "pf_tok_2"}}
        assert self._fn(payload) == "pf_tok_2"

    def test_no_token(self):
        """Returns None when no token found."""
        assert self._fn({}) is None
        assert self._fn({"transactions": {}}) is None
        assert self._fn({"transactions": {"auth": {}}}) is None

    def test_empty_token_string(self):
        """Returns None for empty string tokens."""
        payload = {"transactions": {"auth": {"card_token": ""}}}
        assert self._fn(payload) is None


class TestGetSolidgatePaymentOverride:
    """Tests for the _get_solidgate_payment_override helper."""

    def _fn(self, payload):
        from app.api.v1.endpoints.webhooks import _get_solidgate_payment_override
        return _get_solidgate_payment_override(payload)

    def test_full_override(self):
        """Extracts token_id, cc_number, cc_type, cc_exp_date from transactions."""
        payload = {
            "transactions": {
                "auth": {
                    "card_token": {"token": "ct_full"},
                    "card": {
                        "number": "4111****1111",
                        "brand": "Visa",
                        "card_exp_month": "03",
                        "card_exp_year": "2028",
                    },
                },
            },
        }
        result = self._fn(payload)
        assert result["token_id"] == "ct_full"
        assert result["cc_number"] == "4111****1111"
        assert result["cc_type"] == "1"
        assert result["cc_exp_date"] == "03/2028"

    def test_mastercard_type(self):
        """Maps mastercard to OG code 2."""
        payload = {
            "transactions": {
                "auth": {
                    "card_token": {"token": "ct_mc"},
                    "card": {"brand": "MasterCard"},
                },
            },
        }
        result = self._fn(payload)
        assert result["cc_type"] == "2"

    def test_amex_type(self):
        """Maps amex to OG code 3."""
        payload = {
            "transactions": {
                "auth": {
                    "card_token": {"token": "ct_amex"},
                    "card": {"brand": "American Express"},
                },
            },
        }
        result = self._fn(payload)
        assert result["cc_type"] == "3"

    def test_unknown_card_type(self):
        """Unknown card brand maps to empty string."""
        payload = {
            "transactions": {
                "auth": {
                    "card_token": {"token": "ct_unk"},
                    "card": {"brand": "UnknownCard"},
                },
            },
        }
        result = self._fn(payload)
        assert result["cc_type"] == ""

    def test_no_token_returns_none(self):
        """Returns None when no token found."""
        assert self._fn({}) is None
        assert self._fn({"transactions": {}}) is None
