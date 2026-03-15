import { describe, it, expect, beforeEach } from 'vitest';
import request from 'supertest';
import { app, TEST_SECRET, MOCK_CAPTION_IMAGE } from './setup.js';

describe('POST /image-to-text', () => {
    beforeEach(() => {
        MOCK_CAPTION_IMAGE.mockClear();
        MOCK_CAPTION_IMAGE.mockResolvedValue({
            text: 'A photo of a cat',
            usage: { inputTokens: 100, outputTokens: 50 },
        });
    });

    // ── Required parameters ───────────────────────────────────────────

    it('returns 400 when provider is missing', async () => {
        const res = await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({ image: 'https://example.com/cat.jpg' })
            .expect(400);

        expect(res.body).toHaveProperty('error', true);
        expect(res.body.message).toMatch(/provider/i);
    });

    it('returns 400 when image is missing', async () => {
        const res = await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({ provider: 'google' })
            .expect(400);

        expect(res.body).toHaveProperty('error', true);
        expect(res.body.message).toMatch(/image/i);
    });

    // ── Successful request ────────────────────────────────────────────

    it('returns 200 with correct response shape (minimal params)', async () => {
        const res = await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'google',
                image: 'https://example.com/cat.jpg',
            })
            .expect(200);

        expect(res.body).toHaveProperty('text', 'A photo of a cat');
        expect(res.body).toHaveProperty('provider', 'google');
        expect(res.body).toHaveProperty('model');
        expect(res.body).toHaveProperty('usage');
        expect(res.body.usage).toHaveProperty('inputTokens', 100);
        expect(res.body.usage).toHaveProperty('outputTokens', 50);
    });

    // ── Cost calculation ──────────────────────────────────────────────

    it('includes estimatedCost in the response', async () => {
        const res = await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'google',
                image: 'https://example.com/cat.jpg',
            })
            .expect(200);

        // estimatedCost should be a number (may be null if no pricing for default model)
        expect(res.body).toHaveProperty('estimatedCost');
    });

    // ── Optional: prompt ──────────────────────────────────────────────

    it('passes prompt to the provider when provided', async () => {
        await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'google',
                image: 'https://example.com/cat.jpg',
                prompt: 'Describe in detail',
            })
            .expect(200);

        expect(MOCK_CAPTION_IMAGE).toHaveBeenCalledTimes(1);
        const calledPrompt = MOCK_CAPTION_IMAGE.mock.calls[0][1];
        expect(calledPrompt).toBe('Describe in detail');
    });

    it('passes undefined prompt when omitted', async () => {
        await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'google',
                image: 'https://example.com/cat.jpg',
            })
            .expect(200);

        const calledPrompt = MOCK_CAPTION_IMAGE.mock.calls[0][1];
        expect(calledPrompt).toBeUndefined();
    });

    // ── Optional: model ───────────────────────────────────────────────

    it('passes model to the provider when provided', async () => {
        await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'openai',
                image: 'https://example.com/cat.jpg',
                model: 'gpt-5-mini',
            })
            .expect(200);

        const calledModel = MOCK_CAPTION_IMAGE.mock.calls[0][2];
        expect(calledModel).toBe('gpt-5-mini');
    });

    it('uses default model when model is omitted', async () => {
        const res = await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'google',
                image: 'https://example.com/cat.jpg',
            })
            .expect(200);

        // Should have a model in the response (the resolved default)
        expect(res.body).toHaveProperty('model');
    });

    // ── Image as base64 ───────────────────────────────────────────────

    it('accepts base64 image data', async () => {
        await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'google',
                image: 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ==',
            })
            .expect(200);

        const calledImage = MOCK_CAPTION_IMAGE.mock.calls[0][0];
        expect(calledImage).toMatch(/^data:image/);
    });

    // ── Different providers ───────────────────────────────────────────

    it('works with openai provider', async () => {
        const res = await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'openai',
                image: 'https://example.com/cat.jpg',
            })
            .expect(200);

        expect(res.body).toHaveProperty('provider', 'openai');
    });

    // ── Error handling ────────────────────────────────────────────────

    it('returns 400 for provider that does not support image captioning', async () => {
        const res = await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'elevenlabs',
                image: 'https://example.com/cat.jpg',
            })
            .expect(400);

        expect(res.body).toHaveProperty('error', true);
        expect(res.body.message).toMatch(/image captioning/i);
    });

    it('returns 500 when provider throws', async () => {
        MOCK_CAPTION_IMAGE.mockRejectedValueOnce(new Error('Vision failed'));

        const res = await request(app)
            .post('/chat?stream=false')
            .set('x-api-secret', TEST_SECRET)
            .send({
                provider: 'google',
                image: 'https://example.com/cat.jpg',
            })
            .expect(500);

        expect(res.body).toHaveProperty('error', true);
    });
});
