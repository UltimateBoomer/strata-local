"""The run configs PolyStrata's server starts with, made from config/ at each start.

config/server.json holds what every Qwen3.8-Flash-Next model shares and lists the models ("models": name -> file).
A model's file adds its own keys over those (its "engine", "engine_env" and "sampling" key by key; any other key
replaces the shared one), unless it names its own "exe": then it is another engine (Inkling 2 Mini on strata-poly) and
takes only the server's own keys from server.json (SERVER_KEYS). Each model becomes one PolyStrata run config,
<out>/strata-<name>.json, with "engine" and "engine_env" written out as "args" and "env" (engine_config.py), because
PolyStrata's server lists the models beside the config it starts with (its serve/models.py) and, with
"switch_on_request", loads the one a request names.

STRATA_MODEL=<name> other than qwen serves config/server-<name>.json alone, as before.
"""
from __future__ import annotations

import json
from pathlib import Path

import engine_config

# keys of server.json that belong to the server, not to an engine: every model takes them
SERVER_KEYS = frozenset({"switch_on_request", "backend", "cwd", "unknown_model"})
# keys that are strata-local's own: read here, never written into a run config
OWN_KEYS = frozenset({"models", "default"})
# keys merged key by key between server.json and a model's file
MERGED_KEYS = frozenset({"engine", "engine_env", "sampling", "env"})


def _read(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: not a JSON object")
    return cfg


def merge(common: dict, own: dict) -> dict:
    """A model's config: `own` over `common` (MERGED_KEYS key by key), or over the server's keys alone when `own` has an
    "exe" of its own."""
    if "exe" in own:
        base = {k: v for k, v in common.items() if k in SERVER_KEYS}
    else:
        base = {k: v for k, v in common.items() if k not in OWN_KEYS}
    out = dict(base)
    for key, value in own.items():
        if key in MERGED_KEYS and isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def build(config_dir: Path, model: str = "qwen") -> tuple[dict[str, dict], str]:
    """(name -> run config, the name of the one to start with by default) for STRATA_MODEL `model`."""
    config_dir = Path(config_dir)
    if model != "qwen":
        cfg = _read(config_dir / f"server-{model}.json")
        return {model: engine_config.expand(cfg, f"server-{model}.json")}, model
    common = _read(config_dir / "server.json")
    models = common.get("models")
    if not isinstance(models, dict) or not models:
        raise ValueError('server.json: "models" must name at least one model')
    out = {}
    for name, file in models.items():
        own = _read(config_dir / file)
        out[name] = engine_config.expand(merge(common, own), file)
    first = common.get("default") or next(iter(models))
    if first not in out:
        raise ValueError(f'server.json: "default" names {first!r}, which "models" does not list')
    return out, first


def write(configs: dict[str, dict], out_dir: Path) -> dict[str, Path]:
    """Write each run config as <out_dir>/strata-<name>.json (replacing what was there); name -> path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("strata-*.json"):
        old.unlink()
    paths = {}
    for name, cfg in configs.items():
        path = out_dir / f"strata-{name}.json"
        path.write_text(json.dumps(cfg, indent=2) + "\n")
        paths[name] = path
    return paths
