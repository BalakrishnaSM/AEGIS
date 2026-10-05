.PHONY: install test eval gate serve docker live-eval
DATASET ?= ../dataset
install: ; pip install -r requirements-dev.txt
test: ; AEGIS_DATASET=$(DATASET) PYTHONPATH=src pytest -q
eval: ; PYTHONPATH=src python eval/run_eval.py $(DATASET) && python eval/report.py
gate: eval ; python eval/gate.py
serve: ; PYTHONPATH=src python -m aegis serve --db data/aegis.db
docker: ; docker build -t aegis:1.0.0 .
live-eval: ; PYTHONPATH=src python eval/run_live_llm_eval.py $(DATASET)    # needs ANTHROPIC_API_KEY
