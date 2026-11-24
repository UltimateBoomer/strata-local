#!/bin/sh
# Install the Podman secret driver (podman-secret-driver.sh, and credentials.sh, which it runs) into the Podman volume
# strata-local-driver, through Podman (CONTAINER_CONNECTION: the remote host's socket), and print the volume's directory
# on the Podman host. Podman runs the driver there, from that directory: the secret registrations name it, so they do
# not depend on a checkout being on the host, nor on where it is. Each run replaces the copy with this checkout's.
set -eu
VOLUME=strata-local-driver
src=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
podman volume create --ignore "$VOLUME" >/dev/null
tar -C "$src" --owner=0 --group=0 --mode=755 -cf - podman-secret-driver.sh credentials.sh | podman volume import "$VOLUME" -
podman volume inspect --format '{{.Mountpoint}}' "$VOLUME"
