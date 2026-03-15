import { describe, it, expect, beforeEach } from 'vitest';
import request from 'supertest';
import { app, TEST_SECRET, MOCK_GENERATE_SPEECH } from './setup.js';

describe('POST /text-to-speech', () => {
  beforeEach(() => {
    MOCK_GENERATE_SPEECH.mockClear();
    MOCK_GENERATE_SPEECH.mockResolvedValue({
      contentType: 'audio/mpeg',
      stream: {
        pipe: (res) => {
          res.write(Buffer.from('fake-audio-data'));
          res.end();
        },
      },
    });
  });

  // ── Required parameters ───────────────────────────────────────────

  it('returns 400 when provider is missing', async () => {
    const res = await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ text: 'Hello world' })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/provider/i);
  });

  it('returns 400 when text is missing', async () => {
    const res = await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'openai' })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/text/i);
  });

  // ── Successful request ────────────────────────────────────────────

  it('returns binary audio with correct Content-Type (minimal params)', async () => {
    const res = await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'openai', text: 'Hello world' })
      .expect(200);

    expect(res.headers['content-type']).toMatch(/audio/);
    expect(res.headers['transfer-encoding']).toBe('chunked');
    expect(Buffer.isBuffer(res.body)).toBe(true);
  });

  // ── Optional: voice ───────────────────────────────────────────────

  it('passes voice parameter to the provider', async () => {
    await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'openai', text: 'Hello world', voice: 'echo' })
      .expect(200);

    expect(MOCK_GENERATE_SPEECH).toHaveBeenCalledTimes(1);
    const calledVoice = MOCK_GENERATE_SPEECH.mock.calls[0][1];
    expect(calledVoice).toBe('echo');
  });

  it('passes undefined voice when omitted', async () => {
    await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'openai', text: 'Hello world' })
      .expect(200);

    const calledVoice = MOCK_GENERATE_SPEECH.mock.calls[0][1];
    expect(calledVoice).toBeUndefined();
  });

  // ── Optional: instructions ────────────────────────────────────────

  it('merges instructions into the options object', async () => {
    await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        text: 'Hello',
        instructions: 'Speak slowly',
      })
      .expect(200);

    const calledOptions = MOCK_GENERATE_SPEECH.mock.calls[0][2];
    expect(calledOptions).toHaveProperty('instructions', 'Speak slowly');
  });

  // ── Optional: model ───────────────────────────────────────────────

  it('merges model into the options object', async () => {
    await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        text: 'Hello',
        model: 'gpt-4o-mini-tts',
      })
      .expect(200);

    const calledOptions = MOCK_GENERATE_SPEECH.mock.calls[0][2];
    expect(calledOptions).toHaveProperty('model', 'gpt-4o-mini-tts');
  });

  // ── Optional: extra options ───────────────────────────────────────

  it('spreads extra options into final options', async () => {
    await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        text: 'Hello',
        options: { speed: 1.5, format: 'mp3' },
      })
      .expect(200);

    const calledOptions = MOCK_GENERATE_SPEECH.mock.calls[0][2];
    expect(calledOptions).toHaveProperty('speed', 1.5);
    expect(calledOptions).toHaveProperty('format', 'mp3');
  });

  it('combines instructions, model, and extra options', async () => {
    await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        text: 'Hello',
        voice: 'coral',
        instructions: 'Be cheerful',
        model: 'gpt-4o-mini-tts',
        options: { speed: 0.8 },
      })
      .expect(200);

    const calledOptions = MOCK_GENERATE_SPEECH.mock.calls[0][2];
    expect(calledOptions).toHaveProperty('instructions', 'Be cheerful');
    expect(calledOptions).toHaveProperty('model', 'gpt-4o-mini-tts');
    expect(calledOptions).toHaveProperty('speed', 0.8);
  });

  // ── Different providers ───────────────────────────────────────────

  it('works with elevenlabs provider', async () => {
    await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'elevenlabs', text: 'Hello' })
      .expect(200);

    expect(MOCK_GENERATE_SPEECH).toHaveBeenCalledTimes(1);
  });

  it('works with google provider', async () => {
    await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'google', text: 'Hello' })
      .expect(200);

    expect(MOCK_GENERATE_SPEECH).toHaveBeenCalledTimes(1);
  });

  it('works with inworld provider', async () => {
    await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'inworld', text: 'Hello' })
      .expect(200);

    expect(MOCK_GENERATE_SPEECH).toHaveBeenCalledTimes(1);
  });

  // ── Error handling ────────────────────────────────────────────────

  it('returns 400 for provider that does not support TTS', async () => {
    const res = await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'anthropic', text: 'Hello' })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/text-to-speech/i);
  });

  it('returns custom content-type from provider', async () => {
    MOCK_GENERATE_SPEECH.mockResolvedValueOnce({
      contentType: 'audio/wav',
      stream: {
        pipe: (res) => {
          res.write(Buffer.from('wav-data'));
          res.end();
        },
      },
    });

    const res = await request(app)
      .post('/voice')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'openai', text: 'Hello' })
      .expect(200);

    expect(res.headers['content-type']).toMatch(/audio\/wav/);
  });
});
