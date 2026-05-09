import { ApiError } from "@/api/client";

export function ErrorMessage({ error }: { error: unknown }) {
  let text: string;
  if (error instanceof ApiError) {
    text = `${error.status}: ${error.message}`;
  } else if (error instanceof Error) {
    text = error.message;
  } else {
    text = String(error);
  }
  return (
    <div
      className="rounded border border-red-900/50 bg-red-950/40 px-3 py-2 text-sm text-red-200"
      role="alert"
    >
      {text}
    </div>
  );
}
