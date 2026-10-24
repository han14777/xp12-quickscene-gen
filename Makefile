PYTHON ?= python

.PHONY: gui test

gui:
	$(PYTHON) -m quickscene gui

test:
	$(PYTHON) -m pytest -q
