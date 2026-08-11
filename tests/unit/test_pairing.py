"""Api gateway pairing unit tests: token lifecycle, JWT scoping, revocation."""

import time

import jwt
import pytest

from services.api_gateway.app.pairing import (
    DeviceRevokedError,
    InvalidDeviceTokenError,
    InvalidPairingTokenError,
    PairingExpiredError,
    PairingStore,
)


@pytest.fixture
def store():
    return PairingStore(secret="test-secret")


class TestPairing:
    def test_issue_pairing_creates_scannable_token(self):
        s = PairingStore(secret="x")
        proof = s.issue_pairing("phone")
        assert proof.token.startswith("vioris-pair-")
        assert proof.device_id.startswith("dev_")
        assert proof.expires_at > time.time()
        assert s.list_devices()[0].name == "phone"

    def test_exchange_returns_device_scoped_jwt(self):
        s = PairingStore(secret="x")
        proof = s.issue_pairing("phone")
        traded = s.exchange(proof.device_id, proof.token)
        assert traded.device_id == proof.device_id
        payload = jwt.decode(traded.jwt, s._secret, algorithms=["HS256"])
        assert payload["sub"] == proof.device_id
        assert payload["type"] == "device"

    def test_single_use_token(self):
        s = PairingStore(secret="x")
        proof = s.issue_pairing("phone")
        s.exchange(proof.device_id, proof.token)
        with pytest.raises(InvalidPairingTokenError):
            s.exchange(proof.device_id, proof.token)

    def test_wrong_device_id_rejected(self):
        s = PairingStore(secret="x")
        proof = s.issue_pairing("phone")
        with pytest.raises(InvalidPairingTokenError):
            s.exchange("dev_another", proof.token)

    def test_expired_token(self):
        s = PairingStore(secret="x", token_ttl=86400)
        proof = s.issue_pairing("phone")
        s._pending[proof.token] = (proof.device_id, time.time() - 5)
        with pytest.raises(PairingExpiredError):
            s.exchange(proof.device_id, proof.token)


class TestVerify:
    def test_verify_returns_device(self):
        s = PairingStore(secret="x")
        proof = s.issue_pairing("phone")
        traded = s.exchange(proof.device_id, proof.token)
        dev = s.verify(traded.jwt)
        assert dev.device_id == proof.device_id

    def test_verify_rejects_garbage(self):
        s = PairingStore(secret="x")
        with pytest.raises(InvalidDeviceTokenError):
            s.verify("not-a-jwt")

    def test_verify_rejects_empty(self):
        s = PairingStore(secret="x")
        with pytest.raises(InvalidDeviceTokenError):
            s.verify("")

    def test_verify_rejects_token_for_other_secret(self):
        s = PairingStore(secret="a")
        s2 = PairingStore(secret="b")
        proof = s.issue_pairing("phone")
        traded = s.exchange(proof.device_id, proof.token)
        with pytest.raises(InvalidDeviceTokenError):
            s2.verify(traded.jwt)


class TestRevoke:
    def test_revoke_blocks_valid_jwt(self):
        s = PairingStore(secret="x")
        proof = s.issue_pairing("phone")
        traded = s.exchange(proof.device_id, proof.token)
        assert s.verify(traded.jwt).device_id == proof.device_id
        assert s.revoke(proof.device_id) is True
        with pytest.raises(DeviceRevokedError):
            s.verify(traded.jwt)

    def test_revoke_unknown_returns_false(self):
        s = PairingStore(secret="x")
        assert s.revoke("dev_nobody") is False

    def test_devices_list_shows_revoked(self):
        s = PairingStore(secret="x")
        proof = s.issue_pairing("phone")
        s.revoke(proof.device_id)
        listed = s.list_devices()
        assert listed[0].revoked is True