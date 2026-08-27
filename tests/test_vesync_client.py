"""VeSync client: credential handling and weigh-in endpoint fallback."""

from types import SimpleNamespace

import pytest

from garmin_stats_sync.vesync_client import (
    LEGACY_WEIGH_DATA_ENDPOINT,
    WEIGH_DATA_ENDPOINT,
    VeSyncError,
    VeSyncScaleClient,
    _looks_like_scale,
)


class FakeManager(SimpleNamespace):
    """Stands in for pyvesync's VeSync manager.

    Every credential method is async, exactly as in pyvesync 3.x - calling one
    without awaiting yields a truthy coroutine and silently does nothing.
    """

    def __init__(self, responses=None, login_ok=True, credentials_ok=True):
        super().__init__(
            token="fake-token",
            account_id="fake-account",
            time_zone="America/New_York",
        )
        self._responses = responses or {}
        self._login_ok = login_ok
        self._credentials_ok = credentials_ok
        self.saved_to = None
        self.loaded_from = None
        self.logged_in = False
        self.calls = []

    async def login(self):
        self.logged_in = True
        return self._login_ok

    async def save_credentials(self, filename):
        self.saved_to = filename

    async def load_credentials_from_file(self, filename):
        self.loaded_from = filename
        return self._credentials_ok

    async def async_call_api(self, endpoint, method, json_object=None, headers=None):
        self.calls.append((endpoint, json_object, headers))
        outcome = self._responses.get(endpoint)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, 200


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(
        vesync_email="scale@example.com",
        vesync_password="secret",
        local_tz="America/New_York",
        vesync_credentials=tmp_path / "vesync.json",
    )


def test_looks_like_scale_matches_esf_models():
    assert _looks_like_scale({"deviceType": "ESF-93 V2", "deviceName": "scale"})
    assert _looks_like_scale({"deviceName": "Bathroom Scale"})
    assert not _looks_like_scale({"deviceType": "ESL100", "deviceName": "Lamp"})


@pytest.mark.asyncio
async def test_first_login_saves_credentials(config):
    client = VeSyncScaleClient(config)
    manager = FakeManager()

    await client._authenticate(manager)

    assert manager.logged_in
    assert manager.saved_to == config.vesync_credentials


@pytest.mark.asyncio
async def test_cached_credentials_skip_login(config):
    config.vesync_credentials.write_text("{}")
    client = VeSyncScaleClient(config)
    manager = FakeManager()

    await client._authenticate(manager)

    assert manager.loaded_from == config.vesync_credentials
    assert not manager.logged_in


@pytest.mark.asyncio
async def test_unusable_cached_credentials_fall_back_to_login(config):
    """A stale credentials file must not leave the session unauthenticated."""
    config.vesync_credentials.write_text("{}")
    client = VeSyncScaleClient(config)
    manager = FakeManager(credentials_ok=False)

    await client._authenticate(manager)

    assert manager.logged_in
    assert manager.saved_to == config.vesync_credentials


@pytest.mark.asyncio
async def test_failed_login_raises(config):
    client = VeSyncScaleClient(config)
    with pytest.raises(VeSyncError, match="login failed"):
        await client._authenticate(FakeManager(login_ok=False))


@pytest.mark.asyncio
async def test_weigh_data_falls_back_when_v2_raises(config, weigh_data_payload):
    """pyvesync raises on API error codes, so the fallback must catch."""
    manager = FakeManager(
        responses={
            WEIGH_DATA_ENDPOINT: RuntimeError("MySQL error"),
            LEGACY_WEIGH_DATA_ENDPOINT: weigh_data_payload,
        }
    )
    client = VeSyncScaleClient(config)

    payload = await client._get_weigh_data(manager, "cfg-module")

    assert payload == weigh_data_payload
    assert [call[0] for call in manager.calls] == [
        WEIGH_DATA_ENDPOINT,
        LEGACY_WEIGH_DATA_ENDPOINT,
    ]


@pytest.mark.asyncio
async def test_weigh_data_sends_legacy_headers(config, weigh_data_payload):
    manager = FakeManager(responses={WEIGH_DATA_ENDPOINT: weigh_data_payload})
    client = VeSyncScaleClient(config)

    await client._get_weigh_data(manager, "cfg-module")

    _, body, headers = manager.calls[0]
    assert headers["tk"] == "fake-token"
    assert headers["accountId"] == "fake-account"
    assert body["configModule"] == "cfg-module"
    assert body["method"] == "getWeighingDataV2"


@pytest.mark.asyncio
async def test_weigh_data_raises_when_every_endpoint_fails(config):
    manager = FakeManager(
        responses={
            WEIGH_DATA_ENDPOINT: RuntimeError("MySQL error"),
            LEGACY_WEIGH_DATA_ENDPOINT: RuntimeError("MySQL error"),
        }
    )
    client = VeSyncScaleClient(config)

    with pytest.raises(VeSyncError, match="no weigh-in endpoint"):
        await client._get_weigh_data(manager, "cfg-module")
