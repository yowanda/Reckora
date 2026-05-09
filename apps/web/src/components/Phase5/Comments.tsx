import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { api, unwrap } from "@/api/client";
import type { CommentEntry, ReactionGroup } from "@/api/types";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Spinner } from "@/components/Spinner";
import { formatRelativeTime } from "@/lib/format";

async function fetchComments(subjectId: string): Promise<CommentEntry[]> {
  return unwrap(
    await api.GET("/api/v1/subjects/{subject_id}/comments", {
      params: { path: { subject_id: subjectId } },
    }),
  );
}

async function postComment(args: {
  subjectId: string;
  body: string;
  parentCommentId?: number;
}): Promise<CommentEntry> {
  return unwrap(
    await api.POST("/api/v1/subjects/{subject_id}/comments", {
      params: { path: { subject_id: args.subjectId } },
      body: { body: args.body, parent_comment_id: args.parentCommentId },
    }),
  );
}

async function deleteComment(args: {
  subjectId: string;
  commentId: number;
}): Promise<void> {
  unwrap(
    await api.DELETE(
      "/api/v1/subjects/{subject_id}/comments/{comment_id}",
      {
        params: {
          path: { subject_id: args.subjectId, comment_id: args.commentId },
        },
      },
    ),
  );
}

async function fetchReactions(args: {
  subjectId: string;
  commentId: number;
}): Promise<ReactionGroup[]> {
  return unwrap(
    await api.GET(
      "/api/v1/subjects/{subject_id}/comments/{comment_id}/reactions",
      {
        params: {
          path: { subject_id: args.subjectId, comment_id: args.commentId },
        },
      },
    ),
  );
}

async function addReaction(args: {
  subjectId: string;
  commentId: number;
  key: string;
}): Promise<ReactionGroup[]> {
  return unwrap(
    await api.PUT(
      "/api/v1/subjects/{subject_id}/comments/{comment_id}/reactions/{reaction_key}",
      {
        params: {
          path: {
            subject_id: args.subjectId,
            comment_id: args.commentId,
            reaction_key: args.key,
          },
        },
      },
    ),
  );
}

async function removeReaction(args: {
  subjectId: string;
  commentId: number;
  key: string;
}): Promise<void> {
  unwrap(
    await api.DELETE(
      "/api/v1/subjects/{subject_id}/comments/{comment_id}/reactions/{reaction_key}",
      {
        params: {
          path: {
            subject_id: args.subjectId,
            comment_id: args.commentId,
            reaction_key: args.key,
          },
        },
      },
    ),
  );
}

const REACTION_KEYS = ["+1", "-1", "eyes", "heart"];

export function Comments({ subjectId }: { subjectId: string }) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["subjects", subjectId, "comments"],
    queryFn: () => fetchComments(subjectId),
  });
  const post = useMutation({
    mutationFn: postComment,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["subjects", subjectId, "comments"] });
      qc.invalidateQueries({ queryKey: ["subjects", subjectId, "activity"] });
    },
  });
  const remove = useMutation({
    mutationFn: deleteComment,
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["subjects", subjectId, "comments"] }),
  });

  const [draft, setDraft] = useState("");

  const all = list.data ?? [];
  const top = all.filter((c) => !c.parent_comment_id);
  const repliesByParent = new Map<number, CommentEntry[]>();
  for (const c of all) {
    if (c.parent_comment_id) {
      const arr = repliesByParent.get(c.parent_comment_id) ?? [];
      arr.push(c);
      repliesByParent.set(c.parent_comment_id, arr);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.trim()) return;
    post.mutate(
      { subjectId, body: draft.trim() },
      { onSuccess: () => setDraft("") },
    );
  }

  return (
    <section className="rounded border border-border bg-bg-panel">
      <header className="border-b border-border px-3 py-2 text-xs uppercase tracking-wide text-zinc-500">
        Comments
      </header>
      <div className="space-y-3 p-3">
        {list.isPending ? <Spinner /> : null}
        {list.error ? <ErrorMessage error={list.error} /> : null}
        {top.length === 0 && !list.isPending ? (
          <p className="text-xs text-zinc-500">No comments yet.</p>
        ) : null}
        <ul className="space-y-3">
          {top.map((comment) => (
            <CommentNode
              key={comment.id}
              subjectId={subjectId}
              comment={comment}
              replies={repliesByParent.get(comment.id) ?? []}
              onDelete={() =>
                remove.mutate({ subjectId, commentId: comment.id })
              }
              onReply={(body) =>
                post.mutate({
                  subjectId,
                  body,
                  parentCommentId: comment.id,
                })
              }
            />
          ))}
        </ul>
        <form onSubmit={onSubmit} className="space-y-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            placeholder="Write a comment… use @username to mention."
            className="w-full rounded border border-border bg-bg-subtle px-2 py-1.5 text-sm outline-none focus:border-accent"
          />
          {post.error ? <ErrorMessage error={post.error} /> : null}
          <div className="flex justify-end">
            <button
              type="submit"
              disabled={post.isPending || draft.trim() === ""}
              className="rounded bg-accent-muted px-3 py-1 text-xs hover:bg-accent disabled:opacity-50"
            >
              {post.isPending ? "Posting…" : "Post"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}

function CommentNode({
  subjectId,
  comment,
  replies,
  onDelete,
  onReply,
}: {
  subjectId: string;
  comment: CommentEntry;
  replies: CommentEntry[];
  onDelete: () => void;
  onReply: (body: string) => void;
}) {
  const [replying, setReplying] = useState(false);
  const [draft, setDraft] = useState("");

  return (
    <li className="rounded border border-border bg-bg-subtle p-3">
      <div className="flex items-baseline gap-2 text-xs text-zinc-500">
        <span className="font-medium text-zinc-200">
          {comment.author_username ?? `user ${comment.author_user_id}`}
        </span>
        <span>{formatRelativeTime(comment.created_at)}</span>
        <button
          type="button"
          onClick={onDelete}
          className="ml-auto text-zinc-500 hover:text-red-300"
        >
          delete
        </button>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-sm">{comment.body}</p>
      {comment.mentions && comment.mentions.length > 0 ? (
        <p className="mt-1 text-xs text-zinc-500">
          mentioned: {comment.mentions.map((u) => `@${u}`).join(", ")}
        </p>
      ) : null}
      <ReactionsRow subjectId={subjectId} commentId={comment.id} />
      {replies.length > 0 ? (
        <ul className="mt-2 space-y-2 border-l border-border pl-3">
          {replies.map((reply) => (
            <li key={reply.id} className="rounded bg-bg-panel p-2">
              <div className="flex items-baseline gap-2 text-xs text-zinc-500">
                <span className="font-medium text-zinc-200">
                  {reply.author_username ?? `user ${reply.author_user_id}`}
                </span>
                <span>{formatRelativeTime(reply.created_at)}</span>
              </div>
              <p className="mt-1 whitespace-pre-wrap text-sm">{reply.body}</p>
              <ReactionsRow subjectId={subjectId} commentId={reply.id} />
            </li>
          ))}
        </ul>
      ) : null}
      {replying ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const trimmed = draft.trim();
            if (trimmed === "") return;
            onReply(trimmed);
            setDraft("");
            setReplying(false);
          }}
          className="mt-2 space-y-2"
        >
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            className="w-full rounded border border-border bg-bg-panel px-2 py-1 text-sm outline-none focus:border-accent"
          />
          <div className="flex gap-2">
            <button
              type="submit"
              className="rounded bg-accent-muted px-2 py-1 text-xs hover:bg-accent"
            >
              Reply
            </button>
            <button
              type="button"
              onClick={() => setReplying(false)}
              className="rounded border border-border bg-bg-panel px-2 py-1 text-xs"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setReplying(true)}
          className="mt-2 text-xs text-zinc-400 hover:text-zinc-100"
        >
          Reply
        </button>
      )}
    </li>
  );
}

function ReactionsRow({
  subjectId,
  commentId,
}: {
  subjectId: string;
  commentId: number;
}) {
  const qc = useQueryClient();
  const reactions = useQuery({
    queryKey: ["subjects", subjectId, "comments", commentId, "reactions"],
    queryFn: () => fetchReactions({ subjectId, commentId }),
  });
  const invalidate = () =>
    qc.invalidateQueries({
      queryKey: ["subjects", subjectId, "comments", commentId, "reactions"],
    });
  const add = useMutation({ mutationFn: addReaction, onSuccess: invalidate });
  const remove = useMutation({
    mutationFn: removeReaction,
    onSuccess: invalidate,
  });

  const byKey = new Map<string, ReactionGroup>(
    (reactions.data ?? []).map((r) => [r.key, r]),
  );

  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {REACTION_KEYS.map((key) => {
        const group = byKey.get(key);
        const mine = group?.me_reacted ?? false;
        const count = group?.count ?? 0;
        return (
          <button
            key={key}
            type="button"
            onClick={() => {
              if (mine) {
                remove.mutate({ subjectId, commentId, key });
              } else {
                add.mutate({ subjectId, commentId, key });
              }
            }}
            className={`rounded border px-1.5 py-0.5 text-xs ${
              mine
                ? "border-accent bg-accent-muted"
                : "border-border bg-bg-panel hover:border-accent-muted"
            }`}
          >
            {key} {count > 0 ? <span className="ml-1">{count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
