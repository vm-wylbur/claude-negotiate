# Author: PB and Claude
# Date: 2026-02-28
# License: (c) Patrick Ball, 2026, GPL-2 or newer
#
# claude-negotiate/Makefile

.PHONY: install test run deploy sync-skills install-facilitator install-negotiate-id

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

# Sync the negotiate skill to client machines (user-wide)
sync-skills:
	rsync -av skills/negotiate/ snowball:~/.claude/skills/negotiate/
	rsync -av skills/negotiate/ scott:~/.claude/skills/negotiate/

# Install facilitator skill into this repo's .claude/skills/ (per-repo, not user-wide)
# Run from the claude-negotiate directory; only activates when cc is launched here
install-facilitator:
	mkdir -p .claude/skills/facilitator
	cp skills/facilitator/SKILL.md .claude/skills/facilitator/

# Add negotiate agent_id line to a repo's CLAUDE.md on a remote host.
# Usage: make install-negotiate-id HOST=snowball REPO=/opt/ntx
# Derives agent_id from the repo directory basename: cc-ntx, cc-tfcs, etc.
install-negotiate-id:
	ssh $(HOST) "echo 'My negotiate agent_id is: cc-$(notdir $(REPO))' >> $(REPO)/CLAUDE.md"
	@echo "Added: My negotiate agent_id is: cc-$(notdir $(REPO))  →  $(HOST):$(REPO)/CLAUDE.md"
