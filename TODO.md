# TODO

## Test Urdu the way Sindhi was tested on 11 Oct 2026

Sindhi got a native speaker, four human-written sentences, every candidate
voice on the same text, and the same sentence across voice models. Urdu has
had none of that. Steps, in order:

- [ ] Pull every line the agent said on Urdu calls from the box's `turns` table.
      A native speaker marks each: OK / wrong word / wrong grammar / not Urdu.
- [ ] Word check. Build an Urdu lexicon from the Urdu Wikipedia dump and flag
      every agent word not in it, with human FLORES `urd_Arab` as the control.
      Sindhi was 0.9-1.1% unseen on Sonnet lines against 1.9-2.3% for humans.
- [ ] Four human-written Urdu sentences from FLORES-200 `urd_Arab`, no names or
      numbers. Render each in every Azure Urdu voice and every ElevenLabs voice
      tagged Urdu. Gemini's speech model does not list Urdu, so no Gemini column.
- [ ] Same sentence, same voice, across voice models: Azure neural vs ElevenLabs
      v3, v4, multilingual v2. The native speaker picks the voice, then the model.
- [ ] Ears. Scribe is the Urdu recognizer today. Gemini Live transcription does
      not list Urdu, so the candidates are Scribe v2 and Deepgram Nova-3. Feed the
      same recordings to both and let the native speaker read the transcripts.
- [ ] A `speech` rule in `lang/ur/agent.yaml` naming the Urdu to speak, once the
      marks show which Hindi or English forms creep in.
- [ ] Ten everyday questions through the real pipeline, caller audio in, reply
      audio out, saved side by side with the text, as `calls/sindhi_full_test`.

## Known, not done

- [ ] `chat_max_tokens` is 200. Two of ten long Sindhi replies were cut mid-word
      on 11 Oct. Raise it, about 320 from that run, and check Urdu replies too.
- [ ] Gemini free tier allows 10 speech requests a day. The Google project needs
      billing before Gemini's voice can be compared for Sindhi.
- [ ] The non-streaming reply path (`ClaudeResponder.respond`) fails on Sindhi
      with truncated JSON. Live calls use `stream`, so only scripted runs hit it.
