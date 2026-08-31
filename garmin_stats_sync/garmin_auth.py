"""Browser-driven Garmin login, so no password need be stored permanently.

Garmin sessions last roughly a year: garminconnect keeps a refresh token and
renews the short-lived one itself. That is what makes a login page worth having
instead of GARMIN_PASSWORD in an env file.

Two library behaviours drive the shape of this module, both verified against
garminconnect 0.3.11:

1. `resume_login(self, _client_state, mfa_code)` ignores its state argument - the
   MFA state lives on the Garmin instance. So the *same object* must survive
   between the credentials request and the MFA-code request, which is why
   sessions are held in memory rather than reconstructed per request.

2. Neither `login()` (on the return_on_mfa path, which returns before its own
   dump) nor `resume_login()` persists tokens. We must dump explicitly, or an
   MFA login appears to succeed and then re-prompts after every restart.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

logger = logging.getLogger(__name__)

SESSION_TTL = timedelta(minutes=10)
MAX_SESSIONS = 4

# Verifying tokens costs two Garmin API calls, and /health is polled by the
# container healthcheck every 60s. Cache the answer so a healthy system does not
# hammer Garmin for a value that changes about once a year.
TOKEN_STATE_TTL = timedelta(minutes=15)

TokenState = Literal["valid", "expired", "absent"]


class LoginError(RuntimeError):
    """Login could not be completed. The message is safe to show a user."""


@dataclass
class LoginSession:
    """A login awaiting its MFA code.

    Holds the live Garmin client because resume_login reads MFA state off it.
    """

    session_id: str
    client: Any
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) - self.created_at > SESSION_TTL


_sessions: dict[str, LoginSession] = {}
_lock = threading.Lock()

_token_cache: tuple[datetime, TokenState] | None = None
_token_lock = threading.Lock()


def invalidate_token_state(known: TokenState | None = None) -> None:
    """Drop the cached answer, or replace it with one we just proved.

    Called after a login (proved valid) and after an upload failure (suspect),
    so the cache never masks a state change the user is waiting to see.
    """
    global _token_cache
    with _token_lock:
        _token_cache = (datetime.now(UTC), known) if known else None


def _prune_sessions() -> None:
    for key in [k for k, v in _sessions.items() if v.expired]:
        _sessions.pop(key, None)


def token_state(config, *, force: bool = False) -> TokenState:
    """Whether the stored Garmin tokens exist and still work.

    Cached for TOKEN_STATE_TTL: the check itself costs two Garmin API calls, and
    the answer is stable for months at a time. Pass force=True to bypass.
    """
    global _token_cache

    # Cheap and always accurate - no point caching or calling out for this.
    if not config.garth_dir.exists() or not any(config.garth_dir.iterdir()):
        invalidate_token_state()
        return "absent"

    if not force:
        with _token_lock:
            cached = _token_cache
        if cached and datetime.now(UTC) - cached[0] < TOKEN_STATE_TTL:
            return cached[1]

    try:
        client = _build_client(config)
        client.login(tokenstore=str(config.garth_dir))
        state: TokenState = "valid"
    except Exception as exc:  # noqa: BLE001 - any failure means "log in again"
        logger.info("stored Garmin tokens are not usable: %s", exc)
        state = "expired"

    with _token_lock:
        _token_cache = (datetime.now(UTC), state)
    return state


def _build_client(config, *, email: str = "", password: str = "", mfa: bool = False):
    from garminconnect import Garmin

    return Garmin(
        email=email or config.garmin_email,
        password=password or config.garmin_password,
        return_on_mfa=mfa,
    )


def begin_login(config, email: str, password: str) -> str | None:
    """Start a login. Returns a session id if MFA is required, else None.

    `password` is never persisted or logged - it lives only for this call.
    """
    if not email or not password:
        raise LoginError("Email and password are required.")

    client = _build_client(config, email=email, password=password, mfa=True)
    try:
        status, _ = client.login(tokenstore=str(config.garth_dir))
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
        raise LoginError(f"Garmin rejected the login: {exc}") from exc

    if status != "needs_mfa":
        _persist(config, client)
        return None

    with _lock:
        _prune_sessions()
        if len(_sessions) >= MAX_SESSIONS:
            raise LoginError("Too many logins in progress. Try again shortly.")
        session_id = secrets.token_urlsafe(32)
        _sessions[session_id] = LoginSession(session_id=session_id, client=client)
    return session_id


def submit_mfa(config, session_id: str, code: str) -> None:
    """Finish a login with its MFA code and persist the tokens."""
    if not code.strip():
        raise LoginError("Enter the code Garmin sent you.")

    with _lock:
        _prune_sessions()
        session = _sessions.get(session_id)
    if session is None:
        raise LoginError("That login expired. Start again.")

    try:
        session.client.resume_login(None, code.strip())
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
        raise LoginError(f"Garmin rejected the code: {exc}") from exc
    finally:
        with _lock:
            _sessions.pop(session_id, None)

    _persist(config, session.client)


def _persist(config, garmin) -> None:
    """Write tokens to the tokenstore.

    Required explicitly: the return_on_mfa path returns before garminconnect's
    own dump, and resume_login never dumps at all.

    `Garmin.client` is the underlying garminconnect client that owns dump/load;
    there is no `.garth` attribute on Garmin itself.
    """
    config.garth_dir.mkdir(parents=True, exist_ok=True)
    garmin.client.dump(str(config.garth_dir))
    invalidate_token_state("valid")
    logger.info("garmin tokens stored in %s", config.garth_dir)
