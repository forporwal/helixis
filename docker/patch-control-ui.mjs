#!/usr/bin/env node
// Collapse tool-result messages into their tool card in the OpenClaw Control UI.
//
// Upstream bug (openclaw 2026.3.11). In ui/src/ui/chat/grouped-render.ts the
// collapse-to-card branch of renderGroupedMessage is guarded on there being no
// text to show:
//
//     if (!markdown && hasToolCards && isToolResult) { ...render card only... }
//
// But OpenClaw stores a tool result as content: [{type:"text", text:"..."}],
// and ui/src/ui/chat/message-extract.ts extracts text for every role — it only
// branches on assistant/user for stripping, never skips toolResult. So for any
// tool that returns text (`read` above all) `markdown` is always truthy, the
// guard never fires, and the whole file body renders through the markdown
// pipeline as a chat bubble underneath its own "Completed" card.
//
// Dropping `!markdown` from the guard restores the intended behaviour. The full
// output is not lost: renderToolCardSidebar keeps the inline preview and the
// click-through to the Tool Output sidebar, which is how tool results were
// meant to be read.
//
// Only messages that are genuinely tool results collapse. An assistant message
// carrying both prose and tool cards has isToolResult false, so it is untouched.
//
// Minified identifiers: v = markdown, r = hasToolCards, i = isToolResult.

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// Overridable only so the patch can be exercised against a scratch copy of the
// bundle; the build never sets it.
const ASSET_DIR =
  process.env.CONTROL_UI_ASSET_DIR ?? "/usr/lib/node_modules/openclaw/dist/control-ui/assets";
const FIND = "return!v&&r&&i?";
const REPLACE = "return r&&i?";

const candidates = readdirSync(ASSET_DIR).filter((f) => f.endsWith(".js"));
const hits = [];

for (const file of candidates) {
  const path = join(ASSET_DIR, file);
  const src = readFileSync(path, "utf8");
  const count = src.split(FIND).length - 1;
  if (count === 0) continue;
  hits.push({ file, count });
  writeFileSync(path, src.split(FIND).join(REPLACE));
}

// The base image is pinned by digest, so a miss means the pin moved or the
// bundle was rebuilt. Fail the build rather than ship a silently unpatched UI.
if (hits.length !== 1 || hits[0].count !== 1) {
  console.error(
    `patch-control-ui: expected exactly 1 occurrence of the guard in exactly 1 ` +
      `bundle, found ${JSON.stringify(hits)}. The pinned image changed — ` +
      `re-derive the patch from dist/control-ui/assets/*.js.map before rebuilding.`,
  );
  process.exit(1);
}

console.log(`patch-control-ui: patched ${hits[0].file} (tool results now collapse to cards)`);
