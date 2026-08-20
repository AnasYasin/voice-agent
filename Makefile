# Activate the env first:  conda activate voice-agent
#
# If it does not exist yet:
#   conda create -n voice-agent python=3.11 -c conda-forge --override-channels -y
#   conda activate voice-agent && python -m ensurepip --upgrade
#   make install

.PHONY: install test test-live lint prep eval roundtrip run web livekit fmt clean

install:                   ## Install everything in requirements.txt
	python -m pip install -r requirements.txt

test:                      ## Unit tests. No API keys, no network.
	pytest -m "not live"

test-live:                 ## Tests that call real APIs. Needs keys in .env
	pytest -m live

lint:                      ## Lint and format check. Also runs inside `make test`
	ruff check src tests scripts eval
	ruff format --check src tests scripts eval

prep:                      ## Band-limit raw samples to 8 kHz telephone audio
	python eval/prepare_audio.py

eval:                      ## Score STT providers on prepared samples
	python eval/run_stt_eval.py

roundtrip:                 ## Azure TTS -> 8 kHz -> STT. Plumbing test, not the gate
	python scripts/roundtrip.py

livekit:                   ## Start the LiveKit server
	docker compose up -d livekit

web:                       ## Serve the browser test client on :8080
	python -m http.server 8080 --directory web

run:                       ## One call. Needs `make livekit` and `make web` first
	python -m voice_agent.main --name انس --date کل --time چار

fmt:
	ruff format . && ruff check --fix .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
