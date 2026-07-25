"""Tests for elidia.creative — image, video, audio, local, display."""
import pytest

from elidia.creative.image import IMAGE_MODELS, list_image_models
from elidia.creative.video import VIDEO_MODELS, list_video_models
from elidia.creative.audio import (
    MUSIC_MODELS,
    TTS_MODELS,
    list_audio_models,
    list_tts_voices,
)
from elidia.creative.local import LOCAL_IMAGE_MODELS, check_local_available, list_local_models
from elidia.creative.display import TerminalProtocol, detect_terminal


class TestImageModels:
    def test_has_models(self):
        assert len(IMAGE_MODELS) >= 4

    def test_list_image_models(self):
        models = list_image_models()
        assert len(models) >= 4
        assert all("model" in m for m in models)
        assert all("vendor" in m for m in models)

    def test_known_models(self):
        ids = list(IMAGE_MODELS.keys())
        assert "flux-1.1-pro" in ids or "dall-e-3" in ids


class TestVideoModels:
    def test_has_models(self):
        assert len(VIDEO_MODELS) >= 2

    def test_list_video_models(self):
        models = list_video_models()
        assert len(models) >= 2
        assert all("model" in m for m in models)


class TestAudioModels:
    def test_tts_models(self):
        assert len(TTS_MODELS) >= 2

    def test_music_models(self):
        assert len(MUSIC_MODELS) >= 1

    def test_list_audio_models(self):
        models = list_audio_models()
        assert len(models) >= 3

    def test_tts_voices(self):
        voices = list_tts_voices("tts-1")
        assert "alloy" in voices
        assert len(voices) >= 4

    def test_tts_voices_unknown_model(self):
        voices = list_tts_voices("nonexistent")
        assert voices == []


class TestLocalModels:
    def test_local_models_defined(self):
        assert len(LOCAL_IMAGE_MODELS) >= 1

    def test_check_local_available(self):
        status = check_local_available()
        assert "ready" in status
        # Will be True or False depending on system

    def test_list_local_models(self):
        models = list_local_models()
        assert len(models) >= 1
        assert all("model" in m for m in models)


class TestTerminalDisplay:
    def test_protocol_enum(self):
        assert TerminalProtocol.ITERM2.value == "iterm2"
        assert TerminalProtocol.KITTY.value == "kitty"
        assert TerminalProtocol.SIXEL.value == "sixel"
        assert TerminalProtocol.NONE.value == "none"

    def test_detect_terminal(self):
        proto = detect_terminal()
        assert proto in TerminalProtocol
