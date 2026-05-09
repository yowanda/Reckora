import createClient from "openapi-fetch";

import type { paths } from "./schema.gen";

const TOKEN_KEY = "reckora.token";

export function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function writeToken(token: string | null): void {
  try {
    if (token === null) {
      localStorage.removeItem(TOKEN_KEY);
    } else {
      localStorage.setItem(TOKEN_KEY, token);
    }
  } catch {
    // ignore — private browsing or quota
  }
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";

export const api = createClient<paths>({ baseUrl });

api.use({
  onRequest({ request }) {
    const token = readToken();
    if (token) {
      request.headers.set("authorization", `Bearer ${token}`);
    }
    return request;
  },
});

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type Maybe<T> = T | undefined;

export function unwrap<T>(
  result: { data?: Maybe<T>; error?: unknown; response: Response },
): NonNullable<T> {
  if (
    result.error !== undefined ||
    result.data === undefined ||
    result.data === null
  ) {
    const detail =
      typeof result.error === "object" &&
      result.error !== null &&
      "detail" in result.error
        ? (result.error as { detail?: unknown }).detail
        : result.error;
    const message =
      typeof detail === "string"
        ? detail
        : `Request failed with status ${result.response.status}`;
    throw new ApiError(result.response.status, message, detail);
  }
  return result.data as NonNullable<T>;
}
