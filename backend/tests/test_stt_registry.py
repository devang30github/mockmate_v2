from app.config import Settings
from app.providers.mock.providers import MockSTT
from app.providers.registry import build_providers
from app.providers.stt_faster_whisper import FasterWhisperSTT
from app.providers.caption_sherpa import SherpaCaptionEngine

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

def test_captions_are_off_by_default():
    s = Settings(app_env="dev", provider_mode="mock", stream_asr_mode="off")
    assert build_providers(s).captions is None


def test_sherpa_mode_builds_the_engine_without_loading_a_model():
    s = Settings(
        app_env="dev", provider_mode="mock", stream_asr_mode="sherpa",
        stream_asr_model_dir="models/x", stream_asr_threads=3,
    )
    engine = build_providers(s).captions
    assert isinstance(engine, SherpaCaptionEngine)
    assert engine.model_dir.name == "x"


def test_relative_model_dir_resolves_against_the_repo_root():
    s = Settings(app_env="dev", provider_mode="mock", stream_asr_model_dir="models/x")
    assert s.stream_asr_path.is_absolute()
    assert s.stream_asr_path.parts[-2:] == ("models", "x")


def test_absolute_model_dir_is_kept(tmp_path):
    s = Settings(app_env="dev", provider_mode="mock", stream_asr_model_dir=str(tmp_path))
    assert s.stream_asr_path == tmp_path