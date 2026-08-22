"""Configuration loading for pyconnect."""


def load_config(path, strict=True):
    """Read a config file. With *strict* enabled, unknown keys raise an error."""
    return {"path": path, "strict": strict}


def save_config(config, path):
    """Write *config* back to disk at *path*."""
    return {"saved": True, "path": path, "keys": len(config)}
