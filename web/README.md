# Web app

Diablo in a browser: every section, its chat and its panel, and Devices for pairing. React,
Vite and TypeScript. The hub serves the build at `/app` (`hub/server.py` mounts `$DIABLO_WEB`, or
`web/` next to the hub database). Every hub call goes through `@diablo/hub` in `packages/hub`,
which the phone app shares.

`public/` is copied into the build as it is: `pair.html` (the page a pairing QR code opens),
`qrcode.js`, `devicename.js`, the icons, `tokens.css` and `fonts/`. Colours and sizes come from
`design/tokens.json` at build time.

## Develop

From the repo root, once:

    bun install

Run a hub to talk to. `dev/fakehub.py` is the real hub with a canned brain, canned panels and
invented data in a temporary directory, so nothing reaches a model, a lamp or Telegram:

    uv run --no-project --with-requirements hub/requirements.txt web/dev/fakehub.py --port 8799

Then the dev server, which sends `/v1` to that hub:

    cd web
    DIABLO_DEV_HUB=http://127.0.0.1:8799 bun run dev

Open `http://<this box>:5173/app/?token=$FAKE_TOKEN` (the `TOKEN` constant in `web/dev/fakehub.py`), or leave the
token off to go through onboarding and pair with a code from
`curl -X POST -H "X-Diablo-Token: $FAKE_TOKEN" http://127.0.0.1:8799/v1/pair/code`.
In the fake hub, a message with "slow" in it takes long enough to try Stop.

## Check

    cd web
    bun run typecheck
    bun run test        # pure logic in test/, with bun test
    bun run build       # typecheck, then web/dist

The fake hub serves `web/dist` at `/app` too, so a build can be checked the way the Pi serves it:
`http://127.0.0.1:8799/app/`.

## Ship

`deploy/ship-hub.sh` builds the app (`bun install --frozen-lockfile`, `bun run --filter web build`)
and sends `web/dist/` to the Pi as its `web/`, next to `hub/`. It refuses to run with uncommitted
changes in anything the build reads. `deploy/ship-hub.sh --dry-run` builds and lists what would
change.

## Where things are

| Path | What |
| --- | --- |
| `src/lib/` | token bootstrap, the hub context and event stream, routes, message merging |
| `src/views/` | onboarding, the shell and sidebar, a section's chat, Devices |
| `src/blocks/` | one renderer per block kind; `index.tsx` is the registry, an unknown kind is skipped |
| `src/ui/` | Markdown, icons, the wordmark |
| `dev/fakehub.py` | the hub for trying the app |
