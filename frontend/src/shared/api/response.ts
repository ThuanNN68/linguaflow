export interface ApiErrorBody {
  detail?: string | Array<{ msg?: string }>;
}

export function parseJson<T>(response: Response): Promise<T | null> {
  return response.json().catch(() => null) as Promise<T | null>;
}

export function getApiErrorMessage(
  body: ApiErrorBody | null,
  fallback: string,
): string {
  if (typeof body?.detail === "string") return body.detail;

  if (Array.isArray(body?.detail)) {
    const message = body.detail.find((item) => item.msg)?.msg;
    if (message) return message;
  }

  return fallback;
}
