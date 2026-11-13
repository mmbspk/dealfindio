# Contributing

How to contribute to DealFindio — branch naming, code style, PR workflow, and testing requirements.

---

## Workflow

```bash
# 1. Create a branch from main
git checkout main
git pull origin main
git checkout -b story/123-short-slug   # story branch
# or
git checkout -b epic/123-short-slug    # epic branch
# or
git checkout -b fix/short-description  # bug fix

# 2. Make your changes

# 3. Run checks locally (see below)

# 4. Commit with Conventional Commits
git commit -m "feat(watchlist): add per-list notification channel override"

# 5. Push and open a PR against main
git push origin story/123-short-slug
gh pr create --title "story/123: ..." --body "..."
```

**Never push directly to `main`.** All changes go through a PR with CI passing.

---

## Branch Naming

| Type | Pattern | Example |
|------|---------|---------|
| Story | `story/<issue-number>-<short-slug>` | `story/61-unread-count-fix` |
| Epic | `epic/<issue-number>-<short-slug>` | `epic/60-watchlist-notifications` |
| Bug fix | `fix/<short-description>` | `fix/receipt-confidence-scoring` |
| Chore | `chore/<short-description>` | `chore/update-dependencies` |

---

## Commit Style

DealFindio uses **Conventional Commits**:

```
feat(scope): add X
fix(scope): correct Y
refactor(scope): simplify Z
chore(scope): update dependencies
test(scope): add test for W
docs(scope): update README
```

- **`feat`** — new user-facing feature
- **`fix`** — bug fix
- **`refactor`** — code change that neither adds a feature nor fixes a bug
- **`chore`** — build system, dependencies, config changes
- **`test`** — test additions or fixes only
- **`docs`** — documentation only

Reference the GitHub issue in the **PR description**, not in individual commit messages.

---

## Code Quality Checks

### Backend

```bash
cd backend

make format       # black + ruff --fix
make lint         # ruff check (no auto-fix)
make type-check   # pyright
make test         # pytest full suite (SQLite in-memory)
```

All four must pass cleanly before opening a PR.

For PostgreSQL-specific tests (PostGIS, pgvector):

```bash
TEST_DATABASE_URL=postgresql://dealfindio:dealfindio_dev@localhost:5432/dealfindio_test \
  poetry run pytest -m pg --no-cov
```

### Web Frontend

```bash
cd web

npm run lint        # ESLint
npm run type-check  # TypeScript strict mode
```

### Mobile

```bash
cd mobile

npm run type-check  # tsc --noEmit
```

---

## Testing Requirements

### Milestones

Run a real smoke test at each meaningful milestone during implementation — don't push through a full epic without intermediate validation.

### Test summary at completion

At the end of every story or epic, run the full test suite and report:
- Total tests run
- Tests passed
- Tests failed
- New tests added

Include this summary in the PR body under a `## Tests` section.

---

## Code Style

### Python

- **Formatter:** `black` (line length 88)
- **Linter:** `ruff` (enforced via `pyproject.toml`)
- **Types:** `pyright` strict mode — avoid `Any`
- **Comments:** only when the _why_ is non-obvious (hidden constraints, workarounds, subtle invariants)

### TypeScript (web + mobile)

- **Strict mode:** `"strict": true` in `tsconfig.json` — no `any`
- **Formatter:** Prettier
- **Linter:** ESLint with React rules
- **Imports:** absolute imports from project root (configured in `tsconfig.json` paths)

---

## Mobile-First Rule

DealFindio is primarily a mobile app. Every PR that touches UX/UI, API responses, image formats, or caching must implement the mobile path as part of the same story. If the mobile work can't fit, create a child story under the same epic.

Concrete checklist:
- Touch targets ≥ 44pt
- Bottom navigation (not sidebar)
- Single-column layouts on narrow screens
- Thumb-zone primary actions
- API payloads: paginated, lean (no unused fields), WebP thumbnails
- Receipt/image endpoints: max 5 MB; return quick results or use background tasks

---

## Project Board

All stories and epics are tracked on the GitHub project board:

→ https://github.com/users/mmbspk/projects/1

When creating a new issue, set:
- **Kind:** Epic or Story
- **Status:** Backlog (or In Progress if starting immediately)
- **Priority:** High / Medium / Low
- Link sub-issues to the parent epic using the "Sub-issues" section on the issue page

---

## CI/CD

GitHub Actions runs on every push and PR:

- **`ci.yml`** — `make lint`, `make type-check`, `make test` (backend); `npm run lint`, `npm run type-check` (web)
- **`deploy.yml`** — deploys to Railway (backend) and Vercel (web) on merge to `main`

CI must be green before merging. The test suite enforces a **60% line coverage gate** on the backend.

---

## Pre-commit Hooks

```bash
# One-time setup
pre-commit install

# Run manually on all files
pre-commit run --all-files
```

Hooks run `black`, `ruff`, and basic checks (no large files, no secrets in tracked files) before each commit.
