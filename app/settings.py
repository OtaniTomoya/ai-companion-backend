from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Environment-driven settings.

    Real secrets belong in backend/.env. Use backend/.env.example as the map for
    what to write where. This class intentionally keeps defaults non-secret.
    """

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API keys: write real values in backend/.env, never in Python or Swift files.
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    aiavatar_api_key: SecretStr | None = Field(default=None, alias="AIAVATAR_API_KEY")
    app_timezone: str = Field(default="Asia/Tokyo", alias="APP_TIMEZONE")
    aiavatar_debug: bool = Field(default=False, alias="AIAVATAR_DEBUG")

    openai_llm_model: str = Field(default="gpt-5-nano", alias="OPENAI_LLM_MODEL")
    openai_llm_reasoning_effort: str | None = Field(default="minimal", alias="OPENAI_LLM_REASONING_EFFORT")

    openai_stt_model: str = Field(default="gpt-4o-mini-transcribe", alias="OPENAI_STT_MODEL")
    openai_stt_language: str = Field(default="ja", alias="OPENAI_STT_LANGUAGE")

    aiavatar_vad_mode: str = Field(default="auto", alias="AIAVATAR_VAD_MODE")
    aiavatar_vad_silence_seconds: float = Field(default=0.5, alias="AIAVATAR_VAD_SILENCE_SECONDS")
    aiavatar_vad_segment_silence_seconds: float = Field(default=0.05, alias="AIAVATAR_VAD_SEGMENT_SILENCE_SECONDS")
    aiavatar_vad_max_seconds: float = Field(default=10.0, alias="AIAVATAR_VAD_MAX_SECONDS")
    aiavatar_vad_min_seconds: float = Field(default=0.2, alias="AIAVATAR_VAD_MIN_SECONDS")

    voicevox_base_url: str = Field(default="http://127.0.0.1:50021", alias="VOICEVOX_BASE_URL")
    voicevox_speaker: int = Field(default=8, alias="VOICEVOX_SPEAKER")

    aiavatar_merge_request_threshold: float = Field(default=3.0, alias="AIAVATAR_MERGE_REQUEST_THRESHOLD")
    aiavatar_invoke_timeout_seconds: float = Field(default=60.0, alias="AIAVATAR_INVOKE_TIMEOUT_SECONDS")
    aiavatar_response_audio_chunk_size: int = Field(default=0, alias="AIAVATAR_RESPONSE_AUDIO_CHUNK_SIZE")
    aiavatar_tts_cache_dir: str | None = Field(default=".cache/tts", alias="AIAVATAR_TTS_CACHE_DIR")
    camera_context_ttl_seconds: float = Field(default=4.0, alias="CAMERA_CONTEXT_TTL_SECONDS")

    def secret(self, field_name: str) -> str | None:
        value = getattr(self, field_name)
        if value is None:
            return None
        if isinstance(value, SecretStr):
            raw = value.get_secret_value().strip()
        else:
            raw = str(value).strip()
        return raw or None

    def require_secret(self, field_name: str, env_name: str) -> str:
        value = self.secret(field_name)
        if not value:
            raise RuntimeError(
                f"{env_name} is required. Copy backend/.env.example to backend/.env "
                f"and write {env_name}=... there."
            )
        return value

    def optional_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    def tts_cache_dir_for(self, provider: str) -> str | None:
        base = self.optional_text(self.aiavatar_tts_cache_dir)
        if base is None:
            return None
        path = Path(base)
        if not path.is_absolute():
            path = BACKEND_ROOT / path
        return str(path / provider)
