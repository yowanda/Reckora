import { ApiError } from "@/api/client";

export function ErrorMessage({ error }: { error: unknown }) {
  let status: number | null = null;
  let text: string;
  if (error instanceof ApiError) {
    status = error.status;
    text = error.message;
  } else if (error instanceof Error) {
    text = error.message;
  } else {
    text = String(error);
  }
  return (
    <div
      className="flex items-start gap-2 rounded border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger"
      role="alert"
    >
      <svg
        viewBox="0 0 16 16"
        fill="none"
        className="mt-0.5 h-3.5 w-3.5 shrink-0"
        aria-hidden
      >
        <path
          d="M8 1.5 1.5 13.5h13L8 1.5z"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
        <path
          d="M8 6.5v3.5M8 12.25v.25"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
      </svg>
      <div className="min-w-0 leading-relaxed">
        {status !== null ? (
          <span className="mr-1.5 rounded border border-danger/40 bg-danger/10 px-1.5 py-0.5 font-mono text-2xs uppercase tracking-[0.12em]">
            {status}
          </span>
        ) : null}
        <span className="break-words">{text}</span>
      </div>
    </div>
  );
}
