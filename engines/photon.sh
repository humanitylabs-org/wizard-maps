#!/bin/bash
# Maps search engine supervisor (runs in eclipse-temurin, no Python). The Maps app prepares a merged, filtered Photon
# dump and writes engines/photon/want.env; this loop imports it into a new generation directory while the previous
# generation keeps serving, then swaps. Status for the app goes to engines/photon/status.json.
set -u
D=/data/engines/photon
JAR=/data/bin/photon.jar
mkdir -p "$D"
SERVE_PID=""
SERVING=$(cat "$D/current" 2>/dev/null || echo 0)
DONE=$(cat "$D/done" 2>/dev/null || echo 0)
HEAP_IMPORT=${PHOTON_IMPORT_HEAP:-3g}
HEAP_SERVE=${PHOTON_SERVE_HEAP:-1g}
THREADS=${PHOTON_IMPORT_THREADS:-2}

status() { # state step message (JSON-safe: no quotes, backslashes or control characters)
  local msg
  msg=$(printf '%s' "$3" | tr -d '\000-\037\\' | tr '"' "'")
  printf '{"state":"%s","step":"%s","message":"%s","serving":%s,"done":%s,"updated":%s}\n' \
    "$1" "$2" "$msg" "$SERVING" "$DONE" "$(date +%s)" > "$D/status.tmp" && mv "$D/status.tmp" "$D/status.json"
}

serve() {
  [ -n "$SERVE_PID" ] && kill "$SERVE_PID" 2>/dev/null && wait "$SERVE_PID" 2>/dev/null
  SERVE_PID=""
  if [ "$SERVING" != "0" ] && [ -d "$D/gen-$SERVING/photon_data" ]; then
    java -Xmx"$HEAP_SERVE" -jar "$JAR" serve -data-dir "$D/gen-$SERVING" -listen-ip 0.0.0.0 -listen-port 2322 \
      > "$D/serve.log" 2>&1 &
    SERVE_PID=$!
  fi
}

# OpenSearch inside Photon refuses writes once the disk is 95% full (flood-stage watermark), even with plenty of GB
# free on a big disk. The app checks real free space itself before a build, so switch that guard off and lift a block
# it may already have set. Port 9201 is Photon's embedded OpenSearch HTTP port (loopback only).
unblock() {
  curl -s -m 3 -XPUT localhost:9201/_cluster/settings -H 'Content-Type: application/json' \
    -d '{"persistent":{"cluster.routing.allocation.disk.threshold_enabled":false}}' >/dev/null 2>&1
  curl -s -m 3 -XPUT localhost:9201/_all/_settings -H 'Content-Type: application/json' \
    -d '{"index.blocks.read_only_allow_delete":null}' >/dev/null 2>&1
}

trap '[ -n "$SERVE_PID" ] && kill "$SERVE_PID"; exit 0' TERM INT
serve
status "$([ "$SERVING" = 0 ] && echo idle || echo serving)" "" ""
while true; do
  if [ -f "$D/want.env" ]; then
    GEN=0; INPUT=""; LANGS=en; EXTRA=""
    # shellcheck disable=SC1091
    . "$D/want.env"
    if [ "$GEN" -gt "$DONE" ] && [ -f "$INPUT" ] && [ -f "$JAR" ]; then
      OUT="$D/gen-$GEN"; rm -rf "$OUT"; mkdir -p "$OUT"
      status building import "Importing search data"
      java -Xmx"$HEAP_IMPORT" -jar "$JAR" import -j "$THREADS" -languages "$LANGS" ${EXTRA:+-extra-tags "$EXTRA"} \
        -import-file "$INPUT" -data-dir "$OUT" > "$D/import.log" 2>&1 &
      IMP=$!
      while kill -0 "$IMP" 2>/dev/null; do
        unblock
        status building import "$(grep -o 'Imported [0-9]* documents' "$D/import.log" | tail -1)"
        sleep 3
      done
      if wait "$IMP" && [ -d "$OUT/photon_data" ]; then
        OLD=$SERVING; SERVING=$GEN; DONE=$GEN
        echo "$SERVING" > "$D/current"; echo "$DONE" > "$D/done"
        serve
        [ "$OLD" != "0" ] && [ "$OLD" != "$SERVING" ] && rm -rf "$D/gen-$OLD"
        rm -f "$INPUT"
        status serving "" "Search index ready"
      else
        DONE=$GEN; echo "$DONE" > "$D/done"; rm -rf "$OUT" "$INPUT"
        status failed import "$(tail -c 300 "$D/import.log" | tr '\n"' "  ")"
      fi
    fi
  fi
  if [ "$SERVING" != "0" ] && { [ -z "$SERVE_PID" ] || ! kill -0 "$SERVE_PID" 2>/dev/null; }; then serve; fi
  sleep 5
done
