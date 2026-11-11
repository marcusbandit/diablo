# Mail

The hub reads every new mail in Marcus's two inboxes, sorts it, and answers allowlisted agents on its
own. Code: `hub/mailbox.py` (the handler), `hub/modules/mail.py` (panel, taps, brain tools). Issue #47.

## What happens to a mail

1. Every minute the hub lists both accounts through himalaya (`mail.accounts`, from
   `DIABLO_MAIL_ACCOUNTS` in `~/.config/diablo/local.env`) and reads each new mail with `--preview`, so it stays unread
   in Gmail. The first poll after a fresh state only notes where the inbox stands and reads nothing.
2. Trust comes first, from headers, never from what the mail says. A mail counts as verified only when
   the topmost `Authentication-Results`, written by `mx.google.com`, has `dmarc=pass` for the From
   address's own domain. Then:
   - `agent`: verified, from an allowlisted agent address (`mail.autoreply`, `DIABLO_MAIL_AGENT` in local.env),
     and signed by that agent (`Jorbis` on one of the last lines before any quote). The only mail
     Diablo acts on.
   - `priority`: verified, from a priority address (`mail.priority`) but not signed by the agent, like
     Albert writing himself. Sorted by the smarter model, never acted on.
   - `plain`: everything else, including any mail that claims to be Jorbis from another address or fails
     DMARC. Sorted by Haiku and nothing more.
3. One call to the logged-in `claude` CLI sorts it: a category, `important`, a one line summary. No
   tools, no MCP, no session kept, structured output. `agent` and `priority` mail gets Sonnet on low,
   everyone else Haiku on low. No API key anywhere: it is the same subscription login as the brain.
4. Important mail goes to Telegram as one line plus a link that opens it in Gmail
   (`#search/rfc822msgid:`). Nothing else pings. Verification codes stay with mail-code-monitor.
5. `agent` mail to the `gmail` account follows the
   protocol in the vault note jorbis-mail-protocol:
   - an acknowledgement gets no answer and no ping
   - a question, information or a `[For Marcus]` message gets an answer Diablo writes himself (Sonnet,
     medium), checked first by a quick self check; a reply that fails the check waits for Marcus
   - a request to do something becomes an approval
   - every automatic mail, acknowledgements included, counts against `mail.max_replies` (3) per sender in
     30 minutes. Past that a courtesy note is dropped and a real answer waits for Marcus with a ping, so
     two agents can never loop

A mail that fails to sort is tried again on the next polls; after 3 failures Marcus gets one ping
naming it. The envelope list is sorted by date, not uid, so a poll pages on until a page holds nothing
new, which also covers a long downtime.

## Approvals

A request pings Marcus with Approve and Deny buttons, shows in the Mail panel with the same two taps,
and Jorbis hears it was passed on. Approved, it runs as a brain turn in its own conversation
`mail-task-<id>`, with the house tools, inside the approved scope. Every `mail.check_seconds` (120) of
work the self check looks at the steps so far, without holding up the turn; a thumbs down stops the
turn and asks Marcus whether to let it continue. Let it continue resumes the same conversation with the
steps so far and does not count as another approval. A task still running when the hub stops is marked
paused on the next start and pinged the same way. A self check that errors is skipped, never fatal. The final answer passes the same check and is mailed back in the thread.

Every approval is logged in `approvals`. Approving the same `task_kind` from the same sender
`mail.always_after` (3) times offers an Always button; an always rule approves that kind without a
ping from then on, and each run is still logged. Keep asking takes the rule away again.

Only Marcus decides: Telegram buttons and app taps. The brain has `mail_recent` and `mail_approvals` to
read, and no tool that approves, so text in a mail can never talk a brain turn into an approval. App
taps run on a worker thread, so they land in the `decisions` queue and the mail loop carries them out
within seconds. Telegram buttons run in the hub loop directly.

## Writing to Jorbis

The brain's `mail_agent` tool writes a new mail to an allowlisted agent: "send to Albert" from
Marcus, or a scheduled task. It takes no other address. The subject gets `[Agent]` unless it starts
with `[Agent]` or `[For Albert]`. It goes through the same self check and the same cap of three mails
in half an hour as automatic answers.

## Limit warnings

The hub keeps the subscription's usage windows from every `rate_limit_event` the claude CLI emits
(hub setting `limits.claude`). When a window passes `mail.limit_warn` (0.9) or stops being
`allowed`, and Diablo and Jorbis wrote to each other within the last hour, Diablo mails Jorbis once
for that window: how full it is and when it resets. A new window warns again.

## Settings

Hub settings, defaults in `mailbox.DEFAULTS`: `mail.enabled`, `mail.accounts`, `mail.reply_from`,
`mail.priority`, `mail.autoreply`, `mail.names`, `mail.model.*` and `mail.effort.*` (`default`,
`priority`, `reply`), `mail.poll_seconds`, `mail.always_after`, `mail.check_seconds`, `mail.max_replies`, `mail.limit_warn`, `mail.hints`
(Marcus's own importance hints, folded in from the old mail-watch rules).

State: `~/.local/state/diablo/mail.db` on the Pi (`DIABLO_MAIL_DB` overrides). The standby never runs
the handler. `DIABLO_MAIL=0` keeps it off in a hub process.
