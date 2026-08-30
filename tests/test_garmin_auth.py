"""Web login flow, against a fake garminconnect client.

The two behaviours pinned here are the ones that make a naive implementation
look correct and then fail in production:

  * resume_login ignores its client_state argument, so the SAME client object
    must survive between the two requests;
  * neither login(return_on_mfa=True) nor resume_login persists tokens, so an
    explicit dump is required or every restart re-prompts for MFA.
"""

from __future__ import annotations

import logging

import pytest

from garmin_stats_sync import garmin_auth


class FakeClient:
    """The `Garmin.client` attribute, which owns dump/load."""

    def __init__(self) -> None:
        self.dumped_to: str | None = None

    def dump(self, path: str) -> None:
        self.dumped_to = path


class FakeGarmin:
    """Stands in for garminconnect.Garmin."""

    instances: list = []

    def __init__(self, email="", password="", return_on_mfa=False, needs_mfa=True):
        self.email = email
        self.password = password
        self.return_on_mfa = return_on_mfa
        self.needs_mfa = needs_mfa
        self.client = FakeClient()
        self.resumed_with: str | None = None
        self.login_calls = 0
        FakeGarmin.instances.append(self)

    def login(self, tokenstore=None):
        self.login_calls += 1
        return ("needs_mfa", None) if self.needs_mfa else (None, None)

    def resume_login(self, _client_state, mfa_code):
        self.resumed_with = mfa_code
        return (None, None)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    FakeGarmin.instances = []
    garmin_auth._sessions.clear()
    yield
    garmin_auth._sessions.clear()


@pytest.fixture
def mfa_client(monkeypatch):
    def build(config, *, email="", password="", mfa=False):
        return FakeGarmin(email=email, password=password, return_on_mfa=mfa)

    monkeypatch.setattr(garmin_auth, "_build_client", build)


@pytest.fixture
def clean_client(monkeypatch):
    def build(config, *, email="", password="", mfa=False):
        return FakeGarmin(
            email=email, password=password, return_on_mfa=mfa, needs_mfa=False
        )

    monkeypatch.setattr(garmin_auth, "_build_client", build)


def test_login_requiring_mfa_returns_a_session(config, mfa_client):
    session_id = garmin_auth.begin_login(config, "a@b.com", "secret")

    assert session_id
    assert session_id in garmin_auth._sessions


def test_clean_login_persists_tokens_immediately(config, clean_client):
    assert garmin_auth.begin_login(config, "a@b.com", "secret") is None

    (garmin,) = FakeGarmin.instances
    assert garmin.client.dumped_to == str(config.garth_dir)


def test_mfa_uses_the_same_client_object(config, mfa_client):
    """resume_login reads MFA state off the instance, not off client_state."""
    session_id = garmin_auth.begin_login(config, "a@b.com", "secret")
    garmin_auth.submit_mfa(config, session_id, "123456")

    (garmin,) = FakeGarmin.instances
    assert garmin.resumed_with == "123456"
    assert garmin.login_calls == 1


def test_mfa_completion_persists_tokens_explicitly(config, mfa_client):
    """garminconnect dumps on neither path, so we must."""
    session_id = garmin_auth.begin_login(config, "a@b.com", "secret")
    garmin_auth.submit_mfa(config, session_id, "123456")

    (garmin,) = FakeGarmin.instances
    assert garmin.client.dumped_to == str(config.garth_dir)
    assert config.garth_dir.exists()


def test_session_is_consumed_after_use(config, mfa_client):
    session_id = garmin_auth.begin_login(config, "a@b.com", "secret")
    garmin_auth.submit_mfa(config, session_id, "123456")

    with pytest.raises(garmin_auth.LoginError, match="expired"):
        garmin_auth.submit_mfa(config, session_id, "123456")


def test_unknown_session_is_rejected(config):
    with pytest.raises(garmin_auth.LoginError, match="expired"):
        garmin_auth.submit_mfa(config, "nope", "123456")


def test_expired_session_is_rejected(config, mfa_client, monkeypatch):
    from datetime import UTC, datetime, timedelta

    session_id = garmin_auth.begin_login(config, "a@b.com", "secret")
    session = garmin_auth._sessions[session_id]
    session.created_at = datetime.now(UTC) - timedelta(hours=1)

    with pytest.raises(garmin_auth.LoginError, match="expired"):
        garmin_auth.submit_mfa(config, session_id, "123456")


def test_blank_credentials_are_rejected_without_calling_garmin(config, mfa_client):
    with pytest.raises(garmin_auth.LoginError, match="required"):
        garmin_auth.begin_login(config, "", "")
    assert FakeGarmin.instances == []


def test_blank_mfa_code_is_rejected(config, mfa_client):
    session_id = garmin_auth.begin_login(config, "a@b.com", "secret")
    with pytest.raises(garmin_auth.LoginError, match="code"):
        garmin_auth.submit_mfa(config, session_id, "   ")


def test_concurrent_sessions_are_capped(config, mfa_client):
    for _ in range(garmin_auth.MAX_SESSIONS):
        garmin_auth.begin_login(config, "a@b.com", "secret")

    with pytest.raises(garmin_auth.LoginError, match="Too many"):
        garmin_auth.begin_login(config, "a@b.com", "secret")


def test_password_never_reaches_the_logs(config, mfa_client, caplog):
    with caplog.at_level(logging.DEBUG):
        session_id = garmin_auth.begin_login(config, "a@b.com", "hunter2")
        garmin_auth.submit_mfa(config, session_id, "123456")

    assert "hunter2" not in caplog.text


def test_token_state_is_absent_without_a_tokenstore(config):
    assert garmin_auth.token_state(config) == "absent"


def test_token_state_is_expired_when_login_fails(config, monkeypatch):
    config.garth_dir.mkdir(parents=True)
    (config.garth_dir / "oauth1_token.json").write_text("{}")

    def broken(_config, **_kwargs):
        raise RuntimeError("tokens rejected")

    monkeypatch.setattr(garmin_auth, "_build_client", broken)
    assert garmin_auth.token_state(config) == "expired"


def test_token_state_is_valid_when_login_succeeds(config, clean_client):
    config.garth_dir.mkdir(parents=True)
    (config.garth_dir / "oauth1_token.json").write_text("{}")

    assert garmin_auth.token_state(config) == "valid"
