import type { Conflict } from './types';

function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail))
    return detail
      .map((item) => `${item.loc?.slice(1).join('.') || '请求'}: ${item.msg || '格式不正确'}`)
      .join('；');
  if (detail && typeof detail === 'object') {
    const value = detail as Record<string, unknown>;
    if (value.code === 'version_conflict')
      return '文档已有新的版本。你的草稿已保留，请比较后继续。';
    if (typeof value.message === 'string') return value.message;
    if (typeof value.code === 'string') return value.code;
  }
  return `请求未完成 (${status})`;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: unknown,
  ) {
    super(errorMessage(detail, status));
    this.name = 'ApiError';
  }
  get conflict(): Conflict | null {
    return this.status === 409 &&
      this.detail &&
      typeof this.detail === 'object' &&
      'current' in this.detail
      ? (this.detail as Conflict)
      : null;
  }
}
export async function api<T = unknown>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    headers?: Record<string, string>;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const isForm = options.body instanceof FormData;
  const response = await fetch(`/api${path}`, {
    method: options.method || 'GET',
    credentials: 'include',
    signal: options.signal,
    headers: {
      ...(!isForm && options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
    body:
      options.body === undefined
        ? undefined
        : isForm
          ? (options.body as FormData)
          : JSON.stringify(options.body),
  });
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(response.status, payload?.detail || payload?.message || response.statusText);
  return payload as T;
}
export async function download(path: string, filename: string): Promise<void> {
  const response = await fetch(`/api${path}`, { credentials: 'include' });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(response.status, payload?.detail);
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : typeof error === 'string'
      ? error
      : '发生了意外错误，请重试';
}
