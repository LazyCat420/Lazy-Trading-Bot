// API Service for communicating with Prism AI Gateway
// Follows the same pattern as Retina's PrismService

const API_BASE = '/prism';
const SECRET = 'banana';

function getHeaders(): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'x-api-secret': SECRET,
    'x-project': 'lazy-trader',
    'x-username': 'trader',
  };
}

type StreamCallbacks = {
  onChunk?: (content: string) => void;
  onThinking?: (content: string) => void;
  onImage?: (data: string, mimeType: string) => void;
  onStatus?: (message: string) => void;
  onDone?: (data: Record<string, unknown>) => void;
  onError?: (err: Error) => void;
};

type ChatPayload = {
  provider: string;
  model?: string;
  messages: Array<{ role: string; content: string }>;
  options?: Record<string, unknown>;
  conversationId?: string;
  userMessage?: Record<string, unknown>;
  conversationMeta?: Record<string, unknown>;
};

export default class PrismService {
  /**
   * Shared fetch helper — centralises request / error handling.
   */
  static async _request(
    endpoint: string,
    { method = 'POST', body }: { method?: string; body?: unknown } = {},
  ): Promise<unknown> {
    const res = await fetch(`${API_BASE}${endpoint}`, {
      method,
      headers: getHeaders(),
      ...(body ? { body: JSON.stringify(body) } : {}),
    });

    if (!res.ok) {
      const err = (await res.json().catch(() => ({}))) as { message?: string };
      throw new Error(err.message || `Prism API error: ${res.status}`);
    }

    return res.json();
  }

  // ---------------------------------------------------------------------------
  // Config
  // ---------------------------------------------------------------------------

  /**
   * Fetch the Prism configuration (providers, models, defaults).
   */
  static async getConfig(): Promise<unknown> {
    return PrismService._request('/config', { method: 'GET' });
  }

  // ---------------------------------------------------------------------------
  // Chat
  // ---------------------------------------------------------------------------

  /**
   * Generate text (non-streaming).
   */
  static async generateText(payload: ChatPayload): Promise<unknown> {
    return PrismService._request('/chat?stream=false', { body: payload });
  }

  /**
   * Stream text generation via SSE (Server-Sent Events).
   * Returns an abort function to cancel the stream early.
   */
  static streamText(payload: ChatPayload, callbacks: StreamCallbacks): () => void {
    const { onChunk, onThinking, onImage, onStatus, onDone, onError } = callbacks;

    const controller = new AbortController();

    (async () => {
      try {
        const res = await fetch(`${API_BASE}/chat`, {
          method: 'POST',
          headers: getHeaders(),
          body: JSON.stringify(payload),
          signal: controller.signal,
        });

        if (!res.ok) {
          const err = (await res.json().catch(() => ({}))) as { message?: string };
          if (onError) onError(new Error(err.message || `HTTP ${res.status}`));
          return;
        }

        const reader = res.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });

          // Parse SSE lines: "data: {...}\n\n"
          const lines = buffer.split('\n');
          buffer = lines.pop() || ''; // Keep incomplete line in buffer

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const json = line.slice(6);
            if (!json) continue;

            try {
              const data = JSON.parse(json);
              if (data.type === 'chunk' && onChunk) {
                onChunk(data.content);
              } else if (data.type === 'thinking' && onThinking) {
                onThinking(data.content);
              } else if (data.type === 'image' && onImage) {
                onImage(data.data, data.mimeType);
              } else if (data.type === 'status' && onStatus) {
                onStatus(data.message);
              } else if (data.type === 'done' && onDone) {
                onDone(data);
              } else if (data.type === 'error' && onError) {
                onError(new Error(data.message));
              }
            } catch {
              // Ignore JSON parse errors on individual lines
            }
          }
        }
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        if (onError) onError(err instanceof Error ? err : new Error(String(err)));
      }
    })();

    // Return abort function (same interface as Retina)
    return () => controller.abort();
  }
}
