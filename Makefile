# RoboSpatial-Synth pipeline. Shared file: edit by PR, tag the owners (AGENTS.md).
# `make smoke` must pass before merging. Each stage runs only once its package exists on main,
# so smoke grows by itself as A/B/C/D land their PRs (no Makefile edits needed per PR).

PYTHON ?= python3
SEED   ?= 0
N      ?= 300
DATA   ?= data
SMOKE  := .smoke

.PHONY: smoke test gen tasks clean

smoke:
	rm -rf $(SMOKE) && mkdir -p $(SMOKE)
	$(PYTHON) -m schema.check --data fixtures/
	@if [ -f tasks/__main__.py ]; then \
		echo "+ tasks"; $(PYTHON) -m tasks --data fixtures/ --seed $(SEED) --out $(SMOKE)/tasks.jsonl || exit 1; \
	else echo "- tasks: not on main yet, skipped"; fi
	@if [ -f eval/__main__.py ]; then \
		T=$(SMOKE)/tasks.jsonl; [ -f $$T ] || T=fixtures/tasks.jsonl; \
		echo "+ eval (mock) on $$T"; $(PYTHON) -m eval --model mock --data fixtures/ --tasks $$T --out $(SMOKE)/results --condition image --condition text_only || exit 1; \
	else echo "- eval: not on main yet, skipped"; fi
	@if [ -f report/__main__.py ]; then \
		echo "+ report"; $(PYTHON) -m report --results $(SMOKE)/results --out $(SMOKE)/report || exit 1; \
	else echo "- report: not on main yet, skipped"; fi
	$(MAKE) test
	@echo "smoke OK"

test:
	$(PYTHON) -m pytest -q

gen:
	$(PYTHON) -m gen --seed $(SEED) --n $(N) --out $(DATA)/

tasks:
	$(PYTHON) -m tasks --data $(DATA)/ --seed $(SEED)

clean:
	rm -rf $(SMOKE)
