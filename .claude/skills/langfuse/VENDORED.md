# Vendored, not written here

`SKILL.md`, `references/` and `LICENSE` are copied verbatim from
[langfuse/skills](https://github.com/langfuse/skills) and are not this repo's to edit. A local
change would be silently discarded the next time the copy is refreshed, so anything that needs
changing belongs upstream — the skill's own `references/skill-feedback.md` says how to raise it.

| | |
|---|---|
| Source | `github.com/langfuse/skills`, path `skills/langfuse/` |
| Pinned commit | `f275566d06fba46278d94ceea0e00d5e0cf70e62` (2026-09-22) |
| Upstream version | 1.8.1, per its `.claude-plugin/plugin.json` |
| License | MIT, vendored beside it as `LICENSE` |

Only `skills/langfuse/` was taken. The repository also carries a vendored copy of Anthropic's
`skill-creator` under `.cursor/` and three plugin manifests; none of that is here, because none of
it is the Langfuse skill.

## Refreshing it

Re-fetch at a newer commit and replace the files:

```bash
SHA=<the commit to pin>
D=.claude/skills/langfuse
gh api repos/langfuse/skills/contents/skills/langfuse/SKILL.md?ref=$SHA --jq .content \
  | base64 -d > $D/SKILL.md
for f in $(ls $D/references | sed 's/\.md$//'); do
  gh api "repos/langfuse/skills/contents/skills/langfuse/references/$f.md?ref=$SHA" --jq .content \
    | base64 -d > "$D/references/$f.md"
done
```

Upstream may add or drop a reference file, which that loop will not notice — it iterates over what
is already here. After refreshing, check that every `references/*.md` named in `SKILL.md` exists
and that nothing is left behind that `SKILL.md` no longer names.

## Why vendored rather than installed as a plugin

The upstream repo ships `.claude-plugin/plugin.json`, so it can also be added through Claude
Code's plugin marketplace, which is the route that updates itself:

```
/plugin marketplace add langfuse/skills
/plugin install langfuse@langfuse-skills
```

That route installs per person, and this project's tracing and eval work is Langfuse's
(`docs/ROADMAP.md` Phase 2), so the skill is checked in instead: every contributor gets it with
the clone and no one has to be told to install anything. The cost is that this copy is frozen at
the commit above and only moves when someone refreshes it.

## What it is allowed to do

`SKILL.md`'s own `allowed-tools` frontmatter confines it to fetching `langfuse.com`, and to
`langfuse-cli` invocations that read (`__schema`, `--help`, `list`, `get`) — no write commands.
Every URL across the vendored files resolves to `langfuse.com`, `*.cloud.langfuse.com` or GitHub's
docs. It carries no scripts: the files are markdown, and nothing here executes on its own.
