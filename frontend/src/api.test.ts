import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, download } from './api';

afterEach(() => vi.unstubAllGlobals());
describe('same-origin API client', () => {
  it('sends authenticated JSON with a version field intact', async () => {
    let received: RequestInit | undefined;
    vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
      received = init;
      return new Response(JSON.stringify({ id: 'page', version: 3 }), { status: 200 });
    });
    expect(
      await api('/workspaces/one/pages/page', { method: 'PATCH', body: { version: 2, content: 'revised' } }),
    ).toEqual({ id: 'page', version: 3 });
    expect(received?.credentials).toBe('include');
    expect(JSON.parse(received?.body as string).version).toBe(2);
  });
  it('retains the full current document on a 409 for explicit conflict recovery', async () => {
    const detail = {
      code: 'version_conflict',
      current: { id: 'page', content: 'remote changes', version: 8 },
      submitted_version: 5,
    };
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ detail }), { status: 409 }));
    const error = await api('/workspaces/one/pages/page', { method: 'PATCH', body: { version: 5 } }).catch(
      (e) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
    expect((error as ApiError).detail).toEqual(detail);
  });
  it('reports structured validation messages without losing field context', async () => {
    vi.stubGlobal(
      'fetch',
      async () =>
        new Response(
          JSON.stringify({
            detail: [
              { loc: ['body', 'password'], msg: 'String should have at least 8 characters' },
            ],
          }),
          { status: 422 },
        ),
    );
    await expect(api('/auth/register')).rejects.toThrow(
      'password: String should have at least 8 characters',
    );
  });
  it('handles empty success responses', async () => {
    vi.stubGlobal('fetch', async () => new Response(null, { status: 204 }));
    expect(await api('/auth/logout', { method: 'POST' })).toBeUndefined();
  });
  it('surfaces download authorization failures instead of saving an error as a file', async () => {
    vi.stubGlobal(
      'fetch',
      async () => new Response(JSON.stringify({ detail: 'Membership required' }), { status: 403 }),
    );
    await expect(download('/workspaces/one/export', 'wiki.zip')).rejects.toThrow(
      'Membership required',
    );
  });
});
