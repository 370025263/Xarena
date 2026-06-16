# Xarena

**Xarena** is a Kubernetes-native leaderboard framework (Flask backend +
Streamlit frontend) together with a **SpreadsheetBench × Skill** evaluation
harness. Participants submit an *algorithm* as a container image; the platform
runs it as a native Kubernetes Job and scores it, with full per-task detail in
the UI.

The bundled board — **Spreadsheet Skill Bench** — compares the *skills* produced
by three skill-distillation algorithms (**SkillOpt**, **Trace2Skill**, **xskill**)
on the SpreadsheetBench test split, scored in single mode (`claude_code_exec`)
with **deepseek-v4-flash** on a 10×-reduced split.

## How it works (one picture)

```
submit algo image ─▶ backend creates a K8s Job (1 Pod, 2 containers, shared /shared):
   • algorithm container  → TRAINS for real, writes a skill to /shared/skill, signals DONE
   • evaluator container   → reads the skill, evaluates on SpreadsheetBench, POSTs scores back
backend reconciles Job status + harvests logs natively; the UI shows rankings,
a radar chart, and per-task (input/output/task.md/chat/pass-fail) detail.
```

Six self-contained images: 3 algorithm images (`algo-skillopt`,
`algo-trace2skill`, `algo-xskill` — each really trains) + 3 evaluator images
(`spreadsheet-eval-single|multi|react`), plus the backend and frontend platform
images. Everything runs in a local **kind** cluster with an in-cluster registry.

## Quickstart

```bash
./install.sh
```

The installer is **interactive and from-scratch**: it preflights your tools
(docker/kubectl/kind/node), prompts for your API keys (written to a
**gitignored** `config.local.env` — never committed), starts a local registry,
builds all eight images from the in-repo `vendor/` sources (fully offline /
self-contained), creates the kind cluster, loads the images, deploys the backend
+ frontend, initializes the database with default users, and prints the access
URL and credentials. See `./install.sh --help`.

After it finishes:

- Frontend: `http://<host>:7799`
- Backend API: `http://<host>:30001`
- Default logins: `admin/adminpass` (admin), `l_creator/creatorpass`
  (creator/maintainer), `p_user1/user1pass` (participant).

To submit an algorithm: log in as a participant and submit a board with
`algorithm_image_url = localhost:5000/p_user1/algo-<algo>:v1`.

## Screenshots

Acceptance screenshots (rankings, radar chart, per-question analysis, role
dashboards) are captured by the Playwright scripts in `run/` into `run/shots/`
(gitignored). Run `python run/pw_final.py` against a live deployment to
regenerate them.

## Documentation

- **`CLAUDE.md`** — full architecture, the two-image K8s-native pipeline,
  directory map, install details, the role model, the eval pipeline, how to add a
  new algorithm image, and the gotchas baked into the code. Start here.
- **`DEPLOYMENT_REPORT.md`** — delivery report with the pipeline results.
- **`readme`** — the original task brief (Chinese).
- **`vendor/README.md`** — attribution/licensing for the vendored third-party
  sources.

## Secrets

No API keys or secrets are committed to this repository. They are provided at
install time and surfaced to the cluster as a Kubernetes Secret (`algo-secrets`).
The documented intranet proxy IPs (`50.67.x`) are legacy network references, not
secrets.

## License & attribution

This repository bundles third-party sources under `vendor/`:

- **SkillOpt** — MIT (Microsoft Corporation)
- **xskill** — MIT
- **Trace2Skill** — Apache-2.0
- **SpreadsheetBench** data subset — per upstream dataset terms

See `vendor/README.md` and each `vendor/<name>/LICENSE` for details. Retain those
license files on redistribution.
