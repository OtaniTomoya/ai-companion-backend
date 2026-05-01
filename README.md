# AI Companion Backend

AI Companion iOSアプリ用のFastAPI WebSocketバックエンドです。

iOSクライアントは別リポジトリで管理しています。

```text
https://github.com/OtaniTomoya/ai-companion-ios
```

このバックエンドは、ローカル開発とプロトコル参照を主目的にしています。AIAvatar互換のWebSocketエンドポイントを通して、リアルタイム音声会話を提供します。

## Pipeline

- STT: OpenAI `gpt-4o-mini-transcribe`
- LLM: OpenAI ChatGPT service `gpt-5-nano`
- TTS: VOICEVOX / AivisSpeech互換のローカルエンドポイント
- VAD: `aiavatar` 経由のSilero VAD
- WebSocket: `/ws`
- Health check: `/health`

## 必要環境

- macOSまたはLinux
- `uv` で管理するPython環境
- OpenAI APIキー
- クライアント認証用の共有AIAvatar APIキー
- VOICEVOXまたはAivisSpeech互換のローカル音声合成サーバー

macOSでは、`pyaudio` 依存関係のためにPortAudioをインストールします。

```bash
brew install portaudio
```

## Setup

```bash
cp .env.example .env
```

実際のシークレットは `.env` だけに書いてください。

最低限必要な値は次の通りです。

```env
OPENAI_API_KEY=<your-openai-api-key>
AIAVATAR_API_KEY=<choose-a-shared-api-key>
OPENAI_LLM_MODEL=gpt-5-nano
OPENAI_LLM_REASONING_EFFORT=minimal
OPENAI_STT_MODEL=gpt-4o-mini-transcribe
VOICEVOX_BASE_URL=http://127.0.0.1:50021
VOICEVOX_SPEAKER=8
```

依存関係をインストールし、バックエンドを起動します。

```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
# {"ok": true}
```

最初のWebSocket接続では、VADモデルの初期化またはダウンロードにより時間がかかることがあります。

## iOSクライアント接続

iOSシミュレータからローカルバックエンドへ接続する場合:

```text
ws://127.0.0.1:8000/ws
```

同じネットワーク上の実機iPhoneから接続する場合:

```text
ws://<mac-lan-ip>:8000/ws
```

公開環境や外部バックエンドではTLSを使ってください。

```text
wss://<your-backend-host>/ws
```

iOSアプリの設定画面には、同じ `AIAVATAR_API_KEY` を入力してください。

## WebSocket Protocol

iOSクライアントは次のイベントを送信します。

- `type: "start"`: セッション開始
- `type: "data"` + `audio_data`: PCM音声チャンク
- `type: "invoke"` + `text`: テキスト入力による呼び出し
- `type: "camera_context"` + `files[0].url`: vision modeでの直近カメラフレーム
- `type: "config"` + `metadata.journal_mode`: journal mode状態とprompt context
- `type: "stop"`: セッション停止

バックエンドは、`chunk`、`final`、`voiced`、`stop`、`error` などのAIAvatarイベントを返します。合成音声は `audio_data` にbase64 WAVデータとして返します。

## プライバシーとローカルデータ

このバックエンドは、iOSアプリから送信されるマイク音声、会話テキスト、カメラフレーム、journal prompt contextを処理します。自分が管理できる環境でのみ実行し、認証とTLSなしで外部公開しないでください。

コミットしてはいけないもの:

- `.env`
- `.venv/`
- `.cache/`
- `aiavatar.db`
- `recorded_voices/`
- `__pycache__/`

## License

このプロジェクト全体に対するオープンソースライセンスは、現時点では付与していません。GitHubで公開されているため閲覧はできますが、後日ライセンスを追加しない限り、再利用、再配布、派生物の作成は許可していません。
