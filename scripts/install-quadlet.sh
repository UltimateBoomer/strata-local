#!/bin/sh
# install-quadlet.sh DIR NAME < UNIT: write UNIT (the rendered Quadlet file) to DIR/NAME.container on the deploy host
# (on-host.sh) and reload its systemd user manager, only when it differs from the file there. It starts nothing.
set -eu
dir=${1:?Expected the Quadlet directory}
name=${2:?Expected the unit name}
here=$(dirname "$0")
new=$(mktemp "${TMPDIR:-/tmp}/strata-quadlet.XXXXXX")
trap 'rm -f "$new"' EXIT
cat > "$new"
file=$dir/$name.container
if "$here/on-host.sh" "cat '$file'" 2>/dev/null | cmp -s - "$new"; then
  echo "$file is up to date" >&2
  exit 0
fi
"$here/on-host.sh" "mkdir -p '$dir' && cat > '$file.tmp' && mv -f '$file.tmp' '$file'" < "$new"
"$here/on-host.sh" 'systemctl --user daemon-reload'
echo "wrote $file" >&2
