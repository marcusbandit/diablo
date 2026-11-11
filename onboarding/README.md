# Diablo onboarding

Run from the project root with the voice venv:

```
.venv-voice/bin/python -m onboarding.onboard
```

Collects keys, writes ~/.config/diablo/config.toml, deploys the hub to homehub,
installs piper, and enables the user services: diablo-voice, diablo-pool (Raphael
and the workers), diablo-fast (the fast tongue panel) and diablo-stagehand.
Safe to rerun, existing values are the defaults. `--skip-hub` skips the homehub
deploy.

After onboarding, where to talk to him: out loud with his name, in a terminal
with `diablo agent` (a hands-on agent session in whatever repo you are in) or
`diablo talk` (the fast tongue), or on telegram.
