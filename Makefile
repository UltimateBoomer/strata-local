.DEFAULT_GOAL := up
COMPOSE ?= podman compose

.PHONY: set-api-key set-hf-token migrate-keyring-secrets secrets config build init ports up down logs calibrate check
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
	$(COMPOSE) run --rm --no-deps -T --entrypoint python strata-server /opt/strata/tools/calibrate.py /config/server.json
check:
	python3 -m unittest discover -s tests -v
	sh -n scripts/credentials.sh scripts/register-podman-secrets.sh scripts/podman-secret-driver.sh
	podman-compose -f compose.yaml config >/dev/null
