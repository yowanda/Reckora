/**
 * Human-friendly metadata for each `TraceSource` enum value.
 *
 * The backend stores collector IDs as snake_case (`github_api`,
 * `social_presence_probe`, …). Surfacing them raw in the dossier UI
 * leaks implementation detail and is hard to scan, so this module maps
 * each known source onto:
 *
 * - `label` — the human-readable platform name shown in card headers
 *   (e.g. `"GitHub"`, `"Social presence probe"`)
 * - `tags`  — short category chips rendered under the URL row, used for
 *   at-a-glance triage (`"social"`, `"infrastructure"`, …)
 *
 * Unknown sources fall back to a title-cased version of the raw ID and
 * an empty tag list, so collectors that ship without a label entry
 * still render cleanly.
 */

export interface SourceMeta {
  label: string;
  tags: readonly string[];
}

const SOURCE_META: Record<string, SourceMeta> = {
  github_api: { label: "GitHub", tags: ["coding", "profile"] },
  hackernews_api: { label: "Hacker News", tags: ["news", "tech"] },
  keybase_api: { label: "Keybase", tags: ["identity", "crypto"] },
  gravatar_api: { label: "Gravatar", tags: ["avatar", "identity"] },
  whois_rdap: { label: "WHOIS / RDAP", tags: ["infrastructure", "domain"] },
  dns_resolver: { label: "DNS records", tags: ["infrastructure", "dns"] },
  web_profile: { label: "Web profile", tags: ["web"] },
  phone_libphonenumber: { label: "Phone (libphonenumber)", tags: ["phone"] },
  breach_hibp: { label: "HIBP breach lookup", tags: ["security", "breach"] },
  wallet_blockstream: {
    label: "Bitcoin (Blockstream Esplora)",
    tags: ["wallet", "btc"],
  },
  wallet_etherscan: {
    label: "Ethereum (Etherscan)",
    tags: ["wallet", "eth"],
  },
  wallet_solana: { label: "Solana mainnet", tags: ["wallet", "sol"] },
  avatar_http: { label: "Avatar fetch", tags: ["avatar", "image"] },
  email_profile: { label: "Email profile", tags: ["email"] },
  reddit_profile: { label: "Reddit", tags: ["social"] },
  x_syndication: { label: "X / Twitter", tags: ["social"] },
  tiktok_web: { label: "TikTok", tags: ["social"] },
  social_presence_probe: {
    label: "Social presence probe",
    tags: ["social", "probe"],
  },
  doc_leak: { label: "Doc-leak / paste search", tags: ["breach", "leak"] },
  leak_hunt: { label: "AI leak hunt", tags: ["ai", "leak"] },
  user_provided: { label: "User-provided", tags: ["manual"] },
  web_research: { label: "Web research", tags: ["ai", "research"] },
};

const TITLE_CASE_OVERRIDES: Record<string, string> = {
  url: "URL",
  ip: "IP",
  dns: "DNS",
  spf: "SPF",
  mx: "MX",
  ns: "NS",
  txt: "TXT",
  dmarc: "DMARC",
  dnssec: "DNSSEC",
  api: "API",
  uri: "URI",
  id: "ID",
  uuid: "UUID",
  sha: "SHA",
  ssl: "SSL",
  tls: "TLS",
  rdap: "RDAP",
  hibp: "HIBP",
  btc: "BTC",
  eth: "ETH",
  sol: "SOL",
};

function titleCaseToken(token: string): string {
  const lowered = token.toLowerCase();
  if (TITLE_CASE_OVERRIDES[lowered]) {
    return TITLE_CASE_OVERRIDES[lowered];
  }
  if (lowered.length === 0) {
    return lowered;
  }
  return lowered.charAt(0).toUpperCase() + lowered.slice(1);
}

/**
 * "github_api" -> "Github API"
 * Used as the fallback when a collector name is missing from
 * `SOURCE_META`.
 */
function humanise(rawSource: string): string {
  return rawSource
    .split(/[_\s]+/)
    .filter((part) => part.length > 0)
    .map(titleCaseToken)
    .join(" ");
}

export function sourceMeta(rawSource: string): SourceMeta {
  const known = SOURCE_META[rawSource];
  if (known) {
    return known;
  }
  return { label: humanise(rawSource), tags: [] };
}

/**
 * Pretty-print a `Trace.fields` key as a human-readable label for the
 * 2-column field table — `"display_name"` becomes `"Display name"`,
 * `"profile_url"` becomes `"Profile URL"`, `"twitter_username"` becomes
 * `"Twitter username"`.
 */
export function fieldLabel(rawKey: string): string {
  const parts = rawKey.split(/[_\s]+/).filter((p) => p.length > 0);
  if (parts.length === 0) {
    return rawKey;
  }
  return parts
    .map((part, i) => {
      const lowered = part.toLowerCase();
      const overridden = TITLE_CASE_OVERRIDES[lowered];
      if (overridden) {
        return overridden;
      }
      if (i === 0) {
        return lowered.charAt(0).toUpperCase() + lowered.slice(1);
      }
      return lowered;
    })
    .join(" ");
}

/**
 * Friendly label for an `IdentifierType` enum value (e.g.
 * `"username"` -> `"Username"`, `"url"` -> `"URL"`).
 */
export function identifierTypeLabel(rawType: string): string {
  return fieldLabel(rawType);
}
