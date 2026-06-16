#!/usr/bin/env bash
# =============================================================================
# Xarena — interactive, from-scratch installer.
#
# Brings up the whole K8s-native leaderboard (backend + frontend) plus the
# SpreadsheetBench×Skill harness (3 algo images + 3 eval images) in a local
# kind cluster, step by step. Each step prints what it will do and asks to
# continue; steps are idempotent where feasible so you can re-run safely.
#
# Repo-relative only: no /home/admin/... paths, no ~/.aikey reads. Keys are
# prompted once and stored in a GITIGNORED config.local.env (chmod 600); they
# are never echoed back and never committed.
#
# Usage:
#   ./install.sh              # interactive
#   ./install.sh --yes        # assume "yes" to every step prompt (still prompts for keys if missing)
#   ./install.sh --help
# =============================================================================
set -uo pipefail

# ---- locate repo root (this script lives at repo root) ----------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BENCH="$ROOT/leadeboard_apps/spreadsheets_bench"
EVAL_DIR="$BENCH/skillopt_eval"
RUN_DIR="$ROOT/run"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
CONFIG_ENV="$ROOT/config.local.env"

# ---- knobs (env-overridable) ------------------------------------------------
REG="${REG:-localhost:5000}"
CLUSTER="${CLUSTER:-lb}"
NS="${NS:-leaderboard}"
KIND_NODE_IMAGE="${KIND_NODE_IMAGE:-kindest/node:v1.31.0}"
KIND_NODE_MIRROR="${KIND_NODE_MIRROR:-docker.m.daocloud.io/kindest/node:v1.31.0}"
REGISTRY_IMAGE="${REGISTRY_IMAGE:-registry:2}"
REGISTRY_MIRROR="${REGISTRY_MIRROR:-docker.m.daocloud.io/library/registry:2}"
KIND_VERSION="${KIND_VERSION:-v0.23.0}"
BACKEND_IMG="${BACKEND_IMG:-$REG/leaderboard-api:v2-k8s}"
FRONTEND_IMG="${FRONTEND_IMG:-$REG/leaderboard-ui:v2-k8s}"
HOST_FRONTEND_PORT="${HOST_FRONTEND_PORT:-7799}"
HOST_BACKEND_PORT="${HOST_BACKEND_PORT:-30001}"

ASSUME_YES=0

# ---- colors / logging -------------------------------------------------------
if [ -t 1 ]; then C_B='\033[0;34m'; C_G='\033[0;32m'; C_Y='\033[0;33m'; C_R='\033[0;31m'; C_N='\033[0m'; else C_B=''; C_G=''; C_Y=''; C_R=''; C_N=''; fi
say()  { printf "${C_B}==>${C_N} %s\n" "$*"; }
ok()   { printf "${C_G}  OK${C_N} %s\n" "$*"; }
warn() { printf "${C_Y}  ! ${C_N} %s\n" "$*"; }
err()  { printf "${C_R} ERR${C_N} %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
  cat <<'USAGE'
Xarena installer — interactive, from-scratch.

Brings up the whole K8s-native leaderboard (backend + frontend) plus the
SpreadsheetBench x Skill harness (3 algo images + 3 eval images) in a local
kind cluster, step by step. Each step prints what it will do and asks to
continue; steps are idempotent where feasible so you can re-run safely.

Repo-relative only: no machine-specific host paths, no ~/.aikey reads. Keys are
prompted once and stored in a GITIGNORED config.local.env (chmod 600); they are
never echoed back and never committed.

Steps:
  0 preflight  1 keys  2 registry  3 vendor-check  4 build-images
  5 kind-cluster  6 load-images  7 k8s-deploy(+secret)  8 init-db  9 verify

Usage:
  ./install.sh         interactive
  ./install.sh --yes   assume "yes" to step prompts (still prompts for missing keys)
  ./install.sh --help  this help

Useful env overrides: REG, CLUSTER, NS, KIND_NODE_IMAGE, HOST_FRONTEND_PORT,
HOST_BACKEND_PORT.
USAGE
  exit 0
}

# step <title>  -> prints a banner and asks to continue; returns 0 to run, 1 to skip
step() {
  echo
  printf "${C_B}========================================================${C_N}\n"
  printf "${C_B}STEP: %s${C_N}\n" "$1"
  printf "${C_B}========================================================${C_N}\n"
}
confirm() {
  # confirm "<prompt>"  (default Yes). Honors --yes.
  local p="${1:-Continue?}"
  if [ "$ASSUME_YES" = "1" ]; then echo "  $p [Y/n] y (auto)"; return 0; fi
  local a
  read -r -p "  $p [Y/n] " a || a=""
  case "${a:-y}" in y|Y|yes|YES|"") return 0;; *) return 1;; esac
}

have() { command -v "$1" >/dev/null 2>&1; }
ver()  { "$1" --version 2>/dev/null | head -1 || true; }

# =============================================================================
# arg parsing
# =============================================================================
for arg in "$@"; do
  case "$arg" in
    --help|-h) usage;;
    --yes|-y)  ASSUME_YES=1;;
    *) die "unknown arg: $arg (see --help)";;
  esac
done

cat <<BANNER
${C_B}
  Xarena installer — K8s-native leaderboard + SpreadsheetBench×Skill
${C_N}
  repo root : $ROOT
  registry  : $REG
  cluster   : $CLUSTER   namespace: $NS
  This is interactive: each step asks before running. Ctrl-C to abort.
BANNER

# =============================================================================
# STEP 0 — preflight
# =============================================================================
step "0/9  Preflight (docker / kubectl / kind / node / curl)"
say "Checking required tools."
if confirm "Run preflight checks now?"; then
  set +e
  MISSING=()
  for t in docker kubectl node npm curl; do
    if have "$t"; then ok "$t : $(ver "$t")"; else warn "$t : MISSING"; MISSING+=("$t"); fi
  done
  if have kind; then ok "kind : $(kind version 2>/dev/null)"; else
    warn "kind : MISSING"
    if confirm "Download kind ${KIND_VERSION} to /usr/local/bin?"; then
      ARCH="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
      URL="https://kind.sigs.k8s.io/dl/${KIND_VERSION}/kind-linux-${ARCH}"
      if curl -fsSL -o /tmp/kind "$URL"; then
        sudo install -m 0755 /tmp/kind /usr/local/bin/kind && ok "kind installed: $(kind version 2>/dev/null)"
      else warn "kind download failed ($URL); install it manually."; MISSING+=("kind"); fi
    else MISSING+=("kind"); fi
  fi
  if ! docker info >/dev/null 2>&1; then warn "docker daemon not reachable (is it running / do you have permission?)"; fi
  warn "Note: Docker Hub may be TLS-intercepted here. Base images fall back to the daocloud mirror ($KIND_NODE_MIRROR / $REGISTRY_MIRROR)."
  set -e
  if [ "${#MISSING[@]}" -gt 0 ]; then
    warn "Missing tools: ${MISSING[*]}"
    confirm "Continue anyway?" || die "Install the missing tools and re-run."
  fi
else warn "skipped preflight."; fi

# =============================================================================
# STEP 1 — API keys -> gitignored config.local.env
# =============================================================================
step "1/9  API keys -> config.local.env (gitignored, chmod 600)"
say "DEEPSEEK_API_KEY is required (eval/training). DASHSCOPE_API_KEY is optional (xskill embeddings + agent-LLM feature)."
if [ -f "$CONFIG_ENV" ] && grep -q '^DEEPSEEK_API_KEY=..*' "$CONFIG_ENV"; then
  ok "config.local.env already has DEEPSEEK_API_KEY."
  confirm "Re-enter keys (overwrite)?" && NEED_KEYS=1 || NEED_KEYS=0
else NEED_KEYS=1; fi
if [ "${NEED_KEYS:-1}" = "1" ]; then
  if confirm "Enter API keys now?"; then
    DK=""; while [ -z "$DK" ]; do read -r -s -p "  DEEPSEEK_API_KEY (required, hidden): " DK; echo; [ -z "$DK" ] && warn "cannot be empty."; done
    read -r -s -p "  DASHSCOPE_API_KEY (optional, hidden, Enter to skip): " AK; echo
    umask 177
    {
      echo "# Xarena local secrets — GITIGNORED. Do not commit. chmod 600."
      echo "DEEPSEEK_API_KEY=$DK"
      echo "DASHSCOPE_API_KEY=${AK:-}"
    } > "$CONFIG_ENV"
    chmod 600 "$CONFIG_ENV"
    unset DK AK
    ok "wrote $CONFIG_ENV (chmod 600). Keys not echoed."
  else warn "skipped key entry — later steps that need keys will fail."; fi
fi
# load keys for later steps (do not print)
if [ -f "$CONFIG_ENV" ]; then set -a; . "$CONFIG_ENV"; set +a; fi
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}"

# =============================================================================
# STEP 2 — local registry on :5000
# =============================================================================
step "2/9  Local registry ($REGISTRY_IMAGE on :5000)"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^registry$'; then
  ok "registry container already running."
elif confirm "Start a local registry on :5000?"; then
  if ! docker image inspect "$REGISTRY_IMAGE" >/dev/null 2>&1; then
    say "pulling $REGISTRY_IMAGE (mirror fallback on failure)…"
    docker pull "$REGISTRY_IMAGE" 2>/dev/null || {
      warn "Docker Hub pull failed; trying mirror $REGISTRY_MIRROR"
      docker pull "$REGISTRY_MIRROR" && docker tag "$REGISTRY_MIRROR" "$REGISTRY_IMAGE"
    }
  fi
  docker rm -f registry >/dev/null 2>&1 || true
  docker run -d --restart=always -p 5000:5000 --name registry "$REGISTRY_IMAGE" >/dev/null \
    && ok "registry up on :5000" || warn "failed to start registry."
else warn "skipped registry."; fi

# =============================================================================
# STEP 3 — vendor check
# =============================================================================
step "3/9  Vendor check (offline self-contained sources)"
if confirm "Verify vendor/ sources exist?"; then
  miss=0
  for v in SkillOpt Trace2Skill data_root xskill; do
    if [ -e "$ROOT/vendor/$v" ]; then ok "vendor/$v ($(du -sh "$ROOT/vendor/$v" 2>/dev/null | cut -f1))"; else err "vendor/$v MISSING"; miss=1; fi
  done
  [ -f "$ROOT/vendor/data_root/dataset.json" ] && ok "vendor/data_root/dataset.json present" || { err "vendor/data_root/dataset.json MISSING"; miss=1; }
  [ "$miss" = 0 ] || die "vendor/ incomplete — cannot build offline. (Did the clone include vendor/?)"
else warn "skipped vendor check."; fi

# =============================================================================
# STEP 4 — build all 8 images
# =============================================================================
step "4/9  Build images (2 platform + 3 eval + 3 algo)"
warn "This needs internet for base images / pip / npm (claude-code). It can take a while."
if confirm "Build all images now?"; then
  export http_proxy="${http_proxy:-}" https_proxy="${https_proxy:-}" \
         no_proxy="${no_proxy:-localhost,127.0.0.1,.local,$REG}"
  # --- platform: backend ---
  say "building backend -> $BACKEND_IMG"
  ( cd "$BACKEND" && docker build -t "$BACKEND_IMG" . && docker push "$BACKEND_IMG" ) \
    && ok "backend built+pushed" || warn "backend build failed"
  # --- platform: frontend ---
  say "building frontend -> $FRONTEND_IMG"
  ( cd "$FRONTEND" && docker build -t "$FRONTEND_IMG" . && docker push "$FRONTEND_IMG" ) \
    && ok "frontend built+pushed" || warn "frontend build failed"
  # --- eval x3 (single/multi/react) — shared _ctx staged from vendor/ ---
  say "building eval images (single/multi/react)"
  ( cd "$EVAL_DIR" && bash build.sh ) && ok "eval images built+pushed" || warn "eval build failed"
  # --- algo x3 ---
  for a in skillopt trace2skill xskill; do
    say "building algo-$a"
    ( cd "$BENCH/algo_$a" && bash build.sh ) && ok "algo-$a built+pushed" || warn "algo-$a build failed"
  done
  say "registry catalog:"; curl -s "http://$REG/v2/_catalog" || true; echo
else warn "skipped image builds."; fi

# =============================================================================
# STEP 5 — kind cluster
# =============================================================================
step "5/9  kind cluster '$CLUSTER' (NodePort 30001->host:$HOST_BACKEND_PORT, 30002->host:$HOST_FRONTEND_PORT)"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  ok "cluster '$CLUSTER' already exists."
elif confirm "Create kind cluster '$CLUSTER'?"; then
  if ! docker image inspect "$KIND_NODE_IMAGE" >/dev/null 2>&1; then
    say "pulling node image $KIND_NODE_IMAGE (mirror fallback)…"
    docker pull "$KIND_NODE_IMAGE" 2>/dev/null || {
      warn "Docker Hub pull failed; trying mirror $KIND_NODE_MIRROR"
      docker pull "$KIND_NODE_MIRROR" && docker tag "$KIND_NODE_MIRROR" "$KIND_NODE_IMAGE"
    }
  fi
  # free host:7799 if an old docker frontend holds it
  docker rm -f leaderboard-ui >/dev/null 2>&1 || true
  kind create cluster --name "$CLUSTER" --image "$KIND_NODE_IMAGE" --config "$RUN_DIR/kind-config.yaml" \
    && ok "cluster created" || die "kind create cluster failed"
  kubectl get nodes
else warn "skipped cluster creation."; fi

# =============================================================================
# STEP 6 — load images into kind
# =============================================================================
step "6/9  Load images into kind node"
ALL_IMAGES=(
  "$BACKEND_IMG" "$FRONTEND_IMG"
  "$REG/l_creator/spreadsheet-eval-single:latest"
  "$REG/l_creator/spreadsheet-eval-multi:latest"
  "$REG/l_creator/spreadsheet-eval-react:latest"
  "$REG/p_user1/algo-skillopt:v1"
  "$REG/p_user1/algo-trace2skill:v1"
  "$REG/p_user1/algo-xskill:v1"
)
if confirm "kind load all 8 images now?"; then
  for img in "${ALL_IMAGES[@]}"; do
    if docker image inspect "$img" >/dev/null 2>&1; then
      say "loading $img"; kind load docker-image --name "$CLUSTER" "$img" && ok "loaded $img" || warn "load failed: $img"
    else warn "image not present locally (build it first): $img"; fi
  done
else warn "skipped image load."; fi

# =============================================================================
# STEP 7 — deploy to k8s (namespace/rbac/priority + secret + backend/frontend)
# =============================================================================
step "7/9  Deploy to Kubernetes (+ create algo-secrets Secret)"
if confirm "Apply k8s manifests and create the Secret now?"; then
  kubectl apply -f "$BACKEND/namespace.yaml"
  kubectl apply -f "$BACKEND/rbac.yaml"
  kubectl apply -f "$BACKEND/priority_class.yaml"
  ok "namespace / rbac / priorityclass applied."

  # algo-secrets from config.local.env (recreate idempotently). Never echo values.
  if [ -n "$DEEPSEEK_API_KEY" ]; then
    kubectl -n "$NS" delete secret algo-secrets >/dev/null 2>&1 || true
    kubectl -n "$NS" create secret generic algo-secrets \
      --from-literal=DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
      --from-literal=DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}" \
      && ok "Secret 'algo-secrets' created (values not printed)."
  else
    warn "DEEPSEEK_API_KEY empty — creating an EMPTY algo-secrets; eval/training will fail until you set it."
    kubectl -n "$NS" create secret generic algo-secrets \
      --from-literal=DEEPSEEK_API_KEY="" --from-literal=DASHSCOPE_API_KEY="" >/dev/null 2>&1 || true
  fi

  # optional: patch the agent-LLM key into the backend Deployment env from DASHSCOPE
  kubectl apply -f "$BACKEND/beckend_deployment.k8s.yaml"
  kubectl apply -f "$BACKEND/frontend_deployment.k8s.yaml"
  ok "backend + frontend applied."
  if [ -n "${DASHSCOPE_API_KEY:-}" ] && confirm "Inject DASHSCOPE key into backend AGENT_LLM_API_KEY (agent-LLM feature)?"; then
    kubectl -n "$NS" set env deploy/leaderboard-api AGENT_LLM_API_KEY="$DASHSCOPE_API_KEY" >/dev/null \
      && ok "AGENT_LLM_API_KEY set on backend (value not printed)."
  fi

  say "waiting for rollouts…"
  kubectl -n "$NS" rollout status deploy/leaderboard-api  --timeout=180s || warn "backend rollout not ready"
  kubectl -n "$NS" rollout status deploy/leaderboard-ui   --timeout=180s || warn "frontend rollout not ready"
else warn "skipped deploy."; fi

# =============================================================================
# STEP 8 — init-db + default users
# =============================================================================
step "8/9  Initialize DB + create default users"
if confirm "Run 'flask init-db --create-defaults' in the backend pod?"; then
  POD="$(kubectl -n "$NS" get pod -l app=leaderboard-api -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
  if [ -n "$POD" ]; then
    # the Flask app exposes `init-db`; --create-defaults adds admin/l_creator/p_user1
    if kubectl -n "$NS" exec "$POD" -- flask --app app.py init-db --create-defaults 2>/tmp/initdb.log \
       || kubectl -n "$NS" exec "$POD" -- flask init-db --create-defaults 2>>/tmp/initdb.log; then
      ok "DB initialized + default users created."
    else warn "init-db failed; see: kubectl -n $NS logs $POD"; tail -5 /tmp/initdb.log 2>/dev/null || true; fi
  else warn "no backend pod found; is the deploy ready?"; fi
else warn "skipped init-db."; fi

# =============================================================================
# STEP 9 — verify + summary
# =============================================================================
step "9/9  Verify + summary"
if confirm "Run connectivity checks?"; then
  BCODE="$(curl -s -m5 -o /dev/null -w '%{http_code}' -X POST "http://localhost:$HOST_BACKEND_PORT/api/login" \
            -H 'Content-Type: application/json' -d '{"username":"admin","password":"adminpass"}' 2>/dev/null || echo 000)"
  FCODE="$(curl -s -m5 -o /dev/null -w '%{http_code}' "http://localhost:$HOST_FRONTEND_PORT/_stcore/health" 2>/dev/null || echo 000)"
  [ "$BCODE" = "200" ] && ok "backend login NodePort:$HOST_BACKEND_PORT -> 200" || warn "backend login -> $BCODE (expected 200)"
  [ "$FCODE" = "200" ] && ok "frontend health host:$HOST_FRONTEND_PORT -> 200" || warn "frontend health -> $FCODE (expected 200)"
fi

cat <<DONE

${C_G}========================================================${C_N}
${C_G}Xarena install finished.${C_N}
${C_G}========================================================${C_N}

  Frontend : http://<this-host>:$HOST_FRONTEND_PORT
  Backend  : http://<this-host>:$HOST_BACKEND_PORT   (API under /api)

  Default logins:
    admin     / adminpass     (admin)
    l_creator / creatorpass   (creator / maintainer)
    p_user1   / user1pass     (participant)

  Submit an algorithm (as a participant), then create/choose a board whose
  evaluator is e.g. $REG/l_creator/spreadsheet-eval-single:latest, and submit:
    algorithm_image_url = $REG/p_user1/algo-skillopt:v1   (or trace2skill / xskill)
  Full split instead of reduced: add  TRAIN_SCALE=full  to the submission env_text.

  Inspect a run:
    kubectl -n $NS get jobs,pods
    kubectl -n $NS logs <pod> -c submitter-container   # algorithm training
    kubectl -n $NS logs <pod> -c evaluator-container   # evaluation

  Tear down:  kind delete cluster --name $CLUSTER
  Secrets live in config.local.env (gitignored) and the 'algo-secrets' Secret.
DONE
