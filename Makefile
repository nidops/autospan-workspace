ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif
CONFIG ?=
PLUGIN ?=
PROFILE ?=
PROFILE_ARG = $(if $(strip $(PROFILE)),--profile "$(PROFILE)")
PLUGIN_ARG = $(if $(strip $(PLUGIN)),--plugin "$(PLUGIN)")
CONSOLE_ARGS ?=
TEST ?=
TEST_ARG = $(if $(strip $(TEST)),-k "$(TEST)")
REPORT ?= artifacts/report.html
# The html entry-point name collides with Python's stdlib html module.
# Block its autoload alias before loading the plugin by its full module name.
REPORT_ARGS = -p metadata -p no:html -p pytest_html.plugin --html="$(REPORT)" --self-contained-html
TOOLS_PY := .tools/bin/python
ifeq ($(OS),Windows_NT)
TOOLS_PY := .tools/Scripts/python.exe
endif
.DEFAULT_GOAL := help
.PHONY: help tools menuconfig config preset bootstrap sync install doctor status freeze test hw hw-list exec console plugins select deselect test-workspace format lint pre-commit pre-commit-install
help:
	@$(PYTHON) scripts/ws.py help

tools:
	@$(PYTHON) scripts/ws.py tools

menuconfig: tools
	@$(TOOLS_PY) scripts/ws.py menuconfig

config: tools
	@$(TOOLS_PY) scripts/ws.py config

# Load a supplied component preset, e.g. PROFILE=minimal.
preset: tools
	@$(TOOLS_PY) scripts/ws.py preset $(PROFILE)

bootstrap sync install: tools
	@$(TOOLS_PY) scripts/ws.py $@ $(PLUGIN_ARG)

doctor:
	@$(PYTHON) scripts/ws.py doctor $(PLUGIN_ARG)

status:
	@$(PYTHON) scripts/ws.py status $(PLUGIN_ARG)

freeze: tools
	@$(TOOLS_PY) scripts/ws.py freeze $(PLUGIN_ARG)

# Plugin mode: make test PLUGIN=PLUGIN_NAME TEST_PATH=tests/unit
# Editable mode: make test TEST_PATH=repos/PLUGIN_NAME/tests
test:
	@$(PYTHON) scripts/ws.py test $(PLUGIN_ARG) $(PROFILE_ARG) $(if $(strip $(CONFIG)),--config "$(CONFIG)") $(TEST_PATH) -- $(REPORT_ARGS) $(TEST_ARG) $(PYTEST_ARGS)

# The selected profile supplies config and scenarios; collection never opens devices.
# Do NOT add -m hardware: explicit ordinary scenarios must remain selectable.
hw hw-list:
	@$(PYTHON) scripts/ws.py test $(PLUGIN_ARG) $(PROFILE_ARG) $(if $(strip $(CONFIG)),--config "$(CONFIG)") $(TEST_PATH) -- --hardware -v $(TEST_ARG) $(if $(filter hw-list,$@),--collect-only,$(REPORT_ARGS)) $(PYTEST_ARGS)

# e.g. make exec COMMAND="autospan plugins"
exec:
	@$(PYTHON) scripts/ws.py exec $(PLUGIN_ARG) -- $(COMMAND)

# e.g. make console CONFIG=bench.local.yaml CONSOLE_ARGS="--hardware --plain"
console:
	@$(PYTHON) scripts/ws.py console $(PLUGIN_ARG) $(PROFILE_ARG) $(if $(strip $(CONFIG)),--config "$(CONFIG)") -- $(CONSOLE_ARGS)

plugins select: tools
	@$(TOOLS_PY) scripts/ws.py $@ $(PLUGIN_ARG) $(if $(filter select,$@),$(PROFILE_ARG))

deselect: tools
	@$(TOOLS_PY) scripts/ws.py deselect

test-workspace: tools
	@$(TOOLS_PY) -m pytest -q tests

format: tools
	@$(TOOLS_PY) -m ruff format aworkspace scripts tests

lint: tools
	@$(TOOLS_PY) -m ruff check aworkspace scripts tests
	@$(TOOLS_PY) -m ruff format --check aworkspace scripts tests
	@$(TOOLS_PY) -m compileall -q aworkspace scripts tests

pre-commit: tools
	@$(TOOLS_PY) -m pre_commit run --all-files

pre-commit-install: tools
	@$(TOOLS_PY) -m pre_commit install
