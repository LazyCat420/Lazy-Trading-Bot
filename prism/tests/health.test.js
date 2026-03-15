import { describe, it, expect } from 'vitest';
import request from 'supertest';
import { app } from './setup.js';

describe('GET / (Health Check)', () => {
  it('returns 200 without auth header', async () => {
    const res = await request(app).get('/').expect(200);

    expect(res.body).toHaveProperty('name', 'Prism the AI Gateway');
    expect(res.body).toHaveProperty('version', '1.0.0');
    expect(res.body).toHaveProperty('providers');
    expect(Array.isArray(res.body.providers)).toBe(true);
    expect(res.body).toHaveProperty('endpoints');
    expect(res.body.endpoints).toHaveProperty('rest');
    expect(res.body.endpoints).toHaveProperty('websocket');
  });

  it('includes all expected REST endpoints', async () => {
    const res = await request(app).get('/').expect(200);
    const restEndpoints = res.body.endpoints.rest;

    expect(restEndpoints).toContain('/config');
    expect(restEndpoints).toContain('/chat');
    expect(restEndpoints).toContain('/audio');
    expect(restEndpoints).toContain('/embed');
  });

  it('includes all expected WebSocket endpoints', async () => {
    const res = await request(app).get('/').expect(200);
    const wsEndpoints = res.body.endpoints.websocket;

    expect(wsEndpoints).toContain('/ws/chat');
    expect(wsEndpoints).toContain('/ws/text-to-audio');
  });

  it('lists all registered providers', async () => {
    const res = await request(app).get('/').expect(200);
    const providers = res.body.providers;

    expect(providers).toContain('openai');
    expect(providers).toContain('anthropic');
    expect(providers).toContain('google');
    expect(providers).toContain('elevenlabs');
    expect(providers).toContain('inworld');
    expect(providers).toContain('openai-compatible');
  });
});
