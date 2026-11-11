# Speech to text

Every path that turns Diablo's audio into words, and where its model runs.

| path | where it transcribes | model |
| --- | --- | --- |
| Desk voice loop (voice/__main__.py) | gvoice daemon on banditbox, `GET /file/<wav>` on 127.0.0.1:8756 | large-v3-turbo int8_float16, RTX 3090 |
| Ears, wake then request (voice/ears.py) | the same gvoice daemon | the same |
| Dictation checks (voice/dictation.py) | the same gvoice daemon | the same |
| gvoice dictation itself | in process on banditbox | the same, two copies |
| Wake word (voice/wake.py) | in process on banditbox | openWakeWord features and diablo.onnx, no transcript |
| Telegram voice notes (hub/bot.py, hub/stt.py) | diablo-stt on banditbox, Groq when it is not up | large-v3-turbo int8_float16, or Groq whisper-large-v3-turbo |
| Phone and desktop apps | they send text; neither records audio for transcription | none |

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
