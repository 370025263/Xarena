# Contributing to xskill

Thanks for helping improve xskill. This document covers how to set up, how to
submit changes, and how the project triages bugs.

## Development setup

```bash
git clone https://github.com/SkillNerds/xskill.git
cd xskill
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
xskill serve   # first run writes a ~/.xskill/config.yaml template; fill in the api keys
```

- Unit tests: `make test`
- Docker E2E (before a release): `make e2e`
- xskill needs Python 3.11+.

## Submitting changes

1. Branch off `main` with a short-lived feature branch (`feat/...`, `fix/...`).
2. Keep one logical change per PR. Write the commit message **English-first**
   (a short Chinese line after is fine).
3. Every code change needs a unit test. A bug fix needs a regression test.
4. Run `make test` before pushing. If the change touches ingestion, install,
   or the daemon, run `make e2e` too.
5. Open a PR; the PR template asks for a summary and a test plan. CODEOWNERS
   will auto-request review from the owner of the area you touched.
6. Do not rewrite published history. Do not skip git hooks.

## Bug triage — responsibility areas

xskill is split into 6 areas. Each area has an owner (and a backup) listed in
[`CODEOWNERS`](./CODEOWNERS). Issues are routed to the area owner by label.

| Area label | Covers |
| ---------- | ------ |
| `area:ecosystems` | `adapters.py`, `ecosystems.py` — trajectory ingest, the 5 ecosystem specs |
| `area:pipeline`   | `task_agent`, `task_cluster_agent`, `skill_edit_agent`, `candidates`, `atom_task` — skill generation |
| `area:canary`     | `canary.py`, `atom_canary.py`, `ux_score.py` — gradual rollout |
| `area:install`    | `install_fallback`, `install_history`, `user_edit_absorb_agent` — skill install + user-edit absorb |
| `area:team`       | `team/` — client/server mode |
| `area:server`     | `server.py`, `watcher.py`, `registry.py` — daemon |

## Bug triage — process

Every new issue gets two labels: an `area:*` label and a priority:

- **P0** — crash or data loss
- **P1** — a feature is broken
- **P2** — minor or cosmetic

Response SLA (time to acknowledge and start, not time to fix):

- P0: within 24h, else it auto-escalates to the backup owner
- P1: within 3 days
- P2: enters the backlog, no deadline

A weekly 15-minute triage pass labels new issues, assigns owners, closes
duplicates/invalid ones, and checks that no P0/P1 has blown its SLA. Without
the weekly pass the responsibility areas are only paper — the pass is what
drives the process.

## Reporting bugs

Use the bug report template. A bug we cannot reproduce cannot be fixed —
include exact steps, environment, expected vs actual. Security issues go
through [`SECURITY.md`](./SECURITY.md), not public issues.
