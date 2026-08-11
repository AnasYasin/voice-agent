# Activate the env first:  conda activate voice-agent
#
# If it does not exist yet:
#   conda create -n voice-agent python=3.11 -c conda-forge --override-channels -y
#   conda activate voice-agent && python -m ensurepip --upgrade
#   make install

.PHONY: install test test-live prep eval roundtrip run fmt clean

install:                   ## Install everything in requirements.txt
	python -m pip install -r requirements.txt

test:                      ## Unit tests. No API keys, no network.
	pytest -m "not live"

test-live:                 ## Tests that call real APIs. Needs keys in .env
	pytest -m live

prep:                      ## Band-limit raw samples to 8 kHz telephone audio
	python eval/prepare_audio.py

eval:                      ## Score STT providers on prepared samples
	python eval/run_stt_eval.py

roundtrip:                 ## Azure TTS -> 8 kHz -> STT. Plumbing test, not the gate
	python scripts/roundtrip.py

run:
	python -m voice_agent.main

fmt:
	ruff format . && ruff check --fix .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
