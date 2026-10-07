#!/bin/sh
# Removes the parts of baseimage-selkies that ConnectHub never uses. Runs in a build
# stage whose filesystem is then copied into a fresh image (see the Dockerfile):
# deleting files in a later layer of the same image would not make it any smaller.
set -eu

# Docker-in-Docker (installed on amd64 only), the C/C++ toolchain and build tools,
# and every locale. Only the ones actually installed are named, so arm64 builds,
# which have no Docker, don't fail on unknown packages.
PURGE=""
for pkg in docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
           gcc g++ cmake make git locales-all; do
    if dpkg-query -W -f='${db:Status-Status}' "$pkg" 2>/dev/null | grep -qx installed; then
        PURGE="$PURGE $pkg"
    fi
done

if [ -n "$PURGE" ]; then
    # apt also removes whatever depends on these. Refuse if that would take any package
    # the base image installed on purpose, rather than silently losing it.
    tmp=$(mktemp -d)
    apt-get -s purge --auto-remove $PURGE | awk '/^Purg /{print $2}' | sed 's/:.*//' | sort -u > "$tmp/removed"
    apt-mark showmanual | sed 's/:.*//' | sort -u > "$tmp/manual"
    printf '%s\n' $PURGE | sort -u > "$tmp/purge"
    # removed AND installed on purpose AND not one we asked for
    unexpected=$(comm -12 "$tmp/removed" "$tmp/manual" | comm -23 - "$tmp/purge")
    if [ -n "$unexpected" ]; then
        echo "slim_base: purging$PURGE would also remove packages the base installs on purpose:" >&2
        printf '  %s\n' $unexpected >&2
        exit 1
    fi
    echo "slim_base: removing$PURGE ($(wc -l < "$tmp/removed") packages in total)"
    rm -rf "$tmp"
    apt-get purge -y --auto-remove $PURGE
fi

rm -f /etc/apt/sources.list.d/docker.list /usr/share/keyrings/docker.asc /usr/local/bin/dind

# The base compiles hundreds of locales into one 180 MB archive; the image runs in en_US.UTF-8
rm -f /usr/lib/locale/locale-archive
localedef -i en_US -f UTF-8 en_US.UTF-8

# CJK serif faces (54 MB). The sans faces stay, so SSH terminals still show CJK text.
rm -f /usr/share/fonts/opentype/noto/NotoSerifCJK-*.ttc
fc-cache -f >/dev/null

apt-get clean
rm -rf /var/lib/apt/lists/* /var/cache/apt/* /var/log/apt /var/log/dpkg.log /tmp/* /var/tmp/*
