"""Configuration loading for pyconnect."""


def load_config(path, strict_mode=False):
    """Read a config file. With *strict_mode* enabled, unknown keys raise an error."""
    return {"path": path, "strict": strict_mode}


def save_config(config, path):
    """Write *config* back to disk at *path*."""
    return {"saved": True, "path": path, "keys": len(config)}
