# Activate the env first:  conda activate voice-agent
#
# If it does not exist yet:
#   conda create -n voice-agent python=3.11 -c conda-forge --override-channels -y
#   conda activate voice-agent && python -m ensurepip --upgrade
#   make install

.PHONY: install test test-live lint prep eval roundtrip run talk serve web livekit db calls fmt clean

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

livekit:                   ## Start LiveKit and Postgres. A call needs both
	docker compose up -d livekit postgres

db:                        ## A psql shell into the call transcripts
	docker compose exec postgres psql -U agent -d voice_agent

calls:                     ## The last ten calls, newest first
	docker compose exec postgres psql -U agent -d voice_agent -c \
	  "select call_id, caller_id, outcome, started_at from calls order by started_at desc limit 10"

web:                       ## Dev client on :8081 for `make run`. The demo is `make serve`.
	python -m http.server 8081 --directory web  # then open /dev.html

run:                       ## The appointment form. Needs `make livekit` and `make web` first
	python -m voice_agent.main --name انس --date کل --time چار

talk:                      ## No script, just the persona in lang/<code>/agent.yaml
	python -m voice_agent.main --talk

serve:                     ## The demo server on :8080. One process, many callers.
	python -m voice_agent.main --serve

fmt:
	ruff format . && ruff check --fix .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
