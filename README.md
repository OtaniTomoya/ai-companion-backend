# AI Companion Backend

FastAPI WebSocket backend for the AI Companion iOS app.

The iOS client repository is managed separately:

```text
https://github.com/OtaniTomoya/ai-companion-ios
```

This backend is intended for local development and protocol reference. It
provides realtime voice conversation through an AIAvatar-compatible WebSocket
endpoint.

## Pipeline

- STT: OpenAI `gpt-4o-mini-transcribe`
- LLM: OpenAI ChatGPT service `gpt-5-nano`
- TTS: VOICEVOX / AivisSpeech-compatible local endpoint
- VAD: Silero VAD through `aiavatar`
- WebSocket: `/ws`
- Health check: `/health`

## Requirements

- macOS or Linux
- Python managed through `uv`
- OpenAI API key
- Shared AIAvatar API key for client authentication
- VOICEVOX or AivisSpeech-compatible local TTS server

On macOS, install PortAudio for the `pyaudio` dependency:

```bash
brew install portaudio
```

## Setup

```bash
cp .env.example .env
```

Write real secrets only in `.env`.

Minimum required values:

```env
OPENAI_API_KEY=<your-openai-api-key>
AIAVATAR_API_KEY=<choose-a-shared-api-key>
OPENAI_LLM_MODEL=gpt-5-nano
OPENAI_LLM_REASONING_EFFORT=minimal
OPENAI_STT_MODEL=gpt-4o-mini-transcribe
VOICEVOX_BASE_URL=http://127.0.0.1:50021
VOICEVOX_SPEAKER=8
```

Then install dependencies and start the backend:

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
# {"ok": true}
```

The first WebSocket connection can take longer because the VAD model may be
initialized or downloaded.

## iOS Client Connection

iOS simulator with local backend:

```text
ws://127.0.0.1:8000/ws
```

Physical iPhone on the same network:

```text
ws://<mac-lan-ip>:8000/ws
```

Public or external backends should use TLS:

```text
wss://<your-backend-host>/ws
```

Enter the same `AIAVATAR_API_KEY` in the iOS app settings.

## WebSocket Protocol

The iOS client sends:

- `type: "start"`: session start
- `type: "data"` + `audio_data`: PCM audio chunk
- `type: "invoke"` + `text`: text invocation
- `type: "camera_context"` + `files[0].url`: recent camera frame in vision mode
- `type: "config"` + `metadata.journal_mode`: journal mode state and prompt
  context
- `type: "stop"`: session stop

The backend returns AIAvatar events such as `chunk`, `final`, `voiced`, `stop`,
and `error`. Synthesized audio is returned as base64 WAV data in `audio_data`.

## Privacy And Local Data

This backend can process microphone audio, conversation text, camera frames, and
journal prompt context sent by the iOS app. Run it only in an environment you
control, and do not expose it without authentication and TLS.

Do not commit:

- `.env`
- `.venv/`
- `.cache/`
- `aiavatar.db`
- `recorded_voices/`
- `__pycache__/`

## License

No project-wide open source license is currently granted. Public GitHub access
allows viewing the repository, but reuse, redistribution, or derivative works
are not permitted unless a license is added later.
