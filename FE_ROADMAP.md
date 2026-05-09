# Reckora — Frontend Roadmap (Tier 3 Redesign)

This file tracks the live progress of the **Tier 3 "Forensic / Intelligence"** UI overhaul. Each milestone (M1–M7) lands as one or more commits; this file is updated whenever a milestone changes status.

**Production target**: `https://reckora.my.id`

---

## Brand & Aesthetic

**Forensic / Intelligence** — the visual language of a SOC dashboard or
intelligence analyst's workstation: deep ink backgrounds, restrained
typography, restrained color, with **amber** for flags and risk and
**cyan** for live data and links.

| Token | Role | Hex |
|---|---|---|
| `--bg`              | Page background, deep ink | `#070a12` |
| `--bg-panel`        | Surface — cards, tables   | `#0e1320` |
| `--bg-subtle`       | Hover / focus surface     | `#161d2e` |
| `--bg-elevated`     | Modal / popover           | `#1c243a` |
| `--border`          | Default border            | `#1f2738` |
| `--border-strong`   | Active / focus border     | `#2d3a5a` |
| `--fg`              | Primary text              | `#e6ecf5` |
| `--fg-muted`        | Secondary text            | `#9aa6bd` |
| `--fg-dim`          | Tertiary / disabled       | `#5b6885` |
| `--accent`          | Primary action — cyan     | `#38bdf8` |
| `--accent-strong`   | Hover                     | `#0ea5e9` |
| `--alert`           | Risk / warning — amber    | `#f59e0b` |
| `--success`         | Resolved / verified       | `#34d399` |
| `--danger`          | Destructive / failure     | `#f87171` |

Type: **Inter** (UI) + **JetBrains Mono** (data/IDs), tighter tracking,
crisp scale. Shadows are hairline + soft glow on accents; radii: `4px`
default, `8px` panels, `12px` modals.

---

## Milestones

| # | Milestone | Status | Notes |
|---|---|---|---|
| **M0** | FE roadmap committed                                                | ✅ done | This file. |
| **M1** | Cache busting (Caddy headers; Vite hash already on)                 | ✅ done | `deploy/Caddyfile` — `/assets/*` immutable 1y, `/index.html` no-cache. |
| **M2** | Admin create-member endpoint + SPA Members panel                    | ✅ done | `POST /api/v1/users` (admin only) + `/admin/members` page (admin-only nav link, list/create/promote/demote). |
| **M3** | Forensic design tokens (Tailwind theme)                             | pending | Color, type, radius, shadow scales. |
| **M4** | Layout chrome: sidebar nav + breadcrumbs + global ⌘K command        | pending | Replace topbar; persistent nav. |
| **M5** | Page-by-page restyle                                                | pending | Login, Subjects, Detail, NewInv, Mentions, Pins, Watching, Phase 5. |
| **M6** | Polish: empty states, skeletons, errors, motion                     | pending | |
| **M7** | Rebuild + redeploy to VPS                                           | pending | `docker compose build && up -d`. |

Status legend: ✅ done · ⏳ in progress · pending · ❌ blocked.

---

## Constraints & Conventions

- **Stack**: React 18 + TypeScript strict + Tailwind 3 + react-router 6 + react-query 5 + openapi-fetch.
- **No new heavy dependencies** unless absolutely needed. Prefer Tailwind primitives + a tiny `@radix-ui/*` set for `Dialog`, `DropdownMenu`, `Popover`, `Tooltip` if a milestone requires accessible behavior we'd otherwise rebuild.
- **Cache busting**: Vite emits hashed asset filenames; Caddy sets `Cache-Control: public, max-age=31536000, immutable` on `/assets/*` and `no-cache` on `/index.html`. No `?v=…` query strings.
- **Accessibility floor**: every interactive control has visible focus, role-appropriate semantics, and AA contrast against its background.
- **Mobile**: responsive down to 375px; sidebar collapses to a top drawer on `<md` viewports.
- **Behavior parity**: redesign must not regress functionality. Each milestone PR includes a brief "verified flows" checklist.

---

## Reviewing milestone commits

Commits are titled `M<n>: <milestone description>`. The `FE_ROADMAP.md`
diff in each commit advances the table above so reviewers can see the
sequence in `git log -p FE_ROADMAP.md`.
