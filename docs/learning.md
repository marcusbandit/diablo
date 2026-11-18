# Learning

Diablo gets better with use without slowing a reply. Everything here runs after a reply has
gone out, as cheap side calls (Haiku through the claude CLI, `threads.Quick`, no tools), and
waits while any turn is running. The code is hub/learn.py and hub/skillbook.py.

## Skills he writes himself

A learned skill is a folder in the brain home's `.claude/skills/<slug>/` with a `SKILL.md`
(frontmatter `name` and `description`, then the steps) and a `.learned.json` marker. Claude Code
loads it like the repo's own skills from hub/skills.

MCP tools on the diablo server:

- `save_skill(name, description, steps)` writes or updates one. Marcus can say "remember how to
  do this" and he saves the steps that worked.
- `forget_skill(name)` deletes a learned skill.
- `list_skills(name?)` lists the shipped and learned skills, or shows one learned skill in full.

Every write goes through the same checks: the slug comes from the name (lowercase ASCII, digits
and hyphens, at most 48 characters), the file is at most 8 KB, nothing that looks like a secret
(API keys, tokens, passwords, private keys, card numbers by Luhn, personal id numbers;
hub/sensitive.py), and nothing that tries to override the charter or switch off a safety rule
(a short deny list: "ignore your previous instructions", turning off the lock guard or approvals,
`--no-verify`, the `lock.enabled` setting, never telling Marcus). The skills that ship in
hub/skills, and any folder without the marker, can neither be saved over nor forgotten.

The start-up copy (`brains.install_skills`) only replaces the folders the repo ships and removes
the ones it stopped shipping. It never touches a folder with the marker; if the repo ever ships
a skill with the same name as a learned one, the learned one is renamed `<name>-learned` first.

## Learning a skill after a turn that worked

After a claude turn with at least five tool calls, a reply, and no error, the hub queues a
learning pass:

1. It waits for Marcus's next message in that conversation, or three quiet minutes
   (`learn.skills.quiet_s`, 180), whichever comes first. A message he sent after the turn
   started, while it still ran, is that next message already. Lines from watchers and other
   machine turns do not count.
2. A next message that reads like a correction ("no", "that's wrong", "not what I asked",
   "undo that", "nej", "forkert") cancels it, and so does a correction among any he sent while
   the turn ran.
3. It waits until no turn is running, then counts the turn's tool results in the claude
   session's transcript. Any failed tool call, or a transcript it cannot read, means nothing is
   learned.
4. One side call gets his request, the tool steps (names and short arguments, never outputs)
   and the index of learned skills, and answers `{"skip":true}`, a new skill, or
   `{"update": "<learned name>", ...}` with a better version of one. Anything that is not that
   JSON counts as skip.
5. The skill is saved through the same checks as `save_skill`.

`learn.skills` (default on) turns it off.

## Facts kept after a conversation

Facts used to be stored only when the brain called `save_fact`. Now a memory pass also reads the
main conversation once it has been quiet for `learn.facts.quiet_s` (900 s, the idle reaper's 15
minutes), or as soon as `learn.facts.batch` (30) new messages piled up. The idle reaper checks
every minute and never starts it while a turn runs.

The side call gets the messages since the last pass (at most 60) and the stored facts that share
words with them, with their ids, and answers `{"facts": [{"text", "tags", "replaces"}]}`. A fact
with `replaces` updates that fact in place; one with the same text as a stored fact is dropped;
anything with a secret, a card number or fewer than 12 characters is dropped. At most eight per
pass, each tagged `learned`. `learn.facts.upto` holds the id of the last message read, so nothing
is read twice. On the first run it starts at the newest message, so the old history is not mined.
A failing call (claude missing, a timeout) leaves the marker where it was and retries after ten
minutes; an answer that is not JSON counts as nothing to keep and moves on.

`learn.facts` (default on) turns it off.

## Facts in every turn

Before, facts only reached a turn when the thread picker said the line needed them, or through
the `recall_facts` tool. Now every claude or opencode turn can carry a facts block in its bracket
context (never in the charter, which stays byte-stable for the prompt cache): the 8 newest facts
and up to 20 that share words with the new line. The hub remembers, per conversation lane and
claude session, which facts that session has already been given, and sends only new or changed
ones. A warm session in the same lane usually gets no block at all; a fresh or seeded session
gets the whole block again.

## Events

Each learned skill or fact is logged and published on `/v1/events` as
`{"kind": "learned", "what": "skill" | "fact", "change": "created" | "updated", "label"}`, plus
`id` for a fact. It is quiet: no Telegram push and no message in the chat.
