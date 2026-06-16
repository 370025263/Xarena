PY := python3.11
PYTEST := $(PY) -m pytest

.PHONY: test e2e e2e-openclaw-real help

help:
	@echo "  make test                — 单元测试（pytest 全套）"
	@echo "  make e2e                 — Docker E2E（发版前，fake LLM）"
	@echo "  make e2e-openclaw-real   — 真 openclaw + 真 DeepSeek e2e（耗 token）"

test:
	$(PYTEST) tests/ --ignore=tests/docker_e2e --ignore=tests/live -q

e2e:
	tests/docker_e2e/run.sh all

e2e-openclaw-real:
	tests/docker_e2e/openclaw_real_llm/run_host.sh
