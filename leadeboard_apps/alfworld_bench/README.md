# ALFWorld Skill Bench

Second evaluation board for the Xarena leaderboard framework, mirroring
`spreadsheets_bench/`. It compares the **skills** produced by skill-distillation
algorithms on the **ALFWorld** TextWorld benchmark: an agent must complete
household tasks (pick & place, examine in light, clean/heat/cool & place) by
issuing text actions in a simulated environment, scored by **goal completion**
(the `won` flag) over `max_steps` (default 50) of a ReAct rollout.

Model: **deepseek-v4-flash** via an OpenAI-compatible endpoint (`openai_chat`
direct backend, the same path SpreadsheetBench uses for its `multi`/`react`
modes). The rollout is SkillOpt's `skillopt/envs/alfworld` adapter.

## Layout (mirrors spreadsheets_bench/)

```
alfworld_bench/
├── alfworld_eval/            ← 榜单/eval image (l_creator/alfworld-eval)
│   ├── evaluator.py          ← wait-for-skill → eval_only.py (env=alfworld) → POST metrics+details+result_view
│   ├── result_view.html      ← ALFWorld trajectory renderer (injected DATA = /api/submission/<id>/extra)
│   ├── Dockerfile build.sh requirements.eval.txt
├── algo_noskill/             ← 打榜/algo — the ONE that RUNS (no-op baseline skill)
├── algo_skillopt/            ← 打榜/algo — code present, NOT submitted by default (SkillOpt on configs/alfworld)
├── algo_trace2skill/         ← 打榜/algo — code present, NOT submitted by default (see its entrypoint note)
└── data/
    ├── alfworld_path_split/   ← committed split manifest: train 39 / val 18 / test 134 (191 items)
    ├── alfworld_miniset/      ← committed tiny miniset: 3 test games + their baked game files (~0.8 MB)
    └── fetch_data.sh          ← downloads the full json_2.1.1 / tw-pddl game payload into $ALFWORLD_DATA
```

## The skill contract (same as SpreadsheetBench)

- Algo trains → writes a skill to `SKILL_DIR=/shared/skill` (`skill.md` for single,
  or `skills/<n>/SKILL.md` for the multi/xskill convention) + `ALGO` + `touch DONE`,
  then **`sleep infinity`** (the sidecar's `restartPolicy: Always` re-runs it if it exits).
- Eval blocks on `DONE`, injects the skill into the agent's system prompt
  (`_build_skill_prompt`; an empty skill = no injection = pure baseline), runs the
  ALFWorld rollout, and POSTs `metrics` + per-task `eval_details` + `result_view_html`
  to `…/api/internal/submission/<id>/score`. It copies its run to `$OUTPUT_DIR/eval`
  (algos copy to `$OUTPUT_DIR/algo`).

## ALFWorld data — feasibility & sizing

ALFWorld game data is **not** committed (it is the standard dataset).

- Source: GitHub releases (`alfworld/alfworld`). Reachable from here; total
  **~143 MB zipped** → **~1.7 GB extracted** (4027 `game.tw-pddl`, 7080
  `traj_data.json`, plus `logic/`). All 191 split gamefiles resolve against it.
- Runtime: `alfworld` + `textworld[pddl]`. `textworld[pddl]` pulls
  `fast-downward-textworld`, an **sdist with no wheel** that needs `cmake` + a C++
  toolchain — installed via `apt` in the eval/algo Dockerfiles (`build-essential cmake`).
  Only the **TextWorld** engine (`AlfredTWEnv`) is used; no THOR/ai2thor/torch.

### "Full dataset → git" recommendation

**Do NOT commit the ~1.7 GB game payload to git.** This repo commits only:
the split manifest (`data/alfworld_path_split/`, 191 items, ~70 KB), a 3-game
miniset (`data/alfworld_miniset/`, ~0.8 MB), and `data/fetch_data.sh`.

For the full board, fetch the data at install/run time:
`ALFWORLD_DATA=<vol> bash data/fetch_data.sh` (or build the eval image with
`ALFWORLD_DATASET=none` and mount `$ALFWORLD_DATA`). If the payload must live in
the repo, use **Git LFS** for the zips — never raw game files. Fetch-on-install is
preferred (matches how `alfworld-download` works upstream).

## Build & run (miniset)

```bash
# eval image (bakes the 3-game miniset + split)
bash alfworld_eval/build.sh
# the noskill baseline algo (the one submitted)
bash algo_noskill/build.sh
# load into kind
kind load docker-image localhost:5000/l_creator/alfworld-eval:latest --name lb
kind load docker-image localhost:5000/p_user1/alfworld-noskill:v1   --name lb
```

Then create a board (`l_creator`, `evaluator_image=localhost:5000/l_creator/alfworld-eval:latest`,
`resource_spec` as a JSON object) and submit `algorithm_image_url=localhost:5000/p_user1/alfworld-noskill:v1`
as `p_user1`.
