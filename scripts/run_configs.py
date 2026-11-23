"""The run configs PolyStrata's server starts with, made from config/ at each start.

config/server.json holds only what is not any model's: the server's keys (SERVER_KEYS) and the models ("models":
name -> file). Each model's file is the whole of that model's config (its engine, options, sampling, drafter,
pictures) and takes the server's keys from server.json. Each model becomes one PolyStrata run config,
<out>/strata-<name>.json, with "engine" and "engine_env" written out as "args" and "env" (engine_config.py), because
PolyStrata's server lists the models beside the config it starts with (its serve/models.py) and, with
"switch_on_request", loads the one a request names.

STRATA_MODEL=<name> other than qwen serves config/server-<name>.json alone, as before.
"""
from __future__ import annotations

import json
from pathlib import Path

import engine_config

# keys of server.json that belong to the server, not to a model: every model takes them
SERVER_KEYS = frozenset({"switch_on_request", "backend", "cwd", "unknown_model"})
# keys that are strata-local's own: read here, never written into a run config
OWN_KEYS = frozenset({"models", "default"})


def _read(path: Path) -> dict:
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict):
        raise ValueError(f"{path}: not a JSON object")
    return cfg


def merge(common: dict, own: dict) -> dict:
    """A model's config: the server's keys of `common` (server.json), with `own` (the model's file) over them."""
    return {**{k: v for k, v in common.items() if k in SERVER_KEYS}, **own}


def build(config_dir: Path, model: str = "qwen") -> tuple[dict[str, dict], str]:
    """(name -> run config, the name of the one to start with by default) for STRATA_MODEL `model`."""
    config_dir = Path(config_dir)
    common = _read(config_dir / "server.json")
    other = set(common) - SERVER_KEYS - OWN_KEYS
    if other:
        raise ValueError(f"server.json: {', '.join(sorted(other))} belong in the models' files (it holds only "
                         f"{', '.join(sorted(SERVER_KEYS | OWN_KEYS))})")
    if model != "qwen":
        cfg = merge(common, _read(config_dir / f"server-{model}.json"))
        return {model: engine_config.expand(cfg, f"server-{model}.json")}, model
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
