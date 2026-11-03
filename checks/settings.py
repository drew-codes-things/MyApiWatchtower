import os


def configured_url(name, default):
    return (os.environ.get(name, "").strip() or default).rstrip("/")
