import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section className="mx-auto max-w-md py-16 text-center">
      <h1 className="text-2xl font-semibold">404</h1>
      <p className="mt-1 text-sm text-zinc-400">
        The page you tried to open does not exist.
      </p>
      <Link
        to="/subjects"
        className="mt-4 inline-block text-sm text-accent hover:underline"
      >
        ← Back to subjects
      </Link>
    </section>
  );
}
