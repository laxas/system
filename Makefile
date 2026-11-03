# Run the CLI from the venv, not via `uv run`: uv run holds the uv cache lock,
# which would block `uv cache prune` inside the cleanup.
SYSTEM := .venv/bin/system

.PHONY: collect cleanup postgres pyversions space projects audit repos

collect:
	$(SYSTEM) collect

cleanup:
	$(SYSTEM) cleanup --apply

postgres:
	$(SYSTEM) postgres

pyversions:
	$(SYSTEM) pyversions

space:
	$(SYSTEM) space

projects:
	$(SYSTEM) projects

audit:
	$(SYSTEM) projects --audit

repos:
	$(SYSTEM) repos
