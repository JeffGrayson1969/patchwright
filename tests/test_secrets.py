"""Secret resolution: env fallback, keyring preference, missing-key error."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from patchwright.core.secrets import SERVICE_NAME, SecretNotFound, get_secret


def test_env_var_is_used_when_keyring_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_KEY", "env-value")
    with patch("keyring.get_password", return_value=None):
        assert get_secret("TEST_KEY") == "env-value"


def test_keyring_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_KEY", "env-value")
    with patch("keyring.get_password", return_value="keyring-value") as mock_kr:
        assert get_secret("TEST_KEY") == "keyring-value"
        mock_kr.assert_called_once_with(SERVICE_NAME, "TEST_KEY")


def test_missing_secret_raises_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    with patch("keyring.get_password", return_value=None), pytest.raises(SecretNotFound):
        get_secret("MISSING_KEY")


def test_missing_secret_returns_none_when_required_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    with patch("keyring.get_password", return_value=None):
        assert get_secret("MISSING_KEY", required=False) is None


def test_keyring_exception_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_KEY", "env-value")
    with patch("keyring.get_password", side_effect=RuntimeError("backend broken")):
        assert get_secret("TEST_KEY") == "env-value"
