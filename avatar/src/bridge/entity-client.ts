export interface EntityConnectionSnapshot {
  connected: boolean;
  lifecycle: string;
  checkedAt: string;
  error?: string;
}

/** Read-only loopback client. It never sends commands or owns Entity state. */
export class EntityClient {
  constructor(private readonly baseUrl = 'http://127.0.0.1:8765') {}

  async status(signal?: AbortSignal): Promise<EntityConnectionSnapshot> {
    try {
      const response = await fetch(`${this.baseUrl}/status`, {
        method: 'GET', cache: 'no-store', signal,
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`Entity status HTTP ${response.status}`);
      const body: unknown = await response.json();
      const lifecycle = body && typeof body === 'object' &&
        'lifecycle' in body && typeof body.lifecycle === 'string'
        ? body.lifecycle : 'unknown';
      return { connected: true, lifecycle, checkedAt: new Date().toISOString() };
    } catch (error) {
      if (signal?.aborted) throw error;
      return {
        connected: false, lifecycle: 'unavailable',
        checkedAt: new Date().toISOString(),
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }
}
