"""Today: one row of what matters now, from the other modules, for the main Diablo section."""

ID = "today"
TITLE = "Today"
TOOLS = []


def panel(section):
    from . import lights, mail, reminders, workout

    items = []
    for module in (workout, lights, reminders, mail):
        try:
            tile = module.summary()
        except Exception:
            tile = None
        if tile:
            items.append(dict(tile, open=module.ID))
    return [{"kind": "stats", "items": items}] if items else []
