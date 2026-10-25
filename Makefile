.DEFAULT_GOAL := up
COMPOSE ?= podman compose
# The served model: qwen (config/server.json: the Qwen variants it lists, each config/{model,server}-<variant>.json) or
# another config/{model,server}-<name>.json pair. STRATA_VARIANT picks the Qwen variant to start with (default_model).
STRATA_MODEL ?= qwen
export STRATA_MODEL
STRATA_VARIANT ?=
export STRATA_VARIANT

.PHONY: set-api-key set-hf-token migrate-keyring-secrets secrets config build init ports up down logs calibrate tune check
set-api-key:
	./scripts/credentials.sh store strata_api_key
set-hf-token:
	./scripts/credentials.sh store hf_token
# One-time copy of values stored by earlier versions in the GNOME keyring (it must be unlocked).
migrate-keyring-secrets:
	./scripts/credentials.sh migrate-keyring strata_api_key
	./scripts/credentials.sh migrate-keyring hf_token
secrets:
	./scripts/register-podman-secrets.sh
# Copy config/ into the volume mounted at /config; files removed from config/ stay in it.
config:
	podman volume create --ignore strata-local-config >/dev/null
	tar -C config -c --owner=0 --group=0 . | podman volume import strata-local-config -
build:
	$(COMPOSE) build model-init
init: secrets config build
	$(COMPOSE) run --rm --no-deps -T model-init
up: init
	$(COMPOSE) up -d --no-deps --force-recreate strata-server
down:
	$(COMPOSE) down
logs:
	$(COMPOSE) logs -f strata-server
calibrate:
	$(COMPOSE) stop strata-server
	$(COMPOSE) run --rm --no-deps -T --entrypoint python strata-server /usr/local/lib/strata-local/calibrate.py
# Calibrate a hipBLASLt table for this card and the image's hipBLASLt, over the 43 rows of upstream's gfx1100 1.4.1
# table (solution ids measured fresh); writes config/<arch>-hipblaslt-<version>.txt. Point
# STRATA_HIPBLASLT_TUNING in config/server.json at it, then make up.
tune: build
	$(COMPOSE) stop strata-server
	$(COMPOSE) run --rm --no-deps -T --entrypoint sh strata-server /usr/local/lib/strata-local/tune-hipblaslt.sh \
		> config/.hipblaslt-table.tmp
	name=$$(awk 'NR == 2 && $$1 == "STRATA_HIPBLASLT_TUNING_V1" {print $$2 "-hipblaslt-" $$3 ".txt"}' config/.hipblaslt-table.tmp) \
		&& test -n "$$name" && mv config/.hipblaslt-table.tmp "config/$$name" && echo "wrote config/$$name"
check:
	python3 -m unittest discover -s tests -v
	sh -n scripts/credentials.sh scripts/register-podman-secrets.sh scripts/podman-secret-driver.sh
	podman-compose -f compose.yaml config >/dev/null
