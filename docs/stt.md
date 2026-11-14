# Speech to text

Every path that turns Diablo's audio into words, and where its model runs.

| path | where it transcribes | model |
| --- | --- | --- |
| Desk voice loop (voice/__main__.py) | gvoice daemon on the desk box, `GET /file/<wav>` on 127.0.0.1:8756, Groq through the hub when it fails | large-v3-turbo int8_float16, RTX 3090, or Groq whisper-large-v3-turbo |
| Ears, wake then request (voice/ears.py) | the same, with the same fallback | the same |
| Ears end-of-turn checks and early reads | the gvoice daemon only | large-v3-turbo |
| Dictation checks (voice/dictation.py) | the gvoice daemon only | the same |
| gvoice dictation itself | in process on banditbox | the same, two copies |
| Ears on a box without a GPU, `stt_via = "hub"` in its config.toml | the hub, `POST /v1/stt`, which uses hub/stt.py below | diablo-stt on banditbox, or Groq when it is not up |
| Wake word (voice/wake.py) | in process on banditbox | openWakeWord features and diablo.onnx, no transcript |
| Telegram voice notes (hub/bot.py, hub/stt.py) | diablo-stt on banditbox, Groq when it is not up | large-v3-turbo int8_float16, or Groq whisper-large-v3-turbo |
| Phone and desktop apps | they send text; neither records audio for transcription | none |

## The desk's fallback (voice/stt.py)

On a box with `stt_via = "gvoice"`, the default, the request's words come from gvoice, with a
1 s connect and a read timeout of 5 s (`stt_local_timeout_s` in config.toml) plus a second for every
10 s of audio. Over 372 clips gvoice took 0.20 s at the median, 1.65 s at p99 and 3.42 s at worst. A refused connection, a timeout, an
error status, a missing token or a bad answer sends the same wav to the hub's `POST /v1/stt` with
`X-Stt-Skip: local`, so the hub hands it to Groq with its own key and model before diablo-stt on
the same GPU, which it tries only when Groq fails. A busy daemon (409 or 503, he is dictating) is asked again twice, 1.2 s
apart, before the clip goes to the hub. A refused connection, a timeout or a missing token skips
gvoice for 30 s, doubling to 5 minutes while it keeps failing; an error answer or a bad body does
the same only the second time in a row. One served clip resets it. End-of-turn checks, early reads,
spoken dictation, voice training, dictation checks and `diablo-ears recheck` never fall back, so
they never pay for a cloud call.

Each request logs one line from `diablo.stt`: `stt served by local in 0.24s`, or as a warning
`stt served by hub cloud in 0.62s, local failed (ConnectError: ...)`. The turn's timeline in
turns.jsonl carries `stt_via` (`local` or `hub cloud`), and `diablo-ears timings` prints it.

## hub/stt.py

`transcription.backends` lists backend names in order, `local,cloud` by default. Each name reads
`transcription.<name>.base_url`, `.model`, `.api_key`, `.health` (`on` or `off`) and `.timeout`
in seconds. `cloud` falls back to the older `transcription_base_url`, `transcription_model` and
`transcription_api_key`. A backend needs all of base_url, model and api_key to be used. An empty value means the default, so
turn a backend off by leaving its name out of `transcription.backends`, and its health check with
`off`.

A backend with health on is asked `GET <base_url>/health` first, 0.8 s timeout, answer cached for
5 s, so a sleeping banditbox costs under a second. A request that fails on the network, times out, has its key refused or gets a 502 to 504 marks that
backend down, and so does a second failure in a row of any other kind, so one bad clip does not.
The wait starts at 5 s and doubles with every failure in a row, up to 5 minutes, and a served
request resets it, per backend and URL. An empty transcript lets the next backend try the clip, and
empty comes back only when every backend heard nothing. Each request logs one line,
`stt served by local (large-v3-turbo) in 0.32s`, naming the model the server says it ran, with what was skipped. `GET /v1/health` names the
active backend and `GET /v1/stt` (token) gives the detail, both from the cached health, refreshed
in the background so neither waits on a sleeping banditbox; `healthy` is null until the first
check. The POST connects within 2 s, so a box that fell asleep inside the cache window is skipped
quickly.

`POST /v1/stt` takes `X-Stt-Skip`, a comma list of backend names to try last for that clip, and
answers with the backend that served it.

A new tier, such as a smaller model on nemurenai, is settings only: add its name to
`transcription.backends` and set its keys.

## diablo-stt on banditbox

voice/stt_server.py, run by deploy/diablo-stt.service from .venv-stt. OpenAI compatible
`POST /v1/audio/transcriptions` and `GET /v1/health` on the tailnet address, port 8757, behind
the bearer token in ~/.config/diablo-stt/token. The hub needs that token as
`transcription.local.api_key`; onboarding (onboarding/onboard.py), run on a box with an NVIDIA GPU,
installs the service and sends the hub this box's tailnet URL and the token; on any other box it
leaves the setting alone. A refused key logs a warning every 10 minutes while notes fall back to Groq.

It loads one large-v3-turbo int8_float16 model (about 1.3 GB of VRAM) on the first request,
only when the GPU has 2.5 GB free, and after 10 minutes idle the process exits so the CUDA context goes too, and systemd starts
an empty one that holds no VRAM. With the GPU full, or for a minute after
the model failed to load, it answers 503, so the hub uses Groq and a game keeps its VRAM. The unit caps it at 4 GB of RAM,
no swap and four cores. `DIABLO_STT_MODEL`, `_COMPUTE`, `_IDLE` and `_MIN_FREE_MB` in
~/.config/diablo-stt/env change those.

Install or update: `deploy/stt-install.sh`.

## Ears on another box

A laptop runs the same diablo-ears as banditbox, with `stt_via = "hub"` and the hub's machine token in
~/.config/diablo/config.toml. The wake word, the recording, the reply and any desktop act stay on that
box; only the clip goes to the hub, which hands it to diablo-stt on banditbox the way it does a voice
note, so a sleeping banditbox costs a Groq call, not a failed turn. The turn names the box (`host`),
so the brain knows where he was heard and the lock guard checks that box's screen.
