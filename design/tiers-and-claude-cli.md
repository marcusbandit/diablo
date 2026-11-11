# Tiers and the claude-cli backend

Setup plan for swapping the demonic rank names for Jorbis-style tier names, and adding
Claude Code as a backend that runs on a subscription with GLM as the standing fallback.
Nothing here makes diablo Claude-only. The brain is still any OpenAI-compatible endpoint;
claude-cli is a second backend kind beside it, and every tier falls back to llm_*.

## Why -p does not lose the cache

Anthropic's prompt cache is server side, keyed on the exact bytes of the prompt prefix:
model, system prompt, tool list, message history. It is not keyed on process or session id.
The API is stateless, so every turn resends the full prefix either way. --resume replays the
stored transcript, the prefix lands byte-identical to the previous request, and the cache
hits. A fresh process with a stable prefix hits just as well as a warm one.

Cache hits die when a byte changes: lane prompt churn, CLAUDE.md edits mid-chat, a tool list
change, compaction, or the 5 minute TTL expiring on a quiet chat. Design rule: lane system
prompts stay byte-stable across turns, only the user message grows. The warm process trick
buys process startup latency, not cache.

## Rename: ranks to tiers

The demonic ladder becomes six tiers, same names and meanings as Jorbis. Two of the seven
ranks fold: fiend was unused in the settings defaults, viscount was unused too.

| old rank  | new tier  | holds                          |
|-----------|-----------|--------------------------------|
| imp       | fast      | classification, quick tongue   |
| fiend     | voice     | spoken replies, speed matters  |
| baron     | standard  | background jobs                |
| marquis   | chat      | Diablo's default brain         |
| viscount  | deep      | folds into deep                |
| duke      | deep      | Raphael                        |
| lord      | max       | frontier brain, "ultrathink"   |

Surface to touch, found by grep:

- core/src/brains.rs: RANKS const becomes TIERS, ["fast", "voice", "standard", "chat", "deep", "max"]
- hub/settings.py: rank.* keys become tier.* keys
- hub/jev.py, hub/persona.py, hub/agent.py, hub/server.py, hub/config.py
- pool/repl.py, pool/panel.py, pool/__main__.py
- core/src/sessions.rs, core/src/turn.rs, core/src/serve.rs
- SPEC.md, onboarding/onboard.py, tests

Migration rules, so nothing breaks on update night:

- settings: read tier.<name> first, fall back to rank.<name>, fall back to llm_*. One write
  of tier.* from rank.* values during onboarding or a one-off script, then rank.* keys can
  sit ignored, delete them a release later.
- API: /v1/chat/completions accepts both a tier field and the old rank field as the same
  value. Old clients, the fleet app included, keep working. Old rank names map through the
  table above for a transition window.
- /model and /status in telegram show tier names, still parse the old ones.

## New backend kind: claude-cli

Providers today are all kind openai, meaning an OpenAI-compatible chat completions base url.
The provider registry gains a kind field: provider.<name>.kind, openai or claude-cli. A
claude-cli provider spawns the Claude Code CLI headless on the local machine, logged in once
with claude login. The subscription is the billing; the token stays in
~/.claude/.credentials.json and is never copied into hub settings.

Brain strings gain an optional third field, effort, only meaningful for claude-cli:

    provider|model|effort        e.g.  claude-cli|opus|medium
    provider|model                     openai providers ignore effort, e.g. fireworks|glm-5p3-flash

Proposed tier defaults, GLM untouched where it is already good:

    tier.fast     fireworks|glm-5p3-flash
    tier.voice    fireworks|glm-5p3-flash   until the warm process below is solid, then claude-cli|sonnet|low
    tier.standard fireworks|glm-5p3-flash
    tier.chat     claude-cli|sonnet|medium
    tier.deep     claude-cli|sonnet|high
    tier.max      claude-cli|<frontier alias>|max

No metered Claude anywhere. The constraint is hard: their coding volume makes per-token
pricing a money shredder, they have the Fireworks bill to prove it.

Spawn shape, per turn:

    claude -p --output-format stream-json --verbose \
      --model <model> --effort <effort> \
      --permission-mode bypassPermissions \
      --append-system-prompt <lane persona + context, byte-stable> \
      --session-id <new uuid>        # or --resume <stored id> on later turns

Core maps stream-json events onto the shared session format: text deltas, tool calls, tool
results. Session ids store in the session file, so resume works across restarts.

Failure and fallback, the part that keeps GLM in charge of the floor:

- core watches for usage-cap events, 429s, non-zero exits and stalls, same health tracker
  idea as the hub's provider handling.
- On failure mid-task, core reruns the turn against the llm_* default provider and feeds the
  transcript so far into the prompt, so GLM picks the task up instead of starting over.
- A failing backend gets a cooldown, and the user is told once, in the next reply, which
  brain served the turn.
- Every turn appends a line to ~/.local/state/diablo/usage.jsonl: ts, tier, backend, model,
  effort, tokens in and out, cache reads, outcome. This log decides the plan question below.

## Escalation

Two mechanics, second one deferred:

1. Phrase triggers, built now. The hub greps the incoming text before dispatch: "think hard"
   or "tænk dig godt om" bumps the turn to deep, "ultrathink" or "use max" bumps to max. This
   is rank selection with a pre-step, no architecture change.
2. Model-initiated escalation, deferred. An escalate tool the brain can call, respawning the
   CLI at higher effort with the same session id. It gives the model control of the rate
   window, so it waits until usage.jsonl shows turns genuinely stranding.

## Voice lane

Spoken replies need low latency, and a cold CLI spawn is the enemy. Phase it: GLM serves
tier.voice at launch. When the claude-cli path is proven, keep one warm claude process in
--input-format stream-json mode for the voice lane, feed it utterances, and restart it after
10 idle minutes so a dead window never owns the process.

## Hosts and login

The hub runs on homehub, the Pi; core and the voice loop run on banditbox. Both hosts can run
the CLI, each needs its own claude login, and the rate window is account-wide, so both hosts
draw from the same 5 hour window. The tiers that matter on the Pi are chat and standard.

## Plan decision: Pro rides, coding never does

Decided by data, not taste: the 20x plan broke against personal coding within days, so no
subscription survives their coding, and metered Claude is off the table for the same reason.
The settled stack:

| line              | cost   | job                                                        |
|-------------------|--------|------------------------------------------------------------|
| GLM flash API     | ~$110  | coding workhorse, voice, fast, standard, and the fallback  |
| Pro subscription  | $20    | diablo's chat and deep brain via claude-cli, Claude Code in fresh windows |

Total holds near today's $130. The subscription serves the small turns only, telegram chat
and the occasional deep task; when the window runs dry, GLM catches the rest with the
transcript handoff, so a dry window costs a brain downgrade, never an outage. Personal
coding stays on GLM permanently; the Pro window's coding hours are a bonus, not a plan.

usage.jsonl decides the upgrade question after a month: if the log shows the chat brain
starving daily and real value on the far side, the Max 5x question reopens with numbers.
If Pro holds most days, done.

## Rollout order

1. Rename commit: tier names across hub, core, pool, onboarding, spec, tests. Old names
   still parse. No backend change, no behavior change.
2. claude-cli backend in core, provider kind + brain string effort + stream-json mapping +
   session resume. Tested through the tui on banditbox first.
3. Fallback wiring: cap detection, transcript handoff to llm_*, health tracker, usage.jsonl.
4. Hub phrase triggers for deep and max.
5. Voice warm process, then flip tier.voice to claude-cli|sonnet|low.
6. Two weeks of usage.jsonl, then the plan decision.

Open questions:

- bypassPermissions on lanes that run unattended: is scoped plan mode, read only until an
  approve tap, wanted for telegram and voice, or is bypass fine because the lanes are
  already trusted? Jorbis runs bypass on a server.
- Model alias for tier.max: pick the day step 2 ships, aliases change fast.
- Whether the hub agent on the Pi gets its own tier mapping for chat or reuses core's.
