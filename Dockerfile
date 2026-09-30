# syntax=docker/dockerfile:1
# ubuntunoble stopped receiving builds in June 2026; its older Selkies rejects h264enc
FROM ghcr.io/linuxserver/baseimage-selkies:ubunturesolute

LABEL maintainer="kodi"
LABEL description="ConnectHub - WebRTC remote desktop gateway for RDP, VNC and SSH, multi-GPU encoding (NVIDIA/AMD/Intel/CPU), Authentik SSO & Basic Auth, and WebUI File Sharing."

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/venv \
    PATH="/app/venv/bin:$PATH"

# Install FreeRDP 3, GPU drivers for VA-API, Python 3, and utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    freerdp3-x11 \
    mesa-va-drivers \
    libva2 \
    libva-drm2 \
    vainfo \
    python3 \
    python3-pip \
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

# Release version, set by CI. Declared last so a new version doesn't invalidate
# the cached package and pip layers above.
ARG APP_VERSION=0.0.0-dev
ENV CONNECTHUB_VERSION=$APP_VERSION

# Volumes
VOLUME ["/config", "/shared"]

# Port 3000 (HTTP) and 3001 (HTTPS, self-signed) serve the WebUI, API, and Selkies stream
EXPOSE 3000 3001
