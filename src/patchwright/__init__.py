from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("patchwright")
except PackageNotFoundError:  # pragma: no cover - not installed (e.g. raw source tree)
    __version__ = "0.0.0"
