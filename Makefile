# Makefile — convenience wrappers around the uv-managed dev commands.
# See CLAUDE.md / README.md for the canonical, documented commands this
# mirrors. Everything here just shells out to `uv`; nothing new is invented.

.PHONY: help install install-gui test test-all lint gui run build clean

help:
	@echo "SpotiSeek dev shortcuts (see CLAUDE.md for details):"
	@echo "  make install      - uv sync (installs deps incl. dev group)"
	@echo "  make install-gui  - uv sync --extra gui --group build"
	@echo "  make test         - offline unit tests (tests/unit), headless Qt"
	@echo "  make test-all     - unit + integration tests (hits live network)"
	@echo "  make lint         - no linter/formatter is configured (see below)"
	@echo "  make gui          - launch the PySide6 desktop GUI"
	@echo "  make run          - run the CLI (use: make run ARGS='info <url>')"
	@echo "  make build        - build a single-file executable via PyInstaller"
	@echo "  make clean        - remove dist/ and build/ artifacts"

install:
	uv sync

install-gui:
	uv sync --extra gui --group build

# GUI tests need a headless Qt platform; harmless for non-GUI tests too.
test:
	QT_QPA_PLATFORM=offscreen uv run pytest tests/unit -q

test-all:
	uv run pytest --run-integration

# No linter/formatter/type-checker is configured for this project by design
# (see CLAUDE.md: "Match the existing style"). This target intentionally
# does nothing but say so, rather than silently add tooling nobody asked for.
lint:
	@echo "No linter/formatter/type-checker is configured for SpotiSeek (see CLAUDE.md)."

gui:
	uv run spotiseek-gui

run:
	uv run spotiseek $(ARGS)

build:
	uv sync --extra gui --group build
	uv run python scripts/build_executable.py

clean:
	rm -rf dist build
