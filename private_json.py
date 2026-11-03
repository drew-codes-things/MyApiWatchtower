import json
import os


def read_private_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def write_private_json(path, data):
    temporary_path = path + ".tmp"
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, sort_keys=True)
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, path)
