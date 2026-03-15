// ============================================================
// Prism the AI Gateway — Secrets Template
// ============================================================
// Copy this file to secrets.js and fill in your real values.
//   cp src/secrets.example.js src/secrets.js
// ============================================================

// Server
export const PORT = 3000;

// Gateway Auth
export const GATEWAY_SECRET = '';

// Admin Auth (Iris Dashboard)
export const ADMIN_SECRET = '';

// OpenAI
export const OPENAI_API_KEY = '';

// Anthropic
export const ANTHROPIC_API_KEY = '';

// Google GenAI
export const GOOGLE_API_KEY = '';

// ElevenLabs
export const ELEVENLABS_API_KEY = '';

// Inworld (Base64-encoded apiKey:apiSecret)
export const INWORLD_BASIC = '';

// LM Studio (Local LLM Server)
export const LM_STUDIO_BASE_URL = 'http://localhost:1234';

// Ollama (Local LLM Server)
export const OLLAMA_BASE_URL = 'http://localhost:11434';

// Mongo
export const MONGO_URI = 'mongodb://127.0.0.1:27017';
export const MONGO_DB_NAME = '';

// MinIO (Optional — files stored inline in MongoDB if not set)
export const MINIO_ENDPOINT = '';
export const MINIO_ACCESS_KEY = '';
export const MINIO_SECRET_KEY = '';
export const MINIO_BUCKET_NAME = '';
