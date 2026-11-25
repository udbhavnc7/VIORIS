"""Tests for gateway call-audio buffering (Phase 8.5, 10b)."""

import numpy as np

from services.api_gateway.app.call_audio import (
    CallAudioBuffer,
    float32_to_pcm16_bytes,
    pcm16_bytes_to_float32,
)


def _noise(seconds: float, rate: int = 16000) -> np.ndarray:
    n = int(seconds * rate)
    return (np.linspace(-0.5, 0.5, n)).astype(np.float32)


class TestCallAudioBuffer:
    def test_push_and_drain(self):
        buf = CallAudioBuffer()
        a = _noise(0.1)
        b = _noise(0.1)
        buf.push(a)
        buf.push(b)
        assert buf.has_speech()
        assert abs(buf.duration - 0.2) < 0.01
        drained = buf.drain()
        assert np.allclose(drained, np.concatenate([a, b]))
        assert not buf.has_speech()

    def test_empty_buffer_not_speech(self):
        assert not CallAudioBuffer().has_speech()
        buf = CallAudioBuffer()
        buf.push(np.zeros(0, dtype=np.float32))
        assert not buf.has_speech()

    def test_tail_is_silent_when_last_part_quiet(self):
        buf = CallAudioBuffer()
        buf.push(_noise(1.0))
        assert not buf.tail_is_silent()
        buf2 = CallAudioBuffer()
        buf2.push(_noise(0.9))
        buf2.push(np.zeros(16000, dtype=np.float32))
        assert buf2.tail_is_silent()


class TestPcmConversion:
    def test_pcm16_roundtrip(self):
        audio = np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32)
        decoded = pcm16_bytes_to_float32(float32_to_pcm16_bytes(audio))
        assert decoded.dtype == np.float32
        assert np.allclose(decoded, audio, atol=1 / 32767 + 1e-5)
