# Author: PB and Claude
# Date: 2026-02-28
# License: (c) Patrick Ball, 2026, GPL-2 or newer
#
# claude-negotiate/Makefile

.PHONY: install test run deploy

install:
	uv sync --extra dev

test:
	uv run pytest tests/ -v

# Run locally for development (requires REDIS_URL in environment or .env)
run:
	uv run python -m claude_negotiate.server --port 7832

# Deploy to snowball: sync code, reinstall, test, restart service
deploy:
	rsync -av --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
		. snowball:/opt/claude-negotiate/
	ssh snowball "cd /opt/claude-negotiate && uv sync --extra dev"
	ssh snowball "cd /opt/claude-negotiate && \
		REDIS_URL=\$$(sudo cat /etc/claude-negotiate/env | grep REDIS_URL | cut -d= -f2-) \
		uv run pytest tests/ -v"
	ssh snowball "sudo systemctl restart claude-negotiate"
	ssh snowball "sudo systemctl status claude-negotiate --no-pager"
