#!/bin/sh
# Calibrate a hipBLASLt table for this card and the image's hipBLASLt; the table goes to stdout, progress to stderr.
#   tune-hipblaslt.sh <model>   the shapes of hipblaslt-shapes/<model>.txt (beside this script), every solution id
#                               measured fresh
#   tune-hipblaslt.sh list      the models that have a shape file, one a line
# A model's engine reads its table through STRATA_HIPBLASLT_TUNING (engine_env.hipblaslt_tuning); a table is good for one
# GPU architecture family and one hipBLASLt version. Another model: add hipblaslt-shapes/<model>.txt.
set -eu
shapes=$(cd "$(dirname "$0")/hipblaslt-shapes" && pwd)
if [ "${1:-}" = list ]; then
    for f in "$shapes"/*.txt; do basename "$f" .txt; done
    exit 0
fi
file="$shapes/${1:?usage: tune-hipblaslt.sh <model> | list}.txt"
test -f "$file" || { echo "tune-hipblaslt.sh: no shapes for $1 ($file)" >&2; exit 2; }
cases=$(awk '!/^[[:space:]]*(#|$)/ {printf " --case %s", $1}' "$file")
cd /opt/strata
# shellcheck disable=SC2086
engine/tune_hipblaslt $cases --tuning-out /tmp/table.txt >&2
cat /tmp/table.txt
