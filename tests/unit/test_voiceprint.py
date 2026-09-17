"""Tests for Phase 8.1 — voiceprint verification."""

import numpy as np
import pytest

from packages.shared.voiceprint import (
    FakeEncoder,
    VoiceprintManager,
    cosine_similarity,
    load_wav_mono_float32,
)


@pytest.fixture
def encoder():
    return FakeEncoder()


@pytest.fixture
def manager(encoder, tmp_path):
    return VoiceprintManager(
        encoder=encoder,
        store_path=tmp_path / "voiceprint.npz",
    )


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = np.ones(256, dtype=np.float32)
        assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-5)

    def test_orthogonal_vectors(self):
        a = np.array([1, 0, 0], dtype=np.float32)
        b = np.array([0, 1, 0], dtype=np.float32)
        assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-5)

    def test_zero_vector_returns_zero(self):
        a = np.zeros(256, dtype=np.float32)
        b = np.ones(256, dtype=np.float32)
        assert cosine_similarity(a, b) == 0.0
        assert cosine_similarity(b, a) == 0.0


class TestFakeEncoder:
    def test_deterministic(self):
        enc = FakeEncoder()
        audio = np.random.randn(16000).astype(np.float32)
        e1 = enc.embed(audio)
        e2 = enc.embed(audio)
        np.testing.assert_array_equal(e1, e2)

    def test_output_shape(self):
        enc = FakeEncoder()
        audio = np.random.randn(8000).astype(np.float32)
        emb = enc.embed(audio)
        assert emb.shape == (256,)

    def test_energy_affects_embedding(self):
        enc = FakeEncoder()
        quiet = np.zeros(16000, dtype=np.float32) + 0.01
        loud = np.ones(16000, dtype=np.float32) * 0.5
        e_quiet = enc.embed(quiet)
        e_loud = enc.embed(loud)
        assert not np.array_equal(e_quiet, e_loud)


class TestVoiceprintManager:
    def test_not_enrolled_initially(self, manager):
        assert not manager.is_enrolled()

    def test_enroll_single(self, manager):
        audio = np.random.randn(16000).astype(np.float32)
        ref = manager.enroll_single(audio)
        assert ref.shape == (256,)
        assert manager.is_enrolled()

    def test_enroll_multiple_averages(self, manager):
        segments = [np.random.randn(16000).astype(np.float32) for _ in range(3)]
        ref = manager.enroll(segments)
        # Reference should be average of individual embeddings
        enc = manager.encoder
        individual = [enc.embed(s) for s in segments]
        expected = np.mean(individual, axis=0).astype(np.float32)
        np.testing.assert_array_almost_equal(ref, expected, decimal=5)

    def test_enroll_empty_raises(self, manager):
        with pytest.raises(ValueError, match="at least one"):
            manager.enroll([])

    def test_verify_passes_for_enrolled_voice(self, manager):
        audio = np.random.randn(16000).astype(np.float32)
        manager.enroll_single(audio)
        # Same audio should pass
        passed, sim = manager.verify(audio)
        assert passed is True
        assert sim >= manager.threshold

    def test_verify_fails_with_no_enrollment(self, tmp_path, encoder):
        manager = VoiceprintManager(encoder=encoder, store_path=tmp_path / "vp.npz")
        audio = np.random.randn(16000).astype(np.float32)
        passed, sim = manager.verify(audio)
        assert passed is False
        assert sim == 0.0

    def test_persistence_across_instances(self, encoder, tmp_path):
        path = tmp_path / "vp.npz"
        audio = np.random.randn(16000).astype(np.float32)

        m1 = VoiceprintManager(encoder=encoder, store_path=path)
        m1.enroll_single(audio)
        assert m1.is_enrolled()

        m2 = VoiceprintManager(encoder=encoder, store_path=path)
        assert m2.is_enrolled()
        passed, _ = m2.verify(audio)
        assert passed is True

    def test_delete(self, manager):
        audio = np.random.randn(16000).astype(np.float32)
        manager.enroll_single(audio)
        assert manager.is_enrolled()
        deleted = manager.delete()
        assert deleted is True
        assert not manager.is_enrolled()

    def test_delete_returns_false_when_empty(self, manager):
        assert manager.delete() is False

    def test_custom_threshold(self, encoder, tmp_path):
        manager = VoiceprintManager(
            encoder=encoder,
            store_path=tmp_path / "vp.npz",
            threshold=0.99,  # very high — even same audio may not pass with FakeEncoder
        )
        audio = np.random.randn(16000).astype(np.float32)
        manager.enroll_single(audio)
        # With FakeEncoder + high threshold, similarity is 1.0 for same audio
        passed, _ = manager.verify(audio)
        assert passed is True  # FakeEncoder is deterministic, same input = exact same output


class TestPermissionEngineIntegration:
    def test_voiceprint_injected(self):
        from packages.shared.permission_engine import PermissionEngine
        from packages.shared.voiceprint import FakeEncoder, VoiceprintManager

        PermissionEngine.reset()
        vp = VoiceprintManager(encoder=FakeEncoder())
        PermissionEngine.set_voiceprint(vp)
        assert PermissionEngine._voiceprint is vp
        PermissionEngine.set_voiceprint(None)
        PermissionEngine.reset()

    def test_critical_requires_second_factor(self):
        from packages.shared.permission_engine import PermissionEngine
        from packages.shared.schemas import ToolRegistration, RiskTier

        PermissionEngine.reset()
        PermissionEngine.register(
            ToolRegistration(
                tool_name="test.critical",
                tier=RiskTier.CRITICAL,
                confirmation_required=True,
                description="Critical action",
                diff_card_fields=["target"],
            )
        )
        result = PermissionEngine.classify("test.critical")
        assert result.requires_second_factor is True
        PermissionEngine.reset()

    def test_second_factor_fails_without_enrollment(self):
        from packages.shared.permission_engine import PermissionEngine
        from packages.shared.voiceprint import FakeEncoder, VoiceprintManager

        PermissionEngine.reset()
        vp = VoiceprintManager(
            encoder=FakeEncoder(),
            store_path="nonexistent_path.npz",  # no file exists
        )
        PermissionEngine.set_voiceprint(vp)
        passed, reason = PermissionEngine.verify_critical_second_factor(
            audio=np.zeros(16000, dtype=np.float32)
        )
        assert passed is False
        assert "not enrolled" in reason
        PermissionEngine.set_voiceprint(None)
        PermissionEngine.reset()

    def test_second_factor_passes_with_enrollment(self):
        from packages.shared.permission_engine import PermissionEngine
        from packages.shared.voiceprint import FakeEncoder, VoiceprintManager

        PermissionEngine.reset()
        vp = VoiceprintManager(encoder=FakeEncoder())
        audio = np.random.randn(16000).astype(np.float32)
        vp.enroll_single(audio)
        PermissionEngine.set_voiceprint(vp)

        passed, reason = PermissionEngine.verify_critical_second_factor(audio=audio)
        assert passed is True
        assert "verified" in reason
        PermissionEngine.set_voiceprint(None)
        PermissionEngine.reset()
