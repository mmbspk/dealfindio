# dealfindio — Claude Code project rules

## Mobile-first

**dealfindio is primarily a mobile app.** Every decision — UX/UI, API design, data shape, image formats, storage, caching, notifications, scraper output — must be made mobile-first.

Concrete implications:

- **UX/UI:** touch-friendly targets (≥44pt), bottom-navigation, single-column layouts, thumb-zone primary actions, minimal modals.
- **API responses:** paginated, lean payloads (no unused fields), optimised image URLs (serve WebP thumbnails at mobile sizes).
- **Scraper/storage output:** include all fields a mobile client needs (price, image, short title, store, valid_to) — no post-processing required on device.
- **Offline / resilience:** design for intermittent connectivity; cache catalogue snapshots locally where possible.
- **Performance:** target ≤2 s first meaningful paint on a mid-range device over 4G; avoid large bundle or payload sizes.

If a task directly touches any of these dimensions, implement the mobile path *as part of the same story*. If the mobile work can't fit in the current story (scope, risk, time), **create a child story** under the same epic so it is never forgotten.

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
