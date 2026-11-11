# Diablo spec

Voice agent and Telegram assistant. Two runtimes, one shared memory. Providers are runtime settings, not code.

## Language roadmap

Four artifacts, three languages, one shared session format (plain json, one file per session in ~/.local/state/diablo/sessions):

- core (rust, core/): the brain, the agent loop, the tool implementations and the session store. Reads the same hub settings as the python services, resolves brain strings with the same rank rules, and exposes a local api on 127.0.0.1:8766 so any front end can drive it. Binaries: `diablo-core serve`, `diablo-core run "task"`, `diablo-core sessions list`.
- tui (rust, tui/diablo-tui): the full screen terminal front end, built: ratatui and crossterm over the core's local api (it spawns diablo-core serve itself when 8766 is quiet). Fresh by default, -c resumes the newest session of the cwd, -r opens the in-terminal picker. It streams tool and result events live, renders the same markdown rules as the python one, queues lines while he works, cancels turns through the cancel endpoint, and /relaunch execs itself (waiting out a running turn first). Installed as diablo-tui in ~/.local/bin; bare `diablo` runs it and falls back to pool/repl.py when the binary is missing.
- web and desktop (typescript): the hub served app and the desktop shell. Typescript stays typescript; the rust enters through the shell and the compiler: tauri (rust core, system webview) replaces electron, and the compiler is tsc-rs (npm, theo browne's rust port of the typescript 7 native compiler, repo pingdotgg/ts-rust), with tsgo as the fallback while tsc-rs is young.
- android (kotlin): the fleet app, already kotlin.
- Python shrinks to the live services (voice loop, hub, pool) until each is ported; nothing new gets written in python.

- hub: runs on homehub (always-on Pi, Ubuntu 24.04, aarch64, Python 3.12). Telegram bot plus memory and settings HTTP API. Works standalone, never depends on banditbox.
- voice: runs on banditbox (Arch, RTX 3090, PipeWire, Python 3.12 venv). Mic to action loop. Reuses the existing gvoice daemon for STT instead of a second whisper stack.
- onboarding: runs on banditbox. One interactive command sets up both sides.

No comments in code. No em dashes anywhere. Sentence case in UI strings.

## Providers

The brain is any OpenAI-compatible chat completions endpoint. Nothing in the repo names a provider as fixed; onboarding offers presets (fireworks, groq, openai, openrouter, together, mistral, deepseek, custom base url) and the model is swappable on the fly. STT for telegram voice notes is an ordered list of OpenAI-compatible audio transcriptions endpoints: diablo-stt on banditbox's GPU first, Groq when banditbox is off or unhealthy (docs/stt.md). TTS is Cartesia or OpenAI, with local piper as the always-present fallback on banditbox.

## Where settings and keys live

All provider settings and keys live in the hub's sqlite settings table, written through the API during onboarding, never committed to the repo. Both agents read them fresh: the hub agent on every call, the voice agent on every utterance, so a model or voice change applies on the next turn with no restart. Telegram can swap the model at runtime with /model.

Settings keys: llm_base_url, llm_api_key, llm_model, gate_model, transcription_base_url, transcription_api_key, transcription_model (the cloud backend), transcription.backends (ordered names, default local,cloud), transcription.<name>.base_url, .model, .api_key, .health, .timeout, tts_provider (cartesia|openai|none), tts_api_key, tts_model, tts_voice_id, telegram_bot_token. Brains are named by rank, the demonic ladder: imp, fiend, baron, viscount, marquis, duke, lord. Each rank.<name> setting holds a brain string, "provider|model-id" or a plain model id on the default provider; an unset rank falls back to llm_*. The quick tongue runs at rank imp, the workers at rank baron, Diablo's own default brain is rank marquis, Raphael at rank duke, and rank lord is reserved for the day a frontier brain arrives: the brainiest brain rides the top rank, and Raphael outranks Diablo always. Providers are registered as provider.<name>.base_url and provider.<name>.api_key, any OpenAI shaped endpoint.

Local machine config ~/.config/diablo/config.toml holds only topology and tuning: hub_url, hub_token, gvoice_base, gvoice_token_path, mic_device, sample_rate, silero_path, piper_dir, min_speech_s, silence_tail_s, max_utterance_s, join_window_s, rms_floor, engage_window_s, gate_calls_per_5min, deaf_after_tts_s.

## Hub HTTP API

FastAPI, uvicorn, binds the Pi's Tailscale address (`DIABLO_HUB_BIND` in local.env) on port 8755. Auth: X-Diablo-Token header carrying hub_token (the machines) or a paired device's own token (phones, browsers), 403 otherwise. SQLite at ~/diablo-hub/diablo.db, WAL mode.

- GET /v1/health -> {"ok": true}, no auth
- POST /v1/pair/code -> {"code": "ABCD-EFGH", "expires_at", "ttl", "url": pair page, "link": diablo://pair?host=..&code=..}, a one-time code for ten minutes. Telegram /pair and the brain's pair_device tool make the same codes.
- POST /v1/pair {"code", "name"} -> {"token", "device": {"id", "name"}, "hub"}, no auth. Trades a live code for a new device token; codes and tokens are stored as sha256. 20 wrong codes in ten minutes burn every open code and answer 429.
- GET /v1/pair/status?code= -> {"state": "waiting"|"paired"|"expired"|"unknown", "device"?}
- GET /v1/whoami -> {"master": bool, "device": {"id", "name"}|null, "hub"}
- GET /v1/devices -> {"devices": [{"id", "name", "created", "last_seen", "current"}]}; DELETE /v1/devices/{id} revokes one (also Telegram /devices revoke <n>, the brain's revoke_device)
- POST /v1/messages {"source": "telegram"|"voice", "role": "user"|"assistant", "text": str, "meta"?: object} -> {"id": int}
- GET /v1/messages?limit=50&source= -> {"messages": [...]} newest last
- POST /v1/facts {"text": str, "tags": [str]} -> {"id": int}
- GET /v1/facts?q=&limit=20 -> {"facts": [...]}
- GET /v1/settings -> {"settings": {...all settings merged with defaults...}}
- GET /v1/settings/{key} -> {"key": str, "value": str|null}
- PUT /v1/settings/{key} {"value": str} -> {"ok": true}
- PUT /v1/profile {"name": str, "embedding": [float], "threshold": float} -> {"ok": true}
- GET /v1/profile -> {"name": str, "embedding": [float], "threshold": float} or 404
- POST /v1/chat/completions -> OpenAI chat.completion shape, and it is always Diablo: the hub agent runs on the last user message, both sides stored with source "api". Auth: X-Diablo-Token or Authorization Bearer with hub_token. The rank field picks his brain, a rank name (imp, baron, marquis, duke, lord) or a "provider|model-id" brain string; the model field is honored the same way when rank is absent, empty or missing means the llm_* default. The response echoes rank and the brain model id. Stream true is a 400 for now. Unknown provider or agent failure is 502 or 400 as fits.
- POST /v1/proxy/completions -> the dumb pipe, nothing Diablo: body proxied verbatim to the default provider (missing or empty model becomes llm_model), stream true forwarded as SSE, nothing stored. Provider failure is 502.

Tables: messages(id, ts, source, role, text, meta), facts(id, ts, text, tags), settings(key, value), profile(id=1, name, embedding, threshold), devices(id, name, token_hash, created, last_seen, revoked), pair_codes(code_hash, created, expires, used, device_id).

## Hub agent behavior

Telegram text: store as user message, context from last 20 messages plus up to 10 facts, chat completions with tools, up to 6 rounds, store reply, send as text. Tools: save_fact(text, tags), recall_facts(query), run_shell(command) on homehub with 30s timeout and 4000 char truncation, now().

Commands: /start, /help, /status shows brain, gate, voice and transcription settings, /model shows or sets llm_model on the fly.

Telegram voice note: download ogg, transcribe through hub/stt.py, the first healthy backend in transcription.backends, POST {base_url}/audio/transcriptions, store as user message with meta {"voice": true}, run the agent, reply. Voice replies: {tts_provider} renders wav; for telegram the wav is transcoded to ogg opus with ffmpeg, any failure falls back to a text reply, never an error to the user.

Polling mode, no webhook, handlers do not block each other.

## Voice agent pipeline

sounddevice 16 kHz mono float32 from a PipeWire input (mic_device substring match, else system default; when mic_device contains echo_cancel the source is the pipewire echo-cancel virtual source and playback targets echo_cancel_sink so the assistant's own voice is cancelled and barge-in is safe) -> silero VAD onnx on CPU, 512-sample chunks with a 64-sample left context -> utterance = 96ms+ speech run followed by silence_tail_s (1.2s) silence, min 0.4s, max 20s, RMS floor 0.0015 -> wav to /tmp/opencode -> GET {gvoice_base}/file/<abs path> with X-Gvoice-Token, the daemon runs faster-whisper large-v3 float16 on the 3090 and returns json with a text field -> hallucination filter -> thought completion check: a transcript ending in . ? ! is complete and handled immediately, otherwise it waits join_window_s (0.8s) and merges continuations so mid-thought pauses never fire on half a thought -> LLM gate (also returns the transcript language) -> agent tool loop running in parallel with the spoken ack -> TTS -> post user text and assistant reply to hub.

Gate and router, one call: the fast tongue (rank imp, falling back to llm_*) streams the answer sentence by sentence and speaks them as they land. Skills are its own tool calls and run silently, a cached line acknowledges them. When real work is needed it answers with one line, TASK: name | instructions or THINK: question, which the harness forwards to Raphael; a RAPHAEL: line means the same thing. No utterance ever produces two spoken answers for simple questions.

Engagement: his name is the only door, for every single task. A transcript with diablo in it is acted upon; anything without it is ignored, no matter how urgent it sounds or how soon it follows his last answer. There is no follow-up path and no judge call: even a continuation seconds later waits for the name again. The window counted from the last acted message only keeps the barge-in ear open while he is speaking, so a named order can cut him off mid-sentence; it never opens a path to a response. The window length lives in the local config and can be retuned live from the hub with an engage_window_s setting. Control words (mute, deafen, listen and friends) obey only when he is named, and acting on one counts the window from it. He always listens from boot: unmuted and hearing, and nothing is answered until he is named.

Barge-in: during TTS playback with echo cancellation on, the capture keeps watching the mic; sustained speech above vad 0.85 for eight chunks stops playback mid-sentence and comes out as a barge utterance. A barge utterance is checked for cut words only: mute or deafen is obeyed on the spot, everything else is dropped, so speech over his own voice never activates him. The interrupted reply's remainder is held, estimated from playback position over sentence boundaries, and spoken after the interruption is handled, prefixed with a line from a pre-generated pool of ten in-character resume phrases (voice/interrupt.py), costing no tokens at runtime. Without echo cancellation the mic stays deaf during playback as before.

TTS scrubber: every spoken text passes interrupt.scrub before synthesis: laughter tokens (kufufu and friends), asterisk stage directions and tildes are removed so no model output can reach the voice as gibberish. The hub message history is scrubbed of the same tokens.

Language: the gate detects the transcript language on every utterance. Replies are spoken in the language Marcus used; Cartesia speed is the configured speech_rate (1.2) for English and 1.0 for every other language. The same rule applies to telegram voice notes via the transcription language.

Gates:
1. Engagement gate, always: the wake word, nothing else, as described under Engagement above. Without his name in the words nothing is ever transcribed into action; with no llm_api_key configured, drop everything and log: the room stays silent until onboarding pushes a key.
2. Speaker gate, phase 2, not built: voice_match true plus profile -> speaker embedding similarity before transcription. Config keys reserved.

Agent: settings-driven chat completions, tools run_shell(command, timeout), save_fact, recall_facts, now(). System prompt: Diablo persona for spoken output, address user as sir, max 2 short sentences unless asked for detail, no markdown, confirm actions with lines like "Right away, sir" or "Done, sir", perform actions through tools rather than describing them.

Safety: run_shell refuses commands matching rm -rf /, mkfs, dd if=, shutdown, reboot, fork bombs.

Echo control: while TTS is playing and for deaf_after_tts_s after, discard all mic audio and reset the VAD. Rate limit: max gate_calls_per_5min LLM gate calls per rolling 5 minutes, further utterances logged and dropped. Dictation pause: while the gvoice daemon reports listening true (SUPER+R deliberate dictation), the capture loop goes fully deaf, drains buffered audio and resets state, resuming when dictation stops.

TTS on banditbox: tts_provider cartesia (POST api.cartesia.ai/tts/bytes, X-API-Key, wav pcm_s16le 24000) or openai (/audio/speech, wav), played with pw-play; cloud failure or no provider falls back to local piper at ~/.local/share/diablo/piper, en_GB-alan-medium; no voice available: log and skip speaking, the reply still reaches the hub.

Memory: every gated utterance and reply goes to hub POST /v1/messages source "voice". Agent context: last 20 messages any source plus up to 10 facts. Hub unreachable: log, continue without memory, never crash.

Logging: ~/.local/state/diablo/voice.log rotating INFO, each gate decision with durations. Run: python -m voice.

## Onboarding

Interactive, idempotent, safe to rerun: python -m onboarding.onboard. Flags: --skip-hub.

1. Deps check: nvidia-smi, pactl, ssh homehub.
2. Brain provider: preset menu or custom base url, api key asked with getpass, model with a per-provider default, gate model defaults to the same.
3. Transcription: groq key, model whisper-large-v3-turbo.
4. Voice: cartesia (lists voices from the api to pick by name) or openai or none. Prints a spend-cap reminder for the key.
5. Telegram bot token via BotFather. hub_token generated once and reused; deploy/rotate-hub-token.sh replaces it everywhere it lives.
6. Mic: shows the system default source, accepts a substring override.
7. Write ~/.config/diablo/config.toml, chmod 600.
8. Unless --skip-hub: run deploy/hub-install.sh, then push all settings to the hub via the API.
9. Piper download if missing. 10. Install the diablo-voice user unit and enable it.
11. Install the diablo-pool, diablo-fast and diablo-stagehand user units and enable them; the closing print lists where to talk to him (voice, diablo talk, telegram).

On rerun, existing values are the defaults; api keys are re-entered.

Phase 2, deferred until Marcus bothers: speaker enrollment recording plus an ECAPA onnx embedding via PUT /v1/profile, the voice_match gate. Not urgent: the echo cancellation already keeps speaker audio (video, media) from opening the gate.

Desktop control (built): voice/desktop.py wraps ydotool against the pre-existing user daemon, socket from YDOTOOL_SOCKET or XDG_RUNTIME_DIR/.ydotool_socket. Tools on the voice agent: mouse_move (absolute), mouse_click, mouse_scroll, type_text, press_key (combo parser with a common-keycode map), list_windows and focus_window via hyprctl. All of it drives real input on Marcus's desktop.

Fleet ssh (built): voice/fleet.py and hub/fleet.py wrap ssh and scp with ConnectTimeout 8, BatchMode, and hard subprocess timeouts. Fleet hosts: banditbox, homehub, kangaeru, nemurenai, diablo-pi, plus any this box lists in DIABLO_FLEET_EXTRA (with notes in DIABLO_FLEET_NOTES); the host running the agent is refused with a hint to use run_shell. Both agents have fleet_run, fleet_fetch, fleet_put.

Hue lights (built): voice/hue.py reads ~/.config/hue-hands/config.json (bridge ip, app key, lamp ids from Marcus's gesture project, single source of truth); hub/hue.py reads the same values from hub settings (hue_bridge_ip, hue_app_key, hue_lamps). Tool hue_light(action on|off|toggle, target corner lamp|ceiling panel|wood lamp|all) on both agents, Hue v1 API over the home LAN. Room light: ceiling panel is the Surimu panel, lamp id 3.

## Pool and panel

The pool daemon (diablo-pool.service, python -m pool) runs Raphael and the worker demons on direct provider calls; opencode is fully out of the loop. pool/brains.py resolves brain strings exactly like the hub's rank system (rank names, provider|model, plain ids on the default provider) and keeps sessions in memory as plain message lists with a local tool loop: run_shell, read_file, write_file, edit_file, with the same destructive-command refusals as the voice agent. A failed call rolls the session back to its entry state; an abort or spent budget unwinds as a timeout and the session survives. Workers run one task each, up to DIABLO_MAX_WORKERS in parallel; .out files are json lines of the assistant replies; inbox notes fold in as a continuation round on the same session; Raphael trims his history to the last 30 messages. The fast panel (diablo-fast.service, python -m pool.panel) is the typed tongue in the tmux stage: fast.in lines answered by one locked native session with the same tools, TASK and THINK lines forwarded to raphael.in, worker results folded in silently, answers written to fast.out for the voice loop's fallback path. The terminal door is deploy/tui.py (diablo talk): lines typed there go to fast.in, his replies stream from fast.out, worker starts and reports scroll by from the task files, /tasks shows the board. The tmux stage keeps a talk window running it. The hands-on agent is pool/repl.py, launched bare as `diablo` from any directory: a full screen curses takeover with a header bar (repo, brain, session title), a live transcript, a status bar (spinner, tool count, scroll position) and an input line with history and editing keys. One session is bound to the folder, with the same four tools, each turn up to TURN_TIMEOUT of tool rounds, work shown as it happens through a step hook, /new and /brain to retool the session, history trimmed to the last 80 messages. His replies render as markdown in the screen: headings, bold, italics, inline code, fenced code boxes, bullet and numbered lists, tables, blockquotes, links and rules. Your lines are echoed with a label; scrolling stays put while you read, end returns to live; typing while he works queues the line. Sessions persist as json under ~/.local/state/diablo/sessions, saved after every turn, title settled from the first user line: `diablo -c` instantly resumes the newest session of the current folder, `diablo -r` opens a full screen picker of all sessions newest first and resumes the chosen one in its own folder. On a pipe the same code degrades to a plain inline loop.

User tools: every pool agent (repl, panel, workers) also loads ~/.config/diablo/tools.py when it exists. The file defines a TOOLS list of OpenAI function specs plus run(name, args); run returns None for names it does not own so unknown tools still fail cleanly in brains.run_tool. A broken file costs its tools, not the built-ins.

## Deploy files

- deploy/hub-install.sh: run from banditbox, remote work over ssh homehub. rsync code, uv-based venv (the Pi has no ensurepip), unit installed with sudo install (allowlisted), enabled via systemctl (allowlisted once the sudoers file below is in place), process started, health curl over Tailscale. Idempotent.
- deploy/diablo-hub.service: system unit, a template rendered by deploy/hub-install.sh: User and home filled in for the box, EnvironmentFile ~/.config/diablo/local.env, ExecStart ~/diablo-hub/venv/bin/uvicorn hub.server:app --host ${DIABLO_HUB_BIND} --port 8755, Restart=always.
- deploy/diablo-voice.service: user unit on banditbox, ExecStart=%h/Projects/diablo/.venv-voice/bin/python -m voice, WorkingDirectory=%h/Projects/diablo, Restart=always, After=pipewire.service.
- deploy/backup-hub.sh plus diablo-backup.service and diablo-backup.timer: nightly user timer on banditbox pulls a VACUUM INTO snapshot of the hub db over ssh homehub, verifies integrity and row counts with python3, keeps the newest 14 in ~/.local/share/diablo/hub-backups.

## Sudo model

Three tiers, passwordless for the routine, second factor for the rest, everything journaled.

1. Tier 1 and 2 in a NOPASSWD allowlist per box, as a separate /etc/sudoers.d/11-diablo-agent file, never editing the existing allowlist file: banditbox adds pacman, install, ln, tee, chown, chmod, dmesg, dmidecode to its existing systemctl, journalctl, wg set; homehub gets systemctl and journalctl added to its existing apt, curl, ln, install, tee, chmod set.
2. Tier 3, anything not allowlisted: face auth on banditbox (Channel 0 of the sudo protocol), password elsewhere. Genuinely destructive work stays staged through sudo_pending scripts regardless.
3. Audit: sudo logs every command to the journal; staged scripts archive to ~/.local/log/sudo_claude/.
4. The agents' own tool runners refuse destructive patterns before elevation even matters, so a prompt injection through email or telegram content hits the same wall.

Rationale: on a single-user machine the password is not what protects the system, the tier boundary is. Routine work never interrupts the user; anything unusual requires a human present; every elevation is logged. Full NOPASSWD ALL was rejected because Diablo's job includes reading untrusted content, and the allowlist tier is the one brake that does not depend on the agent's own judgment.
