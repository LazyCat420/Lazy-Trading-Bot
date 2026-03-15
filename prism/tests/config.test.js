import { describe, it, expect } from 'vitest';
import request from 'supertest';
import { app, TEST_SECRET } from './setup.js';

describe('GET /config', () => {
  it('returns the full config catalog', async () => {
    const res = await request(app)
      .get('/config')
      .set('x-api-secret', TEST_SECRET)
      .expect(200);

    expect(res.body).toHaveProperty('providers');
    expect(res.body).toHaveProperty('providerList');
    expect(res.body).toHaveProperty('textToText');
    expect(res.body).toHaveProperty('textToSpeech');
    expect(res.body).toHaveProperty('textToImage');
    expect(res.body).toHaveProperty('imageToText');
    expect(res.body).toHaveProperty('embedding');
  });

  it('textToText has models and defaults', async () => {
    const res = await request(app)
      .get('/config')
      .set('x-api-secret', TEST_SECRET)
      .expect(200);

    expect(res.body.textToText).toHaveProperty('models');
    expect(res.body.textToText).toHaveProperty('defaults');
    expect(typeof res.body.textToText.models).toBe('object');
    expect(typeof res.body.textToText.defaults).toBe('object');
  });

  it('textToSpeech has models, defaults, voices, and defaultVoices', async () => {
    const res = await request(app)
      .get('/config')
      .set('x-api-secret', TEST_SECRET)
      .expect(200);

    expect(res.body.textToSpeech).toHaveProperty('models');
    expect(res.body.textToSpeech).toHaveProperty('defaults');
    expect(res.body.textToSpeech).toHaveProperty('voices');
    expect(res.body.textToSpeech).toHaveProperty('defaultVoices');
  });

  it('textToImage has models and defaults', async () => {
    const res = await request(app)
      .get('/config')
      .set('x-api-secret', TEST_SECRET)
      .expect(200);

    expect(res.body.textToImage).toHaveProperty('models');
    expect(res.body.textToImage).toHaveProperty('defaults');
  });

  it('imageToText has models and defaults', async () => {
    const res = await request(app)
      .get('/config')
      .set('x-api-secret', TEST_SECRET)
      .expect(200);

    expect(res.body.imageToText).toHaveProperty('models');
    expect(res.body.imageToText).toHaveProperty('defaults');
  });

  it('embedding has models and defaults', async () => {
    const res = await request(app)
      .get('/config')
      .set('x-api-secret', TEST_SECRET)
      .expect(200);

    expect(res.body.embedding).toHaveProperty('models');
    expect(res.body.embedding).toHaveProperty('defaults');
  });

  it('providerList contains all known providers', async () => {
    const res = await request(app)
      .get('/config')
      .set('x-api-secret', TEST_SECRET)
      .expect(200);

    const list = res.body.providerList;
    expect(list).toContain('openai');
    expect(list).toContain('anthropic');
    expect(list).toContain('google');
    expect(list).toContain('elevenlabs');
    expect(list).toContain('inworld');
    expect(list).toContain('lm-studio');
  });
});
