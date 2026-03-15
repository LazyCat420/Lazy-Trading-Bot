import { describe, it, expect, beforeEach } from 'vitest';
import request from 'supertest';
import { app, TEST_SECRET, MOCK_GENERATE_IMAGE } from './setup.js';

describe('POST /text-to-image', () => {
  beforeEach(() => {
    MOCK_GENERATE_IMAGE.mockClear();
    MOCK_GENERATE_IMAGE.mockResolvedValue({
      imageData: 'base64data',
      mimeType: 'image/png',
      text: 'A generated image',
    });
  });

  // ── Required parameters ───────────────────────────────────────────

  it('returns 400 when provider is missing', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ prompt: 'A sunset' })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/provider/i);
  });

  it('returns 400 when prompt is missing', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'google' })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/prompt/i);
  });

  // ── Successful request ────────────────────────────────────────────

  it('returns 200 with correct response shape (minimal params)', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'google', prompt: 'A sunset over the ocean' })
      .expect(200);

    expect(res.body).toHaveProperty('imageData', 'base64data');
    expect(res.body).toHaveProperty('mimeType', 'image/png');
    expect(res.body).toHaveProperty('text', 'A generated image');
    expect(res.body).toHaveProperty('provider', 'google');
  });

  // ── Optional: model ───────────────────────────────────────────────

  it('passes model to the provider when provided', async () => {
    await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'google',
        prompt: 'A cat',
        model: 'gemini-3-pro-image-preview',
      })
      .expect(200);

    expect(MOCK_GENERATE_IMAGE).toHaveBeenCalledTimes(1);
    const calledModel = MOCK_GENERATE_IMAGE.mock.calls[0][2];
    expect(calledModel).toBe('gemini-3-pro-image-preview');
  });

  it('passes undefined model when omitted', async () => {
    await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'google', prompt: 'A cat' })
      .expect(200);

    const calledModel = MOCK_GENERATE_IMAGE.mock.calls[0][2];
    expect(calledModel).toBeUndefined();
  });

  // ── Optional: images ──────────────────────────────────────────────

  it('passes images array to the provider when provided', async () => {
    const images = ['data:image/png;base64,abc123'];
    await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'google', prompt: 'Edit this image', images })
      .expect(200);

    expect(MOCK_GENERATE_IMAGE).toHaveBeenCalledTimes(1);
    const calledImages = MOCK_GENERATE_IMAGE.mock.calls[0][1];
    expect(calledImages).toEqual(images);
  });

  it('defaults images to empty array when omitted', async () => {
    await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'google', prompt: 'A dog' })
      .expect(200);

    const calledImages = MOCK_GENERATE_IMAGE.mock.calls[0][1];
    expect(calledImages).toEqual([]);
  });

  // ── Response defaults ─────────────────────────────────────────────

  it('defaults mimeType to image/png when provider returns none', async () => {
    MOCK_GENERATE_IMAGE.mockResolvedValueOnce({
      imageData: 'base64data',
    });

    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'google', prompt: 'A frog' })
      .expect(200);

    expect(res.body.mimeType).toBe('image/png');
  });

  it('defaults text to null when provider returns none', async () => {
    MOCK_GENERATE_IMAGE.mockResolvedValueOnce({
      imageData: 'base64data',
      mimeType: 'image/jpeg',
    });

    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'google', prompt: 'A bird' })
      .expect(200);

    expect(res.body.text).toBeNull();
  });

  // ── Error handling ────────────────────────────────────────────────

  it('returns 400 for provider that does not support image generation', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'anthropic', prompt: 'A cat' })
      .expect(400);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body.message).toMatch(/image generation/i);
  });

  it('returns 500 when provider throws', async () => {
    MOCK_GENERATE_IMAGE.mockRejectedValueOnce(new Error('Generation failed'));

    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({ provider: 'google', prompt: 'A cat' })
      .expect(500);

    expect(res.body).toHaveProperty('error', true);
  });
});
