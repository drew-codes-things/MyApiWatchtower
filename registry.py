import importlib
import os
import sys

from checks.http_service import run_service
from services_store import load_services

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")
SECTION_TITLES = {"api": "Live API checks", "scraper": "Website scraper checks", "service": "Your services"}
_plugins = None


class PluginError(RuntimeError):
    pass


def load_plugins(plugins_dir=None):
    global _plugins
    if _plugins is not None and plugins_dir is None:
        return _plugins
    directory = plugins_dir or PLUGINS_DIR
    loaded = []
    if os.path.isdir(directory):
        if directory not in sys.path:
            sys.path.insert(0, directory)
        for file_name in sorted(os.listdir(directory)):
            if not file_name.endswith(".py") or file_name.startswith("_"):
                continue
            try:
                loaded.append(importlib.import_module(file_name[:-3]))
            except Exception as error:
                raise PluginError(f"plugin {file_name} failed to load: {error}") from error
    _plugins = loaded
    return loaded


def plugin_checks():
    return [check for plugin in load_plugins() for check in getattr(plugin, "CHECKS", [])]


def service_check(service):
    return {
        "id": service["id"],
        "label": service["name"],
        "projects": service.get("tags", []),
        "kind": "service",
        "run": lambda: run_service(service),
        "service": service,
    }


def all_checks():
    checks = plugin_checks() + [service_check(service) for service in load_services()]
    seen = set()
    for check in checks:
        if check["id"] in seen:
            raise PluginError(f"two checks share the id {check['id']!r}")
        seen.add(check["id"])
    return checks


def credentials_for(check):
    return check.get("credentials", [])


def get_check(check_id):
    return next((check for check in all_checks() if check["id"] == check_id), None)


def section_title(kind):
    return SECTION_TITLES.get(kind, kind.replace("_", " ").capitalize())


def plugin_notes():
    return [note for plugin in load_plugins() if hasattr(plugin, "notes") for note in plugin.notes()]


def setup_plugins(app):
    for plugin in load_plugins():
        if hasattr(plugin, "setup"):
            plugin.setup(app)
