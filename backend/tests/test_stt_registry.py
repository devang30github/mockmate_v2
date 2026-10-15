from app.config import Settings
from app.providers.mock.providers import MockSTT
from app.providers.registry import build_providers
from app.providers.stt_faster_whisper import FasterWhisperSTT


def test_mock_is_the_default_speech_provider():
    s = Settings(app_env="dev", provider_mode="mock", stt_mode="mock")
    assert isinstance(build_providers(s).stt, MockSTT)


def test_local_mode_builds_the_adapter_without_loading_a_model():
    s = Settings(
        app_env="dev", provider_mode="mock", stt_mode="local", stt_model="small.en", stt_beam_size=2
    )
    stt = build_providers(s).stt
    assert isinstance(stt, FasterWhisperSTT)
    assert stt.model_name == "faster-whisper:small.en"