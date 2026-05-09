export function formatRelativeTime(iso: string | null | undefined): string {
  if (iso == null) {
    return "—";
  }
  const then = Date.parse(iso);
  if (Number.isNaN(then)) {
    return iso;
  }
  const diffMs = Date.now() - then;
  const seconds = Math.round(diffMs / 1000);
  if (seconds < 5) {
    return "just now";
  }
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  const days = Math.round(hours / 24);
  if (days < 30) {
    return `${days}d ago`;
  }
  const months = Math.round(days / 30);
  if (months < 12) {
    return `${months}mo ago`;
  }
  const years = Math.round(days / 365);
  return `${years}y ago`;
}

export function formatAbsolute(iso: string | null | undefined): string {
  if (iso == null) {
    return "";
  }
  const t = new Date(iso);
  if (Number.isNaN(t.valueOf())) {
    return iso;
  }
  return t.toLocaleString();
}

export function shortId(id: string, head = 8): string {
  if (id.length <= head) {
    return id;
  }
  return `${id.slice(0, head)}…`;
}
