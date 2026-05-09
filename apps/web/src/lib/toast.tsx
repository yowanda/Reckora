import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError } from "@/api/client";

export type ToastKind = "success" | "error" | "info";

export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

interface ToastContextValue {
  toasts: Toast[];
  push: (kind: ToastKind, text: string) => void;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (kind: ToastKind, text: string) => {
      counter.current += 1;
      const id = counter.current;
      setToasts((current) => [...current, { id, kind, text }]);
      window.setTimeout(() => dismiss(id), 4000);
    },
    [dismiss],
  );

  const value = useMemo(
    () => ({ toasts, push, dismiss }),
    [toasts, push, dismiss],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport />
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (ctx === null) {
    throw new Error("useToast must be used inside a ToastProvider");
  }
  return ctx;
}

/**
 * Format any error (ApiError, Error, string, unknown) into a single line
 * suitable for a toast.
 */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.status}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function ToastViewport() {
  const { toasts, dismiss } = useToast();
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: Toast;
  onDismiss: (id: number) => void;
}) {
  // Trigger fade-in on mount.
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const handle = window.setTimeout(() => setVisible(true), 10);
    return () => window.clearTimeout(handle);
  }, []);

  const palette: Record<ToastKind, string> = {
    success: "border-ok/30 bg-ok-soft text-fg",
    error: "border-danger/30 bg-danger-soft text-fg",
    info: "border-ink-line bg-ink-panel text-fg",
  };
  const dot: Record<ToastKind, string> = {
    success: "bg-ok",
    error: "bg-danger",
    info: "bg-fg-muted",
  };

  return (
    <div
      role="status"
      className={[
        "pointer-events-auto flex items-start gap-2 rounded-lg border px-3 py-2 text-xs shadow-panel backdrop-blur transition-all duration-200",
        palette[toast.kind],
        visible ? "translate-x-0 opacity-100" : "translate-x-2 opacity-0",
      ].join(" ")}
    >
      <span
        aria-hidden="true"
        className={`mt-1 inline-block h-2 w-2 flex-shrink-0 rounded-full ${dot[toast.kind]}`}
      />
      <span className="flex-1 whitespace-pre-wrap break-words leading-relaxed">
        {toast.text}
      </span>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label="Dismiss"
        className="text-fg-dim transition-colors hover:text-fg"
      >
        ×
      </button>
    </div>
  );
}
