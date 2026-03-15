# Prism — AI Gateway

A centralized Node.js backend that routes AI requests to multiple providers through a unified REST + WebSocket API. Supports text generation, image generation, text-to-speech, speech-to-text, embeddings, and more — all behind a single gateway with built-in request logging, cost tracking, and an admin API.

## Prerequisites

- **Node.js** v20+ (ES Modules)
- **MongoDB** — used for conversation storage and request logging
- **MinIO** _(optional)_ — object storage for uploaded files. If not configured, files are stored inline in MongoDB

### Optional Provider Dependencies

These are only needed if you plan to use the corresponding providers:

- **OpenAI API Key** — for GPT models, TTS, STT, embeddings, and image generation
- **Anthropic API Key** — for Claude models
- **Google GenAI API Key** — for Gemini models, TTS, and image generation
- **ElevenLabs API Key** — for ElevenLabs TTS
- **Inworld Credentials** — for Inworld TTS (Base64-encoded `apiKey:apiSecret`)
- **LM Studio** — for local LLM inference (runs on `localhost:1234` by default)

## Supported Providers

| Provider         | Text Gen | Streaming | TTS | STT | Image Gen | Vision | Embeddings | Thinking | Web Search | Code Exec |
| ---------------- | -------- | --------- | --- | --- | --------- | ------ | ---------- | -------- | ---------- | --------- |
| **OpenAI**       | ✅       | ✅        | ✅  | ✅  | ✅        | ✅     | ✅         | ✅       | ✅         | —         |
| **Anthropic**    | ✅       | ✅        | —   | —   | —         | ✅     | —          | ✅       | ✅         | ✅        |
| **Google GenAI** | ✅       | ✅        | ✅  | ✅  | ✅        | ✅     | —          | ✅       | ✅         | ✅        |
| **ElevenLabs**   | —        | —         | ✅  | —   | —         | —      | —          | —        | —          | —         |
| **Inworld**      | —        | —         | ✅  | —   | —         | —      | —          | —        | —          | —         |
| **LM Studio**    | ✅       | ✅        | —   | —   | —         | ✅     | —          | —        | —          | —         |

## Setup

### 1. Install dependencies

```bash
npm install
```

### 2. Configure secrets

```bash
cp secrets.example.js secrets.js
```

Edit `secrets.js` and fill in your API keys and configuration:

| Secret              | Required | Description                                            |
| ------------------- | -------- | ------------------------------------------------------ |
| `PORT`              | Yes      | Server port (default: `3000`)                          |
| `GATEWAY_SECRET`    | Yes      | API auth secret — clients send via `x-api-secret`     |
| `ADMIN_SECRET`      | Yes      | Admin auth secret — sent via `x-admin-secret`         |
| `OPENAI_API_KEY`    | No       | OpenAI API key                                         |
| `ANTHROPIC_API_KEY` | No       | Anthropic API key                                      |
| `GOOGLE_API_KEY`    | No       | Google GenAI API key                                   |
| `ELEVENLABS_API_KEY`| No       | ElevenLabs API key                                     |
| `INWORLD_BASIC`     | No       | Base64-encoded Inworld credentials                     |
| `LM_STUDIO_BASE_URL`| No      | LM Studio URL (default: `http://localhost:1234`)       |
| `MONGO_URI`         | Yes      | MongoDB connection string                              |
| `MONGO_DB_NAME`     | Yes      | MongoDB database name                                  |
| `MINIO_ENDPOINT`    | No       | MinIO endpoint                                         |
| `MINIO_ACCESS_KEY`  | No       | MinIO access key                                       |
| `MINIO_SECRET_KEY`  | No       | MinIO secret key                                       |
| `MINIO_BUCKET_NAME` | No       | MinIO bucket name                                      |

### 3. Start the server

```bash
npm run dev        # Development (auto-reload with nodemon)
npm start          # Production
```

## API Endpoints

All endpoints except `/` and `/admin/*` require the `x-api-secret` header.

### REST

| Method | Endpoint            | Description                       |
| ------ | ------------------- | --------------------------------- |
| GET    | `/`                 | Health check — server info        |
| GET    | `/config`           | Full model catalog and options    |
| POST   | `/text-to-text`     | Text/chat generation              |
| POST   | `/text-to-image`    | Image generation                  |
| POST   | `/image-to-text`    | Vision / image description        |
| POST   | `/text-to-speech`   | Text-to-speech (returns audio)    |
| POST   | `/audio-to-text`    | Speech-to-text transcription      |
| POST   | `/text-to-embedding`| Text embeddings                   |
| GET    | `/conversations`    | List saved conversations          |
| POST   | `/conversations`    | Save a conversation               |
| GET    | `/conversations/:id`| Get a conversation by ID          |
| DELETE | `/conversations/:id`| Delete a conversation             |
| GET    | `/files/:key`       | Retrieve a file from storage      |

### WebSocket

| Endpoint                     | Description                       |
| ---------------------------- | --------------------------------- |
| `/text-to-text/stream`       | Streaming text generation         |
| `/text-to-speech/stream`     | Streaming TTS (ElevenLabs)        |

### Admin (requires `x-admin-secret`)

| Method | Endpoint                      | Description                               |
| ------ | ----------------------------- | ----------------------------------------- |
| GET    | `/admin/requests`             | Paginated request logs with filters       |
| GET    | `/admin/requests/:id`         | Single request detail                     |
| GET    | `/admin/stats`                | Aggregate stats (tokens, cost, latency)   |
| GET    | `/admin/stats/projects`       | Per-project breakdown                     |
| GET    | `/admin/stats/models`         | Per-model breakdown                       |
| GET    | `/admin/stats/endpoints`      | Per-endpoint breakdown                    |
| GET    | `/admin/stats/timeline`       | Hourly request/cost timeline              |
| GET    | `/admin/conversations`        | Cross-project conversation list           |
| GET    | `/admin/conversations/:id`    | Full conversation with messages           |
| GET    | `/admin/live`                 | Live activity — recent conversations      |
| GET    | `/admin/health`               | System health, memory, and DB stats       |
| GET    | `/admin/lm-studio/models`     | List LM Studio models                     |
| POST   | `/admin/lm-studio/load`       | Load a model in LM Studio                 |
| POST   | `/admin/lm-studio/unload`     | Unload a model from LM Studio             |

## Features

- **Unified Gateway** — single API for 6 AI providers
- **WebSocket Streaming** — real-time text generation and TTS
- **Request Logging** — every request is logged to MongoDB with tokens, cost, and latency
- **Cost Tracking** — per-model pricing with automatic cost estimation
- **Conversation Persistence** — save/load chat history in MongoDB
- **File Storage** — MinIO object storage with MongoDB fallback
- **Admin API** — analytics, stats, request inspection, and LM Studio management
- **Auth Middleware** — separate secrets for client API and admin access
- **Model Catalog** — centralized model definitions with pricing, capabilities, and arena scores

## Scripts

```bash
npm start            # Start server
npm run dev          # Start with auto-reload (nodemon)
npm run lint         # Run ESLint
npm run lint:fix     # Auto-fix lint issues
npm run format       # Format with Prettier
npm run format:check # Check formatting
npm test             # Run tests (vitest)
npm run test:watch   # Run tests in watch mode
```
