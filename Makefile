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
# uncommitted changes included), and this directory, the image's second build context (the scripts it installs).
STRATA_SRC ?= ../PolyStrata
override STRATA_SRC := $(abspath $(STRATA_SRC))
# The branch make build requires STRATA_SRC to have checked out; empty for any.
STRATA_BRANCH ?= inkling-amd
# Image build arguments: the checkout's commit, and the last commit to the engines' sources (the Containerfile's engine
# steps), each with -dirty when the working tree changes them; the GPU architectures and compile jobs.
ENGINE_SOURCES := engine terminal/tools/hip terminal/tools/vision
git_id = $(shell cd '$(STRATA_SRC)' && echo "$$(git log -1 --format=%h $(1))$$(git status --porcelain $(1) | grep -q . && echo -dirty)")
STRATA_COMMIT ?= $(call git_id,)
STRATA_BUILD_ID ?= $(call git_id,-- $(ENGINE_SOURCES))
HIP_ARCHITECTURES ?= gfx1101
BUILD_JOBS ?= 8
IMAGE := localhost/strata-local:hip

# The deploy host, which runs the server: DEPLOY_HOST, DEPLOY_CONNECTION and DEPLOY_ADDRESS, from .env (mise.toml loads
# it; .env.example). On any other machine every target works there through its Podman socket (the connection
# DEPLOY_CONNECTION; README: Develop here, deploy on a remote host), keeping nothing of the checkouts there: builds
# upload the working trees, make config imports config/, the secret driver lives in a volume. Only the Quadlet unit,
# which is systemd's, takes SSH to DEPLOY_HOST (scripts/on-host.sh): writing and removing its file and systemctl --user.
# On the host itself the targets run directly, as they do with DEPLOY_HOST unset. An explicit CONTAINER_CONNECTION or
# CONTAINER_HOST works through that Podman alone (README: Remote host). On the host the server listens on
# DEPLOY_ADDRESS by default, so the other machines reach it.
DEPLOY_HOST ?=
DEPLOY_CONNECTION := $(or $(DEPLOY_CONNECTION),$(DEPLOY_HOST))
DEPLOY_ADDRESS := $(or $(DEPLOY_ADDRESS),127.0.0.1)
DEPLOY_HERE := $(if $(DEPLOY_HOST),$(filter $(DEPLOY_HOST),$(shell hostname) $(shell hostname -s)))
ifneq ($(and $(DEPLOY_HOST),$(if $(DEPLOY_HERE),,y),$(if $(CONTAINER_CONNECTION)$(CONTAINER_HOST),,y)),)
DEPLOY_SSH := $(DEPLOY_HOST)
CONTAINER_CONNECTION := $(DEPLOY_CONNECTION)
export CONTAINER_CONNECTION
endif
export DEPLOY_SSH
# The server's address and port (compose.yaml and the Quadlet unit) and the GPU it uses.
STRATA_BIND_ADDRESS ?= $(if $(DEPLOY_HERE)$(DEPLOY_SSH),$(DEPLOY_ADDRESS),127.0.0.1)
STRATA_PORT ?= 9931
STRATA_GPU ?= 0
export STRATA_BIND_ADDRESS STRATA_PORT STRATA_GPU

# The server as a Podman Quadlet unit of the host's systemd user manager (it starts at boot with lingering and restarts
# when it fails), or as the compose server: STRATA_SERVER, quadlet or compose. Deploying from another machine, quadlet;
# on the host, quadlet when make quadlet has installed the unit. up, sync, down, logs and stop-server act on it. Both
# serve the same port and volumes.
QUADLET_DIR ?= $(if $(DEPLOY_SSH),.config,$(or $(XDG_CONFIG_HOME),$(HOME)/.config))/containers/systemd
QUADLET_UNIT := strata-local-server
STRATA_SERVER ?= $(if $(DEPLOY_SSH)$(wildcard $(QUADLET_DIR)/$(QUADLET_UNIT).container),quadlet,compose)
ON_HOST := ./scripts/on-host.sh
unit = $(ON_HOST) 'systemctl --user $(1) $(QUADLET_UNIT).service'
define quadlet_render
sed -e 's|@STRATA_BIND_ADDRESS@|$(STRATA_BIND_ADDRESS)|' -e 's|@STRATA_PORT@|$(STRATA_PORT)|' \
	-e 's|@STRATA_MODEL@|$(STRATA_MODEL)|' -e 's|@STRATA_VARIANT@|$(STRATA_VARIANT)|' -e 's|@STRATA_GPU@|$(STRATA_GPU)|' \
	quadlet/$(QUADLET_UNIT).container.in
endef

.PHONY: set-api-key set-hf-token migrate-keyring-secrets secrets config pull-config build init up sync down stop-server\
	logs status calibrate tune quadlet quadlet-uninstall check
.NOTPARALLEL:

# The credentials live on the Podman host, encrypted to it: the value goes there as a Podman secret, which the secret
# driver's store encrypts (scripts/register-podman-secrets.sh).
set-api-key:
	./scripts/register-podman-secrets.sh set strata_api_key
set-hf-token:
	./scripts/register-podman-secrets.sh set hf_token
# One-time copy of values stored by earlier versions in this machine's GNOME keyring (it must be unlocked).
migrate-keyring-secrets:
	@test -z '$(CONTAINER_CONNECTION)$(CONTAINER_HOST)' || { echo 'migrate-keyring-secrets runs on the host itself.' >&2; exit 1; }
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
# Build the image from STRATA_SRC's working tree, sent without the files Containerfile.containerignore names.
build:
	@test -d '$(STRATA_SRC)/engine' -a -d '$(STRATA_SRC)/terminal' || { \
		echo 'STRATA_SRC=$(STRATA_SRC) is not a PolyStrata checkout (engine/ and terminal/, 0.2.19 or later).' >&2; exit 1; }
	@branch=$$(git -C '$(STRATA_SRC)' branch --show-current); test -z '$(STRATA_BRANCH)' -o "$$branch" = '$(STRATA_BRANCH)' || { \
		echo "$(STRATA_SRC) has $${branch:-a detached HEAD} checked out, not $(STRATA_BRANCH) (STRATA_BRANCH= builds it anyway)." >&2; \
		exit 1; }
	podman build --platform linux/amd64 -t $(IMAGE) -f '$(CURDIR)/Containerfile' \
		--ignorefile '$(CURDIR)/Containerfile.containerignore' --build-context local='$(CURDIR)' \
		--build-arg HIP_ARCHITECTURES='$(HIP_ARCHITECTURES)' --build-arg BUILD_JOBS='$(BUILD_JOBS)' \
		--build-arg STRATA_COMMIT='$(STRATA_COMMIT)' --build-arg STRATA_BUILD_ID='$(STRATA_BUILD_ID)' '$(STRATA_SRC)'
init: secrets config build
	$(COMPOSE) run --rm --no-deps -T model-init
ifeq ($(STRATA_SERVER),quadlet)
stop-server down:
	$(call unit,stop)
# Update the deployment: stop the server, build the image, import config/, prepare the models (make init) and start it
# again, leaving the unit's file as it is. up also writes the unit with this invocation's values (make quadlet) first,
# when they change it.
sync: stop-server init
	$(call unit,start)
up: stop-server init quadlet
	$(call unit,start)
logs:
	podman logs -f $(QUADLET_UNIT)
else ifeq ($(STRATA_SERVER),compose)
stop-server:
	$(COMPOSE) stop strata-server
down:
	$(COMPOSE) down
sync up: stop-server init
	$(COMPOSE) up -d --no-deps --force-recreate strata-server
logs:
	$(COMPOSE) logs -f strata-server
else
$(error STRATA_SERVER is quadlet or compose, not $(STRATA_SERVER))
endif
status:
	podman ps -a --filter name=strata-local --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
	-@curl -sS --max-time 5 'http://$(STRATA_BIND_ADDRESS):$(STRATA_PORT)/health' && echo
calibrate: stop-server
	$(COMPOSE) run --rm --no-deps -T --entrypoint python strata-server /usr/local/lib/strata-local/calibrate.py
# Calibrate a hipBLASLt table for this card and the image's hipBLASLt for every model with a shape file
# (scripts/hipblaslt-shapes/<model>.txt; solution ids measured fresh): writes config/<arch>-hipblaslt-<version>-<model>.txt,
# which engine_env.hipblaslt_tuning (STRATA_HIPBLASLT_TUNING) of each model's config/server-<name>.json names (the
# qwen table for q1 and q3). Then make up.
TUNE_MODELS := $(shell sh scripts/tune-hipblaslt.sh list)
tune: stop-server build
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
# Write the unit with this invocation's values on the host and reload systemd, only when they change it; it neither
# starts nor restarts the server (make up does both).
quadlet:
	@if [ -n "$${CONTAINER_CONNECTION:-}$${CONTAINER_HOST:-}" ] && [ -z '$(DEPLOY_SSH)' ]; then \
		echo 'make quadlet needs the deploy host: unset CONTAINER_CONNECTION and CONTAINER_HOST.' >&2; exit 1; fi
	$(quadlet_render) | ./scripts/install-quadlet.sh '$(QUADLET_DIR)' $(QUADLET_UNIT)
# Stop the unit's server (if it runs) and remove the unit; the volumes, image and credentials stay.
quadlet-uninstall:
	-$(call unit,stop)
	$(ON_HOST) 'rm -f $(QUADLET_DIR)/$(QUADLET_UNIT).container && systemctl --user daemon-reload'
check:
	python3 -m unittest discover -s tests -v
	sh -n scripts/credentials.sh scripts/register-podman-secrets.sh scripts/podman-secret-driver.sh scripts/config-volume.sh \
		scripts/on-host.sh scripts/install-driver.sh scripts/install-quadlet.sh
	podman-compose -f compose.yaml config >/dev/null
	dir=$$(mktemp -d) && $(quadlet_render) > "$$dir/$(QUADLET_UNIT).container" \
		&& QUADLET_UNIT_DIRS="$$dir" /usr/libexec/podman/quadlet -dryrun -user >/dev/null; \
		status=$$?; rm -f "$$dir/$(QUADLET_UNIT).container"; rmdir "$$dir"; exit $$status
