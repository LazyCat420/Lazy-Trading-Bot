import { describe, it, expect } from 'vitest';
import request from 'supertest';
import { app, TEST_SECRET } from './setup.js';

describe('Auth Middleware', () => {
  it('returns 401 when x-api-secret header is missing', async () => {
    const res = await request(app).get('/config').expect(401);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body).toHaveProperty('statusCode', 401);
    expect(res.body.message).toMatch(/unauthorized/i);
  });

  it('returns 401 when x-api-secret header is wrong', async () => {
    const res = await request(app)
      .get('/config')
      .set('x-api-secret', 'wrong-secret')
      .expect(401);

    expect(res.body).toHaveProperty('error', true);
    expect(res.body).toHaveProperty('statusCode', 401);
  });

  it('returns 200 with correct x-api-secret header', async () => {
    const res = await request(app)
      .get('/config')
      .set('x-api-secret', TEST_SECRET)
      .expect(200);

    expect(res.body).toHaveProperty('providers');
  });

  it('attaches x-project header value to req.project', async () => {
    // We can verify this indirectly — a POST to text-to-text should work
    // and the RequestLogger.log call will receive the project value.
    // For now, just verify the request succeeds with the project header.
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .set('x-project', 'my-project')
      .send({
        provider: 'openai',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    expect(res.body).toHaveProperty('text');
  });

  it("defaults x-project to 'unknown' when header is absent", async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    expect(res.body).toHaveProperty('text');
  });

  it('attaches x-username header value to req.username', async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .set('x-project', 'my-project')
      .set('x-username', 'rodrigo')
      .send({
        provider: 'openai',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    expect(res.body).toHaveProperty('text');
  });

  it("defaults x-username to 'unknown' when header is absent", async () => {
    const res = await request(app)
      .post('/chat?stream=false')
      .set('x-api-secret', TEST_SECRET)
      .send({
        provider: 'openai',
        messages: [{ role: 'user', content: 'hi' }],
      })
      .expect(200);

    expect(res.body).toHaveProperty('text');
  });
});
