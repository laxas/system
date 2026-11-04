# Run the CLI from the venv, not via `uv run`: uv run holds the uv cache lock,
# which would block `uv cache prune` inside the cleanup.
SYSTEM := .venv/bin/system

.PHONY: collect cleanup postgres pyversions space projects audit repos push

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

# origin (GitLab, the default for `git push`) and the GitHub mirror.
push:
	git push origin main
	git push github main
