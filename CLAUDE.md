# dealfindio — Claude Code project rules

## Git workflow

**Never push directly to `main`.** All work goes on a feature branch with a PR.

- Story branch: `story/<issue-number>-<short-slug>` — e.g. `story/9-stop-hotlinking`
- Epic branch (all stories in one session): `epic/<issue-number>-<short-slug>` — e.g. `epic/8-image-enrichment`
- Open the PR, link it to the GitHub issue, and leave merging to the user.

## Testing

- **Milestone tests:** pause and run a real smoke test at each meaningful milestone during an epic/story — don't push through the whole implementation without intermediate validation.
- **Completion test summary:** at the end of every story/epic, run the full relevant test suite and report in Claude: total tests run, passed, failed, and any new tests added. Include the same summary in the PR body under a `## Tests` section.

## Commit style

Follow the existing Conventional Commits pattern: `feat|fix|refactor|chore(scope): message`. Reference the GitHub issue number in the PR, not in individual commit messages.
