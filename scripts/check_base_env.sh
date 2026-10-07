#!/bin/sh
# Fails the build when the environment re-declared in the Dockerfile has drifted from
# the base image's. The image is rebuilt FROM scratch (see slim_base.sh), so it doesn't
# inherit the base's ENV, and a variable the base adds or changes later would otherwise
# go missing without anyone noticing.
#
# $1: `env` as seen in a build stage of the base image, one VAR=value per line.
set -eu

base_env=$1
# Set per process, or overridden on purpose in the Dockerfile
skip='^(HOSTNAME|PWD|OLDPWD|SHLVL|_|PATH|VIRTUAL_ENV|START_DOCKER|DEBIAN_FRONTEND)='

status=0
grep -Ev "$skip" "$base_env" | while IFS= read -r line; do
    name=${line%%=*}
    expected=${line#*=}
    actual=$(printenv "$name" || true)
    if [ "$actual" != "$expected" ]; then
        echo "check_base_env: $name is '$actual' here but '$expected' in the base image" >&2
        exit 1
    fi
done || status=1

# PATH may only add to the front of the base image's
base_path=$(grep '^PATH=' "$base_env" | cut -d= -f2-)
case "$PATH" in
    *":$base_path"|"$base_path") ;;
    *) echo "check_base_env: PATH '$PATH' does not end with the base image's '$base_path'" >&2; status=1 ;;
esac

if [ "$status" -ne 0 ]; then
    echo "check_base_env: update the ENV block in the Dockerfile to match the base image" >&2
fi
exit "$status"
