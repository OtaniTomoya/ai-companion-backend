import logging
import time

from aiavatar.adapter.websocket.server import AIAvatarWebSocketServer
from aiavatar.adapter.models import AIAvatarResponse
from aiavatar.sts.models import STSResponse
from aiavatar.sts.stt.openai import OpenAISpeechRecognizer

from .prompts import JOURNAL_MODE_PROMPT_JA, SYSTEM_PROMPT_JA
from .settings import Settings


logger = logging.getLogger(__name__)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "active", "start"}
    return bool(value)


def _list_lines(value, fallback: str) -> str:
    if not value:
        return f"- {fallback}"
    if isinstance(value, str):
        return value
    return "\n".join(f"- {item}" for item in value)


def _mapping_lines(value, fallback: str) -> str:
    if not value:
        return f"- {fallback}"
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(f"- {key}: {status}" for key, status in value.items())
    return "\n".join(f"- {item}" for item in value)


def journal_system_prompt(params: dict | None) -> str:
    if not params or params.get("mode") != "journal":
        return SYSTEM_PROMPT_JA

    return SYSTEM_PROMPT_JA + JOURNAL_MODE_PROMPT_JA.format(
        journal_date=params.get("journal_date") or "今日",
        required_slots=_list_lines(params.get("required_slots"), "今日全体の印象"),
        optional_slots=_list_lines(params.get("optional_slots"), "必要に応じた補足"),
        available_context=_list_lines(params.get("available_context"), "会話のみ"),
        progress_summary=params.get("progress_summary") or "まだ聞き取り開始前",
        slot_statuses=_mapping_lines(params.get("slot_statuses"), "今日全体の印象: missing"),
    )


class ResilientAIAvatarWebSocketServer(AIAvatarWebSocketServer):
    async def send_response(self, aiavatar_response: AIAvatarResponse):
        websocket = self.websockets.get(aiavatar_response.session_id)
        if websocket is None:
            logger.info("Skip response for inactive session: %s", aiavatar_response.session_id)
            return

        try:
            await websocket.send_text(aiavatar_response.model_dump_json())
        except RuntimeError as ex:
            logger.info(
                "Skip response because websocket is already closed: session_id=%s error=%s",
                aiavatar_response.session_id,
                ex,
            )


class TTSUnavailableError(RuntimeError):
    pass


class StrictSpeechSynthesizer:
    """Report TTS failures to the client instead of falling back to text-only."""

    def __init__(self, delegate, *, provider_name: str, base_url: str | None = None):
        self.delegate = delegate
        self.provider_name = provider_name
        self.base_url = base_url
        self.last_error_message: str | None = None

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def get_config(self) -> dict:
        config = self.delegate.get_config() if hasattr(self.delegate, "get_config") else {}
        config["text_fallback_enabled"] = False
        config["strict_error_reporting"] = True
        config["provider_name"] = self.provider_name
        config["last_error_message"] = self.last_error_message
        return config

    def set_config(self, config: dict) -> dict:
        if hasattr(self.delegate, "set_config"):
            return self.delegate.set_config(config)
        return {}

    async def close(self):
        if hasattr(self.delegate, "close"):
            await self.delegate.close()

    async def synthesize(self, text: str, style_info: dict = None, language: str = None) -> bytes:
        self.last_error_message = None
        try:
            return await self.delegate.synthesize(text, style_info=style_info, language=language)
        except Exception as ex:
            self.last_error_message = self._friendly_error_message(ex)
            logger.error("TTS failed without fallback: %s", self.last_error_message)
            raise TTSUnavailableError(self.last_error_message) from ex

    def _friendly_error_message(self, ex: Exception) -> str:
        if self.provider_name == "VOICEVOX":
            return (
                "VOICEVOXに接続できません。VOICEVOX / AivisSpeech を起動し、"
                f"VOICEVOX_BASE_URL={self.base_url or '未設定'} を確認してください。"
            )
        return f"{self.provider_name} の音声合成に失敗しました: {ex}"


class ConfigurableOpenAISpeechRecognizer(OpenAISpeechRecognizer):
    def __init__(self, *args, model: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.model = model
        self.last_error_message: str | None = None

    def get_config(self) -> dict:
        config = super().get_config()
        config["model"] = self.model
        return config

    async def transcribe(self, data: bytes) -> str:
        self.last_error_message = None
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
        }
        form_data = {
            "model": self.model,
        }
        if self.language and not self.alternative_languages:
            form_data["language"] = self.language.split("-")[0] if "-" in self.language else self.language

        files = {
            "file": ("voice.wav", self.to_wave_file(data), "audio/wav"),
        }
        try:
            resp = await self.http_client.request(
                method="POST",
                url="https://api.openai.com/v1/audio/transcriptions",
                headers=headers,
                data=form_data,
                files=files,
            )
            if resp.status_code >= 400:
                self.last_error_message = self._friendly_openai_stt_error(resp.status_code, resp.text)
                return None
            return resp.json()["text"]
        except Exception as ex:
            self.last_error_message = f"OpenAI音声認識でエラーが発生しました: {ex}"
            return None

    def _friendly_openai_stt_error(self, status_code: int, body: str) -> str:
        if status_code == 429 and "insufficient_quota" in body:
            return (
                "OpenAI APIの利用枠または課金設定が不足しているため、音声認識できません。"
                "OpenAIのBilling/Usageを確認するか、別のSTT設定に切り替えてください。"
            )
        if status_code == 401:
            return "OpenAI APIキーが無効です。.env の OPENAI_API_KEY を確認してください。"
        return f"OpenAI音声認識APIが失敗しました。status={status_code}"


def trust_silero_torch_hub_repo():
    """Avoid an interactive torch.hub trust prompt during server startup.

    AIAvatarKit currently loads Silero VAD through torch.hub without passing
    trust_repo=True. Uvicorn cannot answer the prompt, so we pre-register only
    the exact repository used by the VAD component.
    """

    import torch

    hub_dir = torch.hub.get_dir()
    trusted_list = f"{hub_dir}/trusted_list"
    repo_id = "snakers4_silero-vad"

    import os

    os.makedirs(hub_dir, exist_ok=True)
    if os.path.exists(trusted_list):
        with open(trusted_list, "r", encoding="utf-8") as file:
            trusted = {line.strip() for line in file if line.strip()}
    else:
        trusted = set()

    if repo_id not in trusted:
        with open(trusted_list, "a", encoding="utf-8") as file:
            file.write(repo_id + "\n")


def build_stt(settings: Settings):
    return ConfigurableOpenAISpeechRecognizer(
        openai_api_key=settings.require_secret("openai_api_key", "OPENAI_API_KEY"),
        model=settings.openai_stt_model,
        sample_rate=16000,
        language=settings.openai_stt_language,
        debug=settings.aiavatar_debug,
    )


def build_vad(settings: Settings, stt):
    use_stream_vad = settings.aiavatar_vad_mode == "silero_stream"

    if use_stream_vad:
        trust_silero_torch_hub_repo()

        from aiavatar.sts.vad.stream import SileroStreamSpeechDetector

        return SileroStreamSpeechDetector(
            speech_recognizer=stt,
            silence_duration_threshold=settings.aiavatar_vad_silence_seconds,
            segment_silence_threshold=settings.aiavatar_vad_segment_silence_seconds,
            max_duration=settings.aiavatar_vad_max_seconds,
            min_duration=settings.aiavatar_vad_min_seconds,
            sample_rate=16000,
            channels=1,
            debug=settings.aiavatar_debug,
        )

    from aiavatar.sts.vad.silero import SileroSpeechDetector

    trust_silero_torch_hub_repo()

    return SileroSpeechDetector(
        silence_duration_threshold=settings.aiavatar_vad_silence_seconds,
        max_duration=settings.aiavatar_vad_max_seconds,
        min_duration=settings.aiavatar_vad_min_seconds,
        sample_rate=16000,
        channels=1,
        debug=settings.aiavatar_debug,
    )


def build_llm(settings: Settings):
    openai_api_key = settings.require_secret("openai_api_key", "OPENAI_API_KEY")
    common = {
        "openai_api_key": openai_api_key,
        "system_prompt": SYSTEM_PROMPT_JA,
        "model": settings.openai_llm_model,
        "reasoning_effort": settings.openai_llm_reasoning_effort,
        "voice_text_tag": "answer",
        "debug": settings.aiavatar_debug,
    }

    def configure_prompt(service):
        @service.get_system_prompt
        async def get_system_prompt(context_id: str, user_id: str, system_prompt_params: dict | None):
            return journal_system_prompt(system_prompt_params)

        return service

    from aiavatar.sts.llm.chatgpt import ChatGPTService

    return configure_prompt(ChatGPTService(**common))


def build_tts(settings: Settings):
    cache_dir = settings.tts_cache_dir_for("voicevox")

    from aiavatar.sts.tts.voicevox import VoicevoxSpeechSynthesizer

    return StrictSpeechSynthesizer(VoicevoxSpeechSynthesizer(
        base_url=settings.voicevox_base_url,
        speaker=settings.voicevox_speaker,
        cache_dir=cache_dir,
        cache_ext="wav",
        debug=settings.aiavatar_debug,
    ), provider_name="VOICEVOX", base_url=settings.voicevox_base_url)


def build_aiavatar_server(settings: Settings) -> AIAvatarWebSocketServer:
    stt = build_stt(settings)
    vad = build_vad(settings, stt)
    llm = build_llm(settings)
    tts = build_tts(settings)

    server = ResilientAIAvatarWebSocketServer(
        vad=vad,
        stt=stt,
        llm=llm,
        tts=tts,
        merge_request_threshold=settings.aiavatar_merge_request_threshold,
        timestamp_interval_seconds=600,
        timestamp_timezone=settings.app_timezone,
        invoke_timeout=settings.aiavatar_invoke_timeout_seconds,
        use_invoke_queue=True,
        api_key=settings.require_secret("aiavatar_api_key", "AIAVATAR_API_KEY"),
        send_voiced=True,
        response_audio_chunk_size=settings.aiavatar_response_audio_chunk_size,
        debug=settings.aiavatar_debug,
    )

    if isinstance(stt, ConfigurableOpenAISpeechRecognizer):
        @server.on_response
        async def expose_stt_error(aiavatar_response, response: STSResponse):
            if response.type != "canceled" or not stt.last_error_message:
                return
            aiavatar_response.type = "error"
            aiavatar_response.text = stt.last_error_message
            aiavatar_response.voice_text = stt.last_error_message
            aiavatar_response.metadata["error"] = stt.last_error_message
            stt.last_error_message = None

    if isinstance(tts, StrictSpeechSynthesizer):
        @server.on_response
        async def expose_tts_error(aiavatar_response, response: STSResponse):
            if response.type != "error" or not tts.last_error_message:
                return
            aiavatar_response.text = tts.last_error_message
            aiavatar_response.voice_text = tts.last_error_message
            aiavatar_response.metadata["error"] = tts.last_error_message
            tts.last_error_message = None

    install_journal_mode_hooks(server)
    install_camera_context_hooks(server, settings)

    return server


def install_camera_context_hooks(server: AIAvatarWebSocketServer, settings: Settings) -> None:
    """Attach the latest camera frame to the next LLM request for this session."""

    session_key = "latest_camera_context"

    @server.on_request
    async def remember_camera_context(request):
        files = request.files or []
        if not request.session_id or not files:
            return

        image_file = next(
            (
                file
                for file in files
                if isinstance(file, dict)
                and str(file.get("url", "")).startswith("data:image/")
            ),
            None,
        )
        if not image_file:
            return

        metadata = request.metadata or {}
        server.sts.vad.set_session_data(
            request.session_id,
            session_key,
            {
                "files": [image_file],
                "captured_at": metadata.get("camera_captured_at"),
                "stored_at": time.time(),
            },
            create_session=True,
        )

    @server.sts.on_before_llm
    async def attach_latest_camera_context(request):
        if request.files:
            return

        context = server.sts.vad.get_session_data(request.session_id, session_key)
        if not context:
            return

        stored_at = float(context.get("stored_at") or 0)
        if time.time() - stored_at > settings.camera_context_ttl_seconds:
            return

        files = context.get("files") or []
        if not files:
            return

        request.files = files
        camera_note = (
            "現在のカメラ画像が添付されています。"
            "ユーザーの発話と画像を合わせて、見えている状況に短く自然に反応してください。"
        )
        if request.text:
            request.text = f"{request.text}\n\n{camera_note}"
        else:
            request.text = camera_note


def install_journal_mode_hooks(server: AIAvatarWebSocketServer) -> None:
    """Keep journal prompt state on the session so both text and voice share it."""

    session_key = "journal_system_prompt_params"

    def params_from_metadata(metadata: dict | None) -> dict | None:
        if not metadata:
            return None
        raw_params = metadata.get("journal_prompt")
        if not isinstance(raw_params, dict):
            return None
        params = dict(raw_params)
        params["mode"] = "journal"
        return params

    def apply_journal_state(session_id: str, enabled: bool, params: dict | None = None) -> None:
        server.sts.vad.set_session_data(session_id, "journal_mode_enabled", enabled, create_session=True)
        server.sts.vad.set_session_data(
            session_id,
            session_key,
            params if enabled else None,
            create_session=True,
        )
        server.sts.vad.set_session_data(
            session_id,
            "system_prompt_params",
            params if enabled else None,
            create_session=True,
        )

    @server.on_request
    async def remember_journal_mode(request):
        metadata = request.metadata or {}
        journal_mode = metadata.get("journal_mode")
        params = params_from_metadata(metadata) or request.system_prompt_params

        if journal_mode is not None:
            enabled = _as_bool(journal_mode)
            apply_journal_state(request.session_id, enabled, params if enabled else None)

        if params and params.get("mode") == "journal":
            apply_journal_state(request.session_id, True, params)
            request.system_prompt_params = params

    @server.sts.on_before_llm
    async def attach_journal_prompt(request):
        if request.system_prompt_params and request.system_prompt_params.get("mode") == "journal":
            return
        if not server.sts.vad.get_session_data(request.session_id, "journal_mode_enabled"):
            return
        params = server.sts.vad.get_session_data(request.session_id, session_key)
        if params:
            request.system_prompt_params = params
