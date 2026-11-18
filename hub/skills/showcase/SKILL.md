---
name: showcase
description: Load this when Marcus asks you to show him what you can do, to demo yourself or give him a tour, or says yes after you offered to show him. Also when he only asks what you can do, so you answer in a line and offer the tour. Runs a short live tour of your abilities on his real desktop, pointing at his own screen, without touching anything.
---

# Showing what you can do

## He only asks

"What can you do?", "what are you capable of": answer in one spoken line with three or four of
your best tricks, then ask whether to show him ("Shall I show you, sir?") as your last sentence,
in plain words: never `ask_user`, never numbered choices. Nothing on screen yet. He answers by
voice right after, without your name.
A yes ("yes", "go on", "show me", "sure") starts the tour below.

## The tour

He asked to see it, so show, do not tell. About thirty seconds, five beats, every line short.
Spoken at his desk it runs on his real screen; from Telegram or the app, see the last section.

1. Look first, in one turn: `desktop_front`, and `read_window` on the front window with `max` 40.
   From the controls pick one real, visible thing with a clear name to point at: a tab, a
   button, a menu word. Skip anything risky (delete, send, pay, sign out).
2. One `show_on_screen` card, size small, title "What I can do", lines:
   - "Talk to me by name, from across the room"
   - "Point at anything on your screen"
   - "Click and type through a task for you"
   - "Lights, reminders, mail and your phone"
   - "Hand coding to T3 and watch it for you"
3. One `point_on_screen` call with three beats:
   - a ring and an arrow on the thing you picked, say "I can point at anything you are looking at."
   - a label on it with its name, say "I find things by the words on them, no guessing."
   - a check mark on it, say "And I can walk you through a task, step by step."
4. A picture: read `example.html` from the explain-3d skill folder and show it as is with
   `show_on_screen`, size large, title "Explained in 3D". Say "Ask how something works and I
   draw it for you."
5. Close in one line: "That is the short tour, sir. Ask for any of it."

Write the spoken lines in that order, one short sentence each, so the beats and your voice
line up. Use no other tools.

## Never during the tour

- No clicks, no typing, no keys: `act_on_desktop` is not part of it.
- No messages sent, no lights changed, no reminders made, no T3 threads started.
- No screenshots: the window's controls are enough to point.

If `point_on_screen` finds nothing, or the screen is locked, skip the pointing beats and say
so in a few words. Never stop the tour for one missing beat.

## From Telegram or the app

You cannot point at his screen from there. Answer with the same five lines as a short list,
and offer to show him live: "Say my name at your desk and ask me to show you."
