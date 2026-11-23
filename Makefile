.DEFAULT_GOAL := up
COMPOSE ?= podman compose
# The served model: qwen (config/server.json: the models it lists, each config/{model,server}-<name>.json, the server
# switching between them on request: Qwen3.8-Flash-Next q1 and q3, Inkling 2 Mini) or another
# config/{model,server}-<name>.json pair served alone. STRATA_VARIANT picks the one to start with (default: q1).
STRATA_MODEL ?= qwen
export STRATA_MODEL
STRATA_VARIANT ?=
export STRATA_VARIANT
# The PolyStrata checkout the image is built from (our fork, its inkling-amd branch, as its working tree is:
# uncommitted changes included), and this directory, the image's second build context (the scripts it installs). Both absolute: compose.yaml reads them.
STRATA_SRC ?= ../PolyStrata
override STRATA_SRC := $(abspath $(STRATA_SRC))
STRATA_LOCAL_DIR := $(CURDIR)
export STRATA_SRC STRATA_LOCAL_DIR
# Image build arguments (compose.yaml): the checkout's commit, and the last commit to the engines' sources (the
# Containerfile's engine steps), each with -dirty when the working tree changes them.
ENGINE_SOURCES := engine terminal/tools/hip terminal/tools/vision
git_id = $(shell cd '$(STRATA_SRC)' && echo "$$(git log -1 --format=%h $(1))$$(git status --porcelain $(1) | grep -q . && echo -dirty)")
STRATA_COMMIT ?= $(call git_id,)
STRATA_BUILD_ID ?= $(call git_id,-- $(ENGINE_SOURCES))
export STRATA_COMMIT STRATA_BUILD_ID

.PHONY: set-api-key set-hf-token migrate-keyring-secrets secrets config pull-config build init ports up down logs calibrate tune check\
	quadlet quadlet-uninstall
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
# Copy config/ into the volume mounted at /config; files removed from config/ stay in it. It refuses to overwrite a file
# the web page's Config view changed (pull-config first, or FORCE=1 to discard those changes).
config:
	./scripts/config-volume.sh push
# Copy the run configs the web page's Config view changed back into config/.
pull-config:
	./scripts/config-volume.sh pull
build:
	@test -d '$(STRATA_SRC)/engine' -a -d '$(STRATA_SRC)/terminal' || { \
		echo 'STRATA_SRC=$(STRATA_SRC) is not a PolyStrata checkout (engine/ and terminal/, 0.2.19 or later).' >&2; exit 1; }
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
# Calibrate a hipBLASLt table for this card and the image's hipBLASLt for every model with a shape file
# (scripts/hipblaslt-shapes/<model>.txt; solution ids measured fresh): writes config/<arch>-hipblaslt-<version>-<model>.txt,
# which engine_env.hipblaslt_tuning (STRATA_HIPBLASLT_TUNING) of each model's config/server-<name>.json names (the
# qwen table for q1 and q3). Then make up.
TUNE_MODELS := $(shell sh scripts/tune-hipblaslt.sh list)
tune: build
	$(COMPOSE) stop strata-server
	set -e; for model in $(TUNE_MODELS); do \
		echo "tuning $$model" >&2; \
		$(COMPOSE) run --rm --no-deps -T --entrypoint sh strata-server \
			/usr/local/lib/strata-local/tune-hipblaslt.sh "$$model" > config/.hipblaslt-table.tmp; \
		name=$$(awk -v m="$$model" 'NR == 2 && $$1 == "STRATA_HIPBLASLT_TUNING_V1" {print $$2 "-hipblaslt-" $$3 "-" m ".txt"}' \
			config/.hipblaslt-table.tmp); \
		test -n "$$name"; \
		mv config/.hipblaslt-table.tmp "config/$$name"; \
		echo "wrote config/$$name"; \
	done
# The server as a Podman Quadlet unit of this user's systemd manager instead of a compose container: it starts at boot
# (with lingering) and restarts when it fails. It runs on this machine only, not over CONTAINER_CONNECTION. The compose
# server and the unit's serve the same port and volumes; run one or the other.
QUADLET_DIR ?= $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/containers/systemd
QUADLET_UNIT := strata-local-server
STRATA_BIND_ADDRESS ?= 127.0.0.1
STRATA_PORT ?= 9931
STRATA_GPU ?= 0
define quadlet_render
sed -e 's|@STRATA_BIND_ADDRESS@|$(STRATA_BIND_ADDRESS)|' -e 's|@STRATA_PORT@|$(STRATA_PORT)|' \
	-e 's|@STRATA_MODEL@|$(STRATA_MODEL)|' -e 's|@STRATA_VARIANT@|$(STRATA_VARIANT)|' -e 's|@STRATA_GPU@|$(STRATA_GPU)|' \
	quadlet/$(QUADLET_UNIT).container.in
endef
# Write the unit with this invocation's values and reload systemd; it neither starts nor restarts the server.
quadlet:
	@if [ -n "$${CONTAINER_CONNECTION:-}$${CONTAINER_HOST:-}" ]; then \
		echo 'make quadlet installs on this machine; unset CONTAINER_CONNECTION and CONTAINER_HOST.' >&2; exit 1; fi
	mkdir -p "$(QUADLET_DIR)"
	$(quadlet_render) > "$(QUADLET_DIR)/$(QUADLET_UNIT).container"
	systemctl --user daemon-reload
# Stop the unit's server (if it runs) and remove the unit; the volumes, image and credentials stay.
quadlet-uninstall:
	-systemctl --user stop $(QUADLET_UNIT).service
	rm -f "$(QUADLET_DIR)/$(QUADLET_UNIT).container"
	systemctl --user daemon-reload
check:
	python3 -m unittest discover -s tests -v
	sh -n scripts/credentials.sh scripts/register-podman-secrets.sh scripts/podman-secret-driver.sh scripts/config-volume.sh
	podman-compose -f compose.yaml config >/dev/null
	dir=$$(mktemp -d) && $(quadlet_render) > "$$dir/$(QUADLET_UNIT).container" \
		&& QUADLET_UNIT_DIRS="$$dir" /usr/libexec/podman/quadlet -dryrun -user >/dev/null; \
		status=$$?; rm -f "$$dir/$(QUADLET_UNIT).container"; rmdir "$$dir"; exit $$status
