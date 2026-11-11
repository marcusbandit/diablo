"""Feature modules. Each one gives the brain tools and the apps a live panel.

A module is a Python module in this package with:

- ID: the name sections list in their panels
- TITLE: shown above its panel
- TOOLS: MCP tool schemas (name, description, inputSchema), may be empty
- call(name, arguments) -> str: runs one of its tools
- panel(section) -> list of objects: what the app shows, built from the block kinds in docs/api.md
- WRITES: the names of its tools that change something, so panels refresh after them
- CACHES: the Cache objects that Try again empties, so the next panel reads its source again
- act(action, args) -> str: a tap in the app, like a lamp switch; raises ActFailed when it did nothing

Register a new one by adding it to ORDER.
"""

import logging
import time

from .. import events
from . import jorbis, lights, machines, mail, network, reminders, today, usage, workout
from .common import ActFailed, notice, unavailable

log = logging.getLogger(__name__)

ORDER = (today, workout, lights, reminders, mail, jorbis, network, machines, usage)
BY_ID = {module.ID: module for module in ORDER}


class ModuleError(LookupError):
    pass


def tools():
    listed = []
    for module in ORDER:
        listed.extend(getattr(module, "TOOLS", ()))
    return listed


def owner(tool_name):
    short = tool_name.rsplit("__", 1)[-1]
    for module in ORDER:
        if any(tool["name"] == short for tool in getattr(module, "TOOLS", ())):
            return module
    return None


def call(name, arguments):
    module = owner(name)
    if module is None:
        raise ModuleError(name)
    short = name.rsplit("__", 1)[-1]
    result = module.call(short, arguments or {})
    if short in getattr(module, "WRITES", ()):
        events.publish({"kind": "panel", "modules": [module.ID, "today"]})
    return result


def retry(module):
    """Forgets what the module cached, so the next panel reads its source again."""
    for holder in getattr(module, "CACHES", ()):
        holder.clear()
    return "Reading it again."


def panel(module_id, section):
    module = BY_ID.get(module_id)
    if module is None:
        return []
    try:
        blocks = module.panel(section)
    except Exception as error:
        log.exception("panel %s failed", module_id)
        return [dict(unavailable(f"{module.TITLE} could not load just now. Asking again usually works."), module=module_id)]
    return [dict(block, module=module_id) for block in blocks]


def section_panel(section):
    blocks = []
    for module_id in section.get("panels") or []:
        blocks.extend(panel(module_id, section))
    return {"section": section["id"], "objects": blocks, "generated": time.time()}


def act(module_id, action, args):
    module = BY_ID.get(module_id)
    if module is not None and action == "retry":
        return retry(module)
    if module is None or not hasattr(module, "act"):
        raise ModuleError(module_id)
    return module.act(str(action or ""), args or {})


def writer(tool_name):
    module = owner(tool_name)
    if module is None or tool_name.rsplit("__", 1)[-1] not in getattr(module, "WRITES", ()):
        return None
    return module.ID
