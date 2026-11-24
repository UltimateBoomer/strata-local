#!/bin/sh
# The Podman secrets strata-local-api-key and strata-local-hf-token, all through Podman (CONTAINER_CONNECTION: a remote
# host's socket; no SSH):
#
#   register-podman-secrets.sh            register them (make secrets)
#   register-podman-secrets.sh set NAME   prompt for credential NAME (strata_api_key, hf_token) and store it on the
#                                         Podman host (make set-api-key, set-hf-token)
#
# They use a shell driver that decrypts the credentials (credentials.sh) on the Podman host whenever a container is
# created: the copy install-driver.sh puts in the volume strata-local-driver. A value sent with set reaches the driver's
# store, which encrypts it there, to that host. A registration that names another driver (an earlier path, or the
# default driver) is replaced.
set -eu

if ! command -v podman >/dev/null 2>&1; then
  echo 'podman is required' >&2
  exit 1
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

secret_of() {
  case "$1" in
    strata_api_key) echo strata-local-api-key ;;
    hf_token) echo strata-local-hf-token ;;
    *) echo "Unknown credential: $1" >&2; exit 2 ;;
  esac
}

# A remote Podman named by CONTAINER_CONNECTION or CONTAINER_HOST alone (not the Makefile's deploy host, DEPLOY_SSH):
# keep the credentials here, and store their values with the remote's default driver, replacing them on every run.
if [ -n "${CONTAINER_CONNECTION:-}${CONTAINER_HOST:-}" ] && [ -z "${DEPLOY_SSH:-}" ]; then
  if [ "${1:-}" = set ]; then
    exec "$script_dir/credentials.sh" store "${2:?Expected a credential name}"
  fi
  api_key=$("$script_dir/credentials.sh" lookup strata_api_key)
  if [ -z "$api_key" ]; then
    echo 'The Strata API key credential is empty' >&2
    exit 1
  fi
  hf_token=$("$script_dir/credentials.sh" lookup hf_token)
  # Podman rejects empty secrets; the trailing newline keeps an absent HF token nonempty,
  # and both readers strip it.
  printf '%s\n' "$api_key" | podman secret create --replace strata-local-api-key - >/dev/null
  printf '%s\n' "$hf_token" | podman secret create --replace strata-local-hf-token - >/dev/null
  exit 0
fi

driver=$("$script_dir/install-driver.sh")/podman-secret-driver.sh
options() {
  echo "lookup=$driver lookup $1,store=$driver store $1,list=$driver list $1,delete=$driver delete $1"
}

if [ "${1:-}" = set ]; then
  name=${2:?Expected a credential name}
  secret_name=$(secret_of "$name")
  value=$("$script_dir/credentials.sh" prompt "$name")
  # Podman refuses an empty secret: the driver's store reads this as removing the optional credential.
  [ -n "$value" ] || value=strata-local:remove
  printf %s "$value" | podman secret create --replace --driver=shell --driver-opts "$(options "$name")" \
    "$secret_name" - >/dev/null
  echo "Stored $name on the Podman host" >&2
  exit 0
fi

register() {
  credential_name=$1
  secret_name=$(secret_of "$credential_name")
  current=$(podman secret inspect --format '{{.Spec.Driver.Name}} {{index .Spec.Driver.Options "lookup"}}' \
    "$secret_name" 2>/dev/null || true)
  if [ -n "$current" ] && [ "$current" != "shell $driver lookup $credential_name" ]; then
    podman secret rm "$secret_name" >/dev/null
  fi
  # Values are decrypted from the credential files at container creation, so registrations
  # do not need replacing when a credential changes. Avoid --replace's delete
  # path and preserve the existing opaque ID on repeated startup.
  printf %s registered | podman secret create --ignore --driver=shell \
    --driver-opts "$(options "$credential_name")" "$secret_name" - >/dev/null
}

register strata_api_key
register hf_token

# The driver decrypts the API key on the Podman host; only "ok" comes back.
if [ "$(podman secret inspect --showsecret --format '{{if .SecretData}}ok{{end}}' strata-local-api-key 2>/dev/null)" != ok ]
then
  echo 'The Podman host cannot decrypt the Strata API key: run make set-api-key' >&2
  exit 1
fi
