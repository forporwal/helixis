#!/usr/bin/env bash
# helixis-wiki-sync — mirror the read-only Helixis wiki mount into the OpenClaw
# workspace so skills distilled by training runs become live agent context.
#
# Mounted read-only at /helixis/wiki (see docker-compose.yml, nemoclaw service).
# Runs once before the gateway starts, then on a background loop; each pass is
# a no-op unless wiki/state.json's generation changed.
#
# Discovery facts confirmed against the pinned image (openclaw 2026.3.11):
#   - workspace skills live at <ws>/skills/<slug>/SKILL.md, reported by
#     `openclaw skills list` with source `openclaw-workspace`
#   - discovery is NOT recursive: <ws>/skills/helixis/<slug>/SKILL.md is
#     invisible, so the mirror writes slugs flat and tracks the ones it owns in
#     a manifest to prune safely without touching user-authored skills
#   - rsync is not installed in the image; this uses cp + manifest prune

set -uo pipefail

WIKI_DIR="${HELIXIS_WIKI_DIR:-/helixis/wiki}"
STATE_FILE="$WIKI_DIR/state.json"

log() { echo "wiki-sync: $*"; }

# --- resolve the OpenClaw workspace -----------------------------------------
resolve_workspace() {
  if [ -n "${HELIXIS_CLAW_WORKSPACE:-}" ]; then
    echo "$HELIXIS_CLAW_WORKSPACE"
    return
  fi
  # `openclaw agents list` prints e.g. "  Workspace: ~/.openclaw/workspace"
  local ws
  ws="$(openclaw agents list 2>/dev/null \
        | awk -F': *' '/Workspace:/ {print $2; exit}')"
  ws="${ws/#\~/$HOME}"
  if [ -n "$ws" ]; then
    echo "$ws"
  else
    echo "$HOME/.openclaw/workspace"
  fi
}

WS="$(resolve_workspace)"
SKILLS_DST="$WS/skills"
PAGES_DST="$WS/docs/wiki"
MANIFEST="$WS/.helixis-skills.manifest"
STAMP="$WS/.helixis-wiki-sync.stamp"

BOOTSTRAP_MARKER="<!-- helixis-wiki-sync -->"

# --- change detection --------------------------------------------------------
# Generation from state.json is the primary signal (Req 2.1); if it is missing
# or unreadable we fall back to the newest mtime under the wiki (design:
# "state.json unreadable → warn, fall back to mtime").
current_generation() {
  if [ -r "$STATE_FILE" ]; then
    local gen
    gen="$(sed -n 's/.*"generation"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' \
           "$STATE_FILE" | head -1)"
    if [ -n "$gen" ]; then
      echo "gen:$gen"
      return
    fi
  fi
  # A wiki with no readable state.json is still syncable — fall back to a
  # content fingerprint so distills land even when the counter is unavailable.
  # The warning goes to stderr: this function's stdout IS the token, and a log
  # line on stdout would be captured as part of it.
  if [ -e "$STATE_FILE" ]; then
    log "warning: $STATE_FILE unreadable or has no generation; using file mtimes" >&2
  fi
  # -printf is GNU-only; `ls -l` output over the tree is portable enough here
  # and changes whenever a file is added, removed, or rewritten.
  echo "files:$(ls -lLR "$WIKI_DIR" 2>/dev/null | cksum | tr -d ' ')"
}

generation_number() {
  case "$1" in
    gen:*) echo "${1#gen:}" ;;
    *) echo "unknown (no state.json)" ;;
  esac
}

# --- skill mirror ------------------------------------------------------------
# Mirror, not append-only (Req 2.2): every slug this script wrote on a previous
# pass is recorded in the manifest, and any that no longer exists in the wiki is
# removed. Skills the manifest does not name are user-authored and never touched.
#
# Counts come back through globals rather than stdout: these functions also
# log, and a command substitution would swallow those lines into the count.
N_SKILLS=0
N_PAGES=0

sync_skills() {
  local wrote=0 new_manifest="$MANIFEST.tmp"
  : > "$new_manifest"

  if [ -d "$WIKI_DIR/skills" ]; then
    for src in "$WIKI_DIR"/skills/*/; do
      [ -f "$src/SKILL.md" ] || continue
      local slug
      slug="$(basename "$src")"
      # Never let the mirror overwrite a skill it does not own.
      if [ -e "$SKILLS_DST/$slug" ] && ! grep -qxF "$slug" "$MANIFEST" 2>/dev/null; then
        log "warning: skipping '$slug' — a non-Helixis skill already owns that slug"
        continue
      fi
      mkdir -p "$SKILLS_DST/$slug"
      # Copy the whole skill directory verbatim: frontmatter (generation,
      # source_episodes) passes through untouched, preserving provenance (Req 3.1).
      cp -a "$src." "$SKILLS_DST/$slug/" 2>/dev/null || {
        log "warning: failed to copy skill '$slug'"
        continue
      }
      echo "$slug" >> "$new_manifest"
      wrote=$((wrote + 1))
    done
  fi

  # Prune slugs we own that are gone from the wiki (superseded or renamed).
  if [ -f "$MANIFEST" ]; then
    while IFS= read -r old; do
      [ -n "$old" ] || continue
      if ! grep -qxF "$old" "$new_manifest"; then
        rm -rf "${SKILLS_DST:?}/$old"
        log "pruned stale skill '$old'"
      fi
    done < "$MANIFEST"
  fi

  mv "$new_manifest" "$MANIFEST"
  N_SKILLS="$wrote"
}

# --- pages mirror ------------------------------------------------------------
sync_pages() {
  local wrote=0
  # Mirror semantics here too: drop the previous copy so renamed/removed pages
  # do not linger as stale references.
  rm -rf "${PAGES_DST:?}"
  if [ -d "$WIKI_DIR/pages" ]; then
    for src in "$WIKI_DIR"/pages/*.md; do
      [ -f "$src" ] || continue
      mkdir -p "$PAGES_DST"
      cp -a "$src" "$PAGES_DST/" 2>/dev/null && wrote=$((wrote + 1))
    done
  fi
  N_PAGES="$wrote"
}

# --- bootstrap note ----------------------------------------------------------
# Appended once, guarded by a marker so repeated syncs never duplicate it (Req 1.3).
# Only written when there is actually something to point at, so an empty wiki
# leaves no broken references (Req 1.4).
sync_bootstrap() {
  local n_skills="$1" n_pages="$2"
  local agents_md="$WS/AGENTS.md"
  [ "$n_skills" -gt 0 ] || [ "$n_pages" -gt 0 ] || return 0
  [ -f "$agents_md" ] || return 0
  grep -qF "$BOOTSTRAP_MARKER" "$agents_md" 2>/dev/null && return 0

  cat >> "$agents_md" <<EOF

$BOOTSTRAP_MARKER
## Helixis Learned Skills

Skills under \`skills/\` whose frontmatter carries a \`generation\` field were
learned by Helixis training runs, distilled from real task failures. Their
\`source_episodes\` list the episodes they came from.

Domain playbooks and open questions live in \`docs/wiki/\`. Consult the relevant
playbook before Gmail, Calendar, or CRM tasks — it records what previous runs
got wrong and how they were fixed.
EOF
  log "appended Helixis bootstrap note to AGENTS.md"
}

# --- main --------------------------------------------------------------------
main() {
  if [ ! -d "$WIKI_DIR" ]; then
    log "no wiki mounted at $WIKI_DIR; nothing to sync"
    return 0
  fi

  local token
  token="$(current_generation)"
  if [ -f "$STAMP" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$token" ]; then
    return 0
  fi

  mkdir -p "$WS" || { log "workspace $WS not writable; skipping"; return 0; }

  sync_skills
  sync_pages
  sync_bootstrap "$N_SKILLS" "$N_PAGES"

  echo "$token" > "$STAMP"
  log "generation $(generation_number "$token"), $N_SKILLS skills, $N_PAGES pages -> $WS"
}

main "$@"
