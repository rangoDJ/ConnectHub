# syntax=docker/dockerfile:1
# ubuntunoble stopped receiving builds in June 2026; its older Selkies rejects h264enc
FROM ghcr.io/linuxserver/baseimage-selkies:ubunturesolute AS base

# Strip what ConnectHub never uses from the base: Docker-in-Docker, compilers, all but one
# locale, CJK serif fonts (~800 MB). Deleting files in a later layer can't shrink an
# image, so this stage's filesystem is copied into a fresh one below. CI's layer cache
# keeps this stage, and so that copied layer, unchanged until the base image changes,
# so updates still only download ConnectHub's own layers.
FROM base AS slim
COPY scripts/slim_base.sh /tmp/slim_base.sh
RUN sh /tmp/slim_base.sh && env | sort > /connecthub-base.env

FROM scratch
COPY --from=slim / /

# The base image's configuration, which copying its files doesn't carry over.
# check_base_env.sh below fails the build if this drifts from the base image.
ENV HOME=/config \
    LANGUAGE=en_US.UTF-8 \
    LANG=en_US.UTF-8 \
    TERM=xterm \
    SHELL=/bin/bash \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    S6_VERBOSITY=1 \
    S6_STAGE2_HOOK=/docker-mods \
    DISPLAY=:1 \
    PERL5LIB=/usr/local/bin \
    PULSE_RUNTIME_PATH=/defaults \
    SELKIES_INTERPOSER=/usr/lib/selkies_input_interposer.so \
    SELKIES_WEBCAM_INTERPOSER=/usr/lib/selkies_v4l2_interposer.so \
    NVIDIA_DRIVER_CAPABILITIES=all \
    DISABLE_DRI3=false \
    SELKIES_ENCODER=h264enc,h265enc,vp8enc,vp9enc,av1enc,jpeg \
    SELKIES_ENABLE_BASIC_AUTH=false \
    SELKIES_VIDEO_STREAMING_MODE=false \
    SELKIES_ALLOWED_ORIGINS=* \
    SELKIES_RATE_CONTROL_MODE=crf,cbr \
    __GL_SYNC_TO_VBLANK=0 \
    TITLE=Selkies
# Docker was removed above; the base's svc-docker only sleeps with this false
ENV START_DOCKER=false

LABEL maintainer="kodi"
LABEL description="ConnectHub - WebRTC remote desktop gateway for RDP, VNC and SSH, multi-GPU encoding (NVIDIA/AMD/Intel/CPU), Authentik SSO & Basic Auth, and WebUI File Sharing."

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/venv \
    PATH="/app/venv/bin:/lsiopy/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

COPY scripts/check_base_env.sh /tmp/check_base_env.sh
RUN sh /tmp/check_base_env.sh /connecthub-base.env \
    && rm -f /tmp/check_base_env.sh /connecthub-base.env

# The base's "*" lets any page open the stream WebSocket. The session cookie only keeps
# out other *sites*, so a page on a sibling subdomain (other.example.com beside
# hub.example.com) could still connect and control the desktop. Empty is Selkies'
# same-origin default: the Origin's hostname must match the Host nginx forwards.
ENV SELKIES_ALLOWED_ORIGINS=

# Selkies' own file panel (side menu) defaults to ~/Desktop. Point it at the shared
# folder, so it shows what the Shared Files drawer and \\tsclient\SharedFolder show.
ENV SELKIES_FILE_MANAGER_PATH=/shared

# Install FreeRDP 3, GPU drivers for VA-API, Python 3, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    freerdp3-x11 \
    mesa-va-drivers \
    libva2 \
    libva-drm2 \
    vainfo \
    python3 \
    python3-venv \
    procps \
    curl \
    jq \
    feh \
    xdotool \
    xclip \
    tigervnc-viewer \
    tigervnc-tools \
    openssh-client \
    sshpass \
    xterm \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment for Python FastAPI WebUI & API
RUN python3 -m venv /app/venv

# Install Python backend dependencies
COPY root/app/backend/requirements.txt /tmp/requirements.txt
RUN /app/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# Ensure symlink for xfreerdp exists for universal compatibility
RUN if [ ! -f /usr/bin/xfreerdp ] && [ -f /usr/bin/xfreerdp3 ]; then \
        ln -s /usr/bin/xfreerdp3 /usr/bin/xfreerdp; \
    fi

# Create shared folder for RDP drive redirection & web file manager
RUN mkdir -p /shared /config /app/backend /app/frontend

# Patch Selkies for image paste: the web client reads the clipboard on Ctrl+V in
# Chromium/Brave/Edge, and the server adds a BMP beside pasted images for FreeRDP
COPY scripts/patch_selkies.py /tmp/patch_selkies.py
RUN python3 /tmp/patch_selkies.py && rm -f /tmp/patch_selkies.py

# Selkies' output goes through s6-log (svc-selkies-log, from root/ below) so the dashboard
# can show it. Its run script gets a prelude that sends stderr down that pipe too. Stops
# the build if the base image ever pipes svc-selkies somewhere itself, since two
# producer-for files would otherwise only fail when the container starts.
COPY scripts/selkies-run-prelude.sh /tmp/selkies-run-prelude.sh
RUN run=/etc/s6-overlay/s6-rc.d/svc-selkies/run \
    && head -n1 "$run" | grep -q '^#!' \
    && test ! -e /etc/s6-overlay/s6-rc.d/svc-selkies/producer-for \
    && sed -i '1r /tmp/selkies-run-prelude.sh' "$run" \
    && rm -f /tmp/selkies-run-prelude.sh

# Copy root configuration files
COPY root/ /

# Set executable permissions for service scripts and autostart
RUN chmod +x \
    /defaults/autostart \
    /etc/s6-overlay/s6-rc.d/02-rdp-webui/run \
    /etc/s6-overlay/s6-rc.d/svc-selkies-log/run

# Compile the service tree as the container would at start, so a broken service or
# pipeline (like the svc-selkies -> svc-selkies-log one above) fails the build instead
RUN /command/s6-rc-compile /tmp/s6-rc-check \
        /package/admin/s6-overlay/etc/s6-rc/sources \
        /etc/s6-overlay/s6-rc.d \
    && rm -rf /tmp/s6-rc-check

# What the session needs still runs after the slimming above
RUN /lsiopy/bin/python3 -c "import selkies, pixelflux, pcmflux" \
    && for bin in Xvfb nginx pulseaudio openbox xdotool xclip xterm xfreerdp3 xtigervncviewer sshpass s6-rc; do \
           command -v "$bin" > /dev/null || { echo "missing after slimming: $bin" >&2; exit 1; }; \
       done \
    && locale -a | grep -qix 'en_US.utf8'

# Release version, set by CI. Declared last so a new version doesn't invalidate
# the cached package and pip layers above.
ARG APP_VERSION=0.0.0-dev
ENV CONNECTHUB_VERSION=$APP_VERSION

# Volumes
VOLUME ["/config", "/shared"]

# Port 3000 (HTTP) and 3001 (HTTPS, self-signed) serve the WebUI, API, and Selkies stream
EXPOSE 3000 3001

ENTRYPOINT ["/init"]
