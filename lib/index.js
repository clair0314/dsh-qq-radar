// dsh-qq-group-digest — ship the `qq-digest` skill as an installable DSH plugin.
//
// The bundle carries the Python CLI (app/, bin/) and the skill instructions
// (SKILL.md). On load we register the skill with the harness and rewrite the
// `~/.agents/skills/qq-digest` paths inside the instructions to this package's
// install directory, so the agent calls the bundled CLI wherever npm put it.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'qq-group-digest';
export const inject = ['skills'];

const SKILL_NAME = 'qq-digest';
const FALLBACK_DESCRIPTION =
  '把用户关注的 QQ 群最近聊的内容总结成中文日报（HTML/Markdown），可推送到飞书群。';

/** Split YAML frontmatter from a SKILL.md body. */
function splitFrontmatter(raw) {
  const text = raw.replace(/^\uFEFF/, '');
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!match) return { meta: {}, body: text };
  const meta = {};
  for (const line of match[1].split(/\r?\n/)) {
    const kv = line.match(/^([A-Za-z][\w-]*):\s*(.*)$/);
    if (!kv) continue;
    let value = kv[2].trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    meta[kv[1]] = value;
  }
  return { meta, body: text.slice(match[0].length) };
}

/**
 * Register the qq-digest skill.
 * @param ctx - Cordis context; `skills` is injected.
 */
export function apply(ctx) {
  const here = dirname(fileURLToPath(import.meta.url));
  const root = join(here, '..');
  const { meta, body } = splitFrontmatter(readFileSync(join(root, 'SKILL.md'), 'utf8'));

  // The skill is written for a `~/.agents/skills/qq-digest` install; point it at
  // this package instead so `Q=.../bin/qq-digest` resolves after an npm install.
  const content = body
    .replaceAll('~/.agents/skills/qq-digest', root)
    .replaceAll('$HOME/.agents/skills/qq-digest', root);

  ctx.effect(
    () =>
      ctx.skills.register({
        name: meta.name || SKILL_NAME,
        description: meta.description || FALLBACK_DESCRIPTION,
        content,
      }),
    'qq-digest: skill',
  );
}
