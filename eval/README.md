# Phase 0 — does Urdu STT actually work on phone audio?

This is the go/no-go gate. Run it before building anything else.

Every accuracy number quoted during planning was an estimate. The published
3.1% WER for Urdu is measured on clean studio audio. Your calls are 8 kHz
narrowband with background noise. This measures the real thing.

## Collect samples

25 to 30 recordings, different speakers, some noisy. Best source is actual
phone calls recorded with a call recorder app. WhatsApp voice notes work too,
since `prepare_audio.py` band-limits them to phone quality anyway.

Have people answer the questions the agent will ask, the way they really would.
Include the messy cases on purpose: Roman Urdu, English numbers, "kya?",
talking over the question, a TV in the background.

Drop them in `samples/raw/`. That folder is gitignored — these are other
people's voices.

## Run it

```
make prep                                                     # band-limit to 8 kHz
python eval/run_stt_eval.py --pack ur-PK                      # Urdu, eval/expected.csv
python eval/run_stt_eval.py --pack sd-PK --expected eval/expected_sd.csv
```

Every recognizer with a key in `.env` runs: Scribe v1 file, Scribe v2 realtime,
Gemini Live, Gemini batch, Deepgram Nova-3. Results land in `eval/results_<pack>.md`
with WER and CER per recognizer and every transcript next to its reference.

## Known sentences, Sindhi

`expected_sd.csv` lists nine sentences with the filename each recording must
have, `sd_01.wav` to `sd_09.wav`. Record each one, drop the files in
`samples/raw/`, run the two commands above. Four are human translations from
FLORES-200, five are the appointment script's own vocabulary.

## Fill in expected.csv

One row per sample. `transcript` is what was said; `expected_slot` is the thing
that actually matters.

## What the result means

Word error rate is the headline number, but **slot accuracy is the decision**.
A transcript can be 30% wrong and still be perfectly usable if the agent
reliably extracts "yes, 3pm". Judge on slot accuracy.

| Slot accuracy | Verdict |
|---|---|
| Above 90% | Build it |
| 75 to 90% | Build it, lean harder on DTMF fallback |
| Below 75% | Narrow the questions, make DTMF primary, re-measure |

Below 75% is not a reason to change vendors. It is a reason to change the
script so the questions have fewer possible answers.
