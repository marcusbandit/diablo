# App map

Every screen in Diablo's app (phone and browser, one Expo codebase in `app/`) and what it is for.
A new feature gets a place here in the same PR that builds it. Adding a room is one line. The
map describes the app; it never blocks an idea, and when it gets crowded we sort it on purpose.

The approved pictures live in `docs/app/design/`. Plan and history: #91.

## The three jobs

The app exists to do three things, in this order.

1. **Needs you**: questions, approvals and decisions Diablo is waiting on.
2. **Talk**: one conversation with him.
3. **Work**: what he is doing now and what he finished.

Everything else is a room.

## Screens

| Screen | For | Reached from |
|---|---|---|
| Den | Home. His mood block, what needs you, running work, the rooms, the input. | App start, back from anywhere |
| Talk | The conversation. Asks appear inline. | Typing in the input, the den |
| Needs you | Every open ask, answered in place; a Done tab for history. | The den headline |
| One ask | A question with its options and his pick, or an approval with Always. | Needs you, a notification |
| Memory | What he knows: search, grouped, change or forget a memory. Reshaped by Memory V3. | Tapping his face |
| Settings | Hubs, devices, notifications, about. | The den |
| Onboarding | First launch: what he does for you, then pairing. Every step has Later. | First launch, Settings |

## Rooms

A room exists once it is used. Each one is a page with his mood block on top, holding the room's
name, and the one number that matters as the biggest thing on it.

| Room | For | Hub module |
|---|---|---|
| Workout | Push-ups today, the week, logging a set. | `workout` |
| Lights | The lamps and their colours. | `lights` |
| Mail | Mail he sorted and what he wants to send. | `mail` |
| Reminders | What he will remind you of, and when. | `reminders` |

Waiting for a place once built: hand work to Diablo (#82), the fleet room (#84), widget, share
target and quick tile (#86).

## The mood block

The block on top of every screen holds his face and one big line. It is calm slate while he works
and turns orange only when something needs you, so its colour always means attention. The change
spreads out from his face like ink. What he is doing shows in his face and a thin sweeping line
under the block, in the orb's state colours: blue listening, orange thinking, green speaking,
pink trouble.

## Postures

- **Phone**: one screen at a time; the den scrolls and the input stays at the bottom.
- **Unfolded** (close to square): look left, talk right. The left shows whatever is open (the den,
  a room, Needs you, memory), the right is always Talk, and he can point at what is on the left.
  The split sits on the hinge.
- **Book fold**: the same as unfolded, nothing on the seam.
- **Tabletop fold**: the conversation above the hinge, the asks and the input below it.
- **Browser**: the unfolded layout, wider.

Back always steps back: something open closes first, a page returns to the one it came from, and
only the den with nothing open leaves the app.
