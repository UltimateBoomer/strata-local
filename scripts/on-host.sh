#!/bin/sh
# on-host.sh COMMAND: run COMMAND on the deploy host, over SSH to DEPLOY_SSH (the Makefile's DEPLOY_HOST, when it
# deploys there), or on this machine without it. Only the Quadlet unit uses it (its file and systemctl --user); every
# other target works through the Podman socket. COMMAND is one string, read by the host's login shell: write it so sh
# and fish read it alike (single quotes, &&, no $(...)). stdin passes through; a terminal is passed on with -t.
set -eu
command=${1:?Expected a command}
if [ -z "${DEPLOY_SSH:-}" ]; then
  exec sh -c "$command"
fi
if [ -t 0 ] && [ -t 1 ]; then
  exec ssh -t "$DEPLOY_SSH" "$command"
fi
exec ssh -o BatchMode=yes "$DEPLOY_SSH" "$command"
