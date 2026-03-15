import { describe, it, expect, beforeEach } from 'vitest';
import request from 'supertest';
import { app, TEST_SECRET, MOCK_GENERATE_TEXT } from './setup.js';

describe('POST /text-to-text', () => {
  beforeEach(() => {
    MOCK_GENERATE_TEXT.mockClear();
    MOCK_GENERATE_TEXT.mockResolvedValue({
      text: 'Hello from mock',
      usage: { inputTokens: 10, outputTokens: 5 },
    });
  });

  // ── Required parameters ───────────────────────────────────────────

  it('returns 400 when provider is missing', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ messages: [{ role: 'user', content: 'hi' }] })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/provider/i);
  });

  it('returns 400 when messages is missing', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'openai' })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/messages/i);
  });

  it('returns 400 when messages is not an array', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'openai', messages: 'not an array' })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/messages/i);
  });

  // ── Successful request ────────────────────────────────────────────

  it('returns 200 with correct response shape (minimal params)', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        messages: [{ role: 'user', content: 'Hello' }],
      })
      .expect(200);

    expect(res.body).toHaveProperty('text', 'Hello from mock');
    expect(res.body).toHaveProperty('provider', 'openai');
    expect(res.body).toHaveProperty('model');
    expect(res.body).toHaveProperty('usage');
    expect(res.body.usage).toHaveProperty('inputTokens', 10);
    expect(res.body.usage).toHaveProperty('outputTokens', 5);
    expect(res.body).toHaveProperty('estimatedCost');
  });

  // ── Optional: model ───────────────────────────────────────────────

  it('uses the default model when model is omitted', async () => {
    await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    // The provider should have been called; model comes from config defaults
    expect(MOCK_GENERATE_TEXT).toHaveBeenCalledTimes(1);
    const calledModel = MOCK_GENERATE_TEXT.mock.calls[0][1];
    expect(typeof calledModel).toBe('string');
    expect(calledModel.length).toBeGreaterThan(0);
  });

  it('uses custom model when provided', async () => {
    await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        model: 'gpt-5.2',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    expect(MOCK_GENERATE_TEXT).toHaveBeenCalledTimes(1);
    const calledModel = MOCK_GENERATE_TEXT.mock.calls[0][1];
    expect(calledModel).toBe('gpt-5.2');
  });

  // ── Optional: options ─────────────────────────────────────────────

  it('passes options through to the provider', async () => {
    await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        messages: [{ role: 'user', content: 'hi' }],
        options: { temperature: 0.5, maxTokens: 100 },
      })
      .expect(200);

    expect(MOCK_GENERATE_TEXT).toHaveBeenCalledTimes(1);
    const calledOptions = MOCK_GENERATE_TEXT.mock.calls[0][2];
    expect(calledOptions).toEqual({ temperature: 0.5, maxTokens: 100 });
  });

  it('defaults options to empty object when omitted', async () => {
    await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    const calledOptions = MOCK_GENERATE_TEXT.mock.calls[0][2];
    expect(calledOptions).toEqual({});
  });

  // ── Multiple messages ─────────────────────────────────────────────

  it('handles multiple messages in the array', async () => {
    await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        messages: [
          { role: 'system', content: 'You are helpful.' },
          { role: 'user', content: 'Hello' },
          { role: 'assistant', content: 'Hi!' },
          { role: 'user', content: 'How are you?' },
        ],
      })
      .expect(200);

    const calledMessages = MOCK_GENERATE_TEXT.mock.calls[0][0];
    expect(calledMessages).toHaveLength(4);
  });

  // ── Different providers ───────────────────────────────────────────

  it('works with anthropic provider', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'anthropic',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    expect(res.body).toHaveProperty('provider', 'anthropic');
  });

  it('works with google provider', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'google',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    expect(res.body).toHaveProperty('provider', 'google');
  });

  it('works with openai-compatible provider', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai-compatible',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    expect(res.body).toHaveProperty('provider', 'openai-compatible');
  });

  // ── Error handling ────────────────────────────────────────────────

  it('returns 500 when provider throws an error', async () => {
    MOCK_GENERATE_TEXT.mockRejectedValueOnce(new Error('API down'));

    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(500);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/API down/);
  });

  it('returns error for unsupported provider that does not support text generation', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'elevenlabs',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/text generation/i);
  });

  it('returns error for unknown provider', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'nonexistent',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(500);

    expect(res.body).toHaveProperty('error', true);
  });
});
