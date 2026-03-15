/**
 * Shared test setup — creates an Express app with mocked providers and secrets.
 * Every test file gets a pre-configured supertest agent via `createAgent()`.
 */
import { vi } from 'vitest';

// ── Mock secrets before anything imports them ──────────────────────────
vi.mock('../src/secrets.js', () => ({
    PORT: 0,
    GATEWAY_SECRET: 'test-secret',
    OPENAI_API_KEY: 'fake',
    ANTHROPIC_API_KEY: 'fake',
    GOOGLE_API_KEY: 'fake',
    ELEVENLABS_API_KEY: 'fake',
    INWORLD_BASIC: 'fake',
    OPENAI_COMPATIBLE_BASE_URL: 'http://localhost:9999',
    MONGO_URI: 'mongodb://127.0.0.1:27017',
    MONGO_DB_NAME: 'prism-test',
}));

// ── Mock MongoDB wrapper to avoid real connections ────────────────────
vi.mock('../src/wrappers/MongoWrapper.js', () => ({
    default: {
        createClient: vi.fn().mockResolvedValue(undefined),
        getDb: vi.fn().mockReturnValue(null),
    },
}));

// ── Mock RequestLogger to avoid DB writes ─────────────────────────────
vi.mock('../src/services/RequestLogger.js', () => ({
    default: {
        log: vi.fn(),
    },
}));

// ── Build mock provider functions ─────────────────────────────────────
export const MOCK_GENERATE_TEXT = vi.fn().mockResolvedValue({
    text: 'Hello from mock',
    usage: { inputTokens: 10, outputTokens: 5 },
});

export const MOCK_GENERATE_TEXT_STREAM = vi
    .fn()
    .mockImplementation(async function* () {
        yield 'Hello ';
        yield 'world';
        yield { type: 'usage', usage: { inputTokens: 10, outputTokens: 5 } };
    });

export const MOCK_GENERATE_SPEECH = vi.fn().mockResolvedValue({
    contentType: 'audio/mpeg',
    stream: {
        pipe: vi.fn((res) => {
            res.write(Buffer.from('fake-audio-data'));
            res.end();
        }),
    },
});

export const MOCK_GENERATE_SPEECH_STREAM = vi.fn();

export const MOCK_GENERATE_IMAGE = vi.fn().mockResolvedValue({
    imageData: 'base64data',
    mimeType: 'image/png',
    text: 'A generated image',
});

export const MOCK_CAPTION_IMAGE = vi.fn().mockResolvedValue({
    text: 'A photo of a cat',
    usage: { inputTokens: 100, outputTokens: 50 },
});

export const MOCK_GENERATE_EMBEDDING = vi.fn().mockResolvedValue({
    embedding: [0.1, 0.2, 0.3],
});

// ── Mock providers ────────────────────────────────────────────────────
vi.mock('../src/providers/index.js', () => {
    // Re-import at the top of the factory is not allowed, so we inline
    const mockProviderFull = {
        generateText: (...args) => MOCK_GENERATE_TEXT(...args),
        generateTextStream: (...args) => MOCK_GENERATE_TEXT_STREAM(...args),
        generateSpeech: (...args) => MOCK_GENERATE_SPEECH(...args),
        generateSpeechStream: (...args) => MOCK_GENERATE_SPEECH_STREAM(...args),
        generateImage: (...args) => MOCK_GENERATE_IMAGE(...args),
        captionImage: (...args) => MOCK_CAPTION_IMAGE(...args),
        generateEmbedding: (...args) => MOCK_GENERATE_EMBEDDING(...args),
    };

    const mockProviderTextOnly = {
        generateText: (...args) => MOCK_GENERATE_TEXT(...args),
        generateTextStream: (...args) => MOCK_GENERATE_TEXT_STREAM(...args),
    };

    const mockProviderTtsOnly = {
        generateSpeech: (...args) => MOCK_GENERATE_SPEECH(...args),
        generateSpeechStream: (...args) => MOCK_GENERATE_SPEECH_STREAM(...args),
    };

    const providers = {
        openai: mockProviderFull,
        anthropic: mockProviderTextOnly,
        google: mockProviderFull,
        elevenlabs: mockProviderTtsOnly,
        inworld: mockProviderTtsOnly,
        'openai-compatible': mockProviderTextOnly,
    };

    return {
        getProvider: (name) => {
            const p = providers[name];
            if (!p) {
                throw new Error(
                    `Unknown provider "${name}". Available: ${Object.keys(providers).join(', ')}`,
                );
            }
            return p;
        },
        listProviders: () => Object.keys(providers),
        providers,
    };
});

// ── Build app (import AFTER mocks are set up) ─────────────────────────
const { default: express } = await import('express');
const { default: cors } = await import('cors');
const { errorHandler } = await import('../src/utils/errors.js');
const { authMiddleware } = await import('../src/middleware/AuthMiddleware.js');
const { listProviders } = await import('../src/providers/index.js');

const { default: chatRouter } = await import('../src/routes/chat.js');
const { default: audioRouter } = await import('../src/routes/audio.js');
const { default: embedRouter } = await import('../src/routes/embed.js');
const { default: configRouter } = await import('../src/routes/config.js');

export const app = express();
app.use(cors());
app.use(express.json({ limit: '50mb' }));

app.get('/', (_req, res) => {
    res.json({
        name: 'Prism the AI Gateway',
        version: '1.0.0',
        providers: listProviders(),
        endpoints: {
            rest: [
                '/config',
                '/chat',
                '/audio',
                '/embed',
            ],
            websocket: ['/ws/chat', '/ws/text-to-audio'],
        },
    });
});

app.use(authMiddleware);
app.use('/config', configRouter);
app.use('/chat', chatRouter);
app.use('/audio', audioRouter);
app.use('/embed', embedRouter);
app.use(errorHandler);

// ── Helpers ───────────────────────────────────────────────────────────
export const TEST_SECRET = 'test-secret';

