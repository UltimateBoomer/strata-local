.DEFAULT_GOAL := up
COMPOSE ?= podman compose

.PHONY: set-api-key set-hf-token migrate-keyring-secrets secrets build init ports up down logs calibrate quadlet check
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
build:
	$(COMPOSE) build model-init
init: secrets build
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
# Install systemd units for the current user; start with: systemctl --user start strata-server
quadlet: secrets build
	python3 -I scripts/install-quadlet.py
	systemctl --user daemon-reload
check:
	python3 -m unittest discover -s tests -v
	sh -n scripts/credentials.sh scripts/register-podman-secrets.sh scripts/podman-secret-driver.sh
	podman-compose -f compose.yaml config >/dev/null
	STRATA_BIND_ADDRESS=127.0.0.1 python3 -I scripts/install-quadlet.py --check
