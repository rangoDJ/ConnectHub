# ConnectHub

**Browser-based remote desktop gateway for RDP, VNC & SSH, with a WebUI, Multi-GPU encoding, File Sharing & Authentik SSO.**

A high-performance, low-latency WebRTC remote desktop container that connects to Windows machines via **FreeRDP 3** and streams the display directly to your browser using **Selkies**. Equipped with a modern **WebUI configuration dashboard**, persistent profile storage, **two-way file sharing**, and **Authentik SSO / Basic Auth**.

---

## Key Features

- ⚡ **Ultra-Low Latency Streaming**: Powered by **Selkies-GStreamer** WebRTC, delivering responsive 60fps streaming with minimal lag.
- 🎯 **Multi-Encoder Acceleration**:
  - **CPU Software Encoding**: Default fallback (`x264` / `openh264`) that runs on any host.
  - **AMD & Intel GPU Acceleration**: Hardware VA-API encoding via `/dev/dri`.
  - **NVIDIA GPU Acceleration**: Hardware NVENC encoding via `nvidia-container-toolkit`.
- 🔐 **Comprehensive Authentication**:
  - **Single Sign-On (SSO)**: Native OIDC with **Authentik**, Keycloak, Google Workspace, Microsoft Entra ID, or Okta.
  - **Forward Auth / Reverse Proxy**: Out-of-the-box header trust for Authentik Proxy Outpost, Traefik, Authelia, and Cloudflare Access.
  - **Basic Auth**: Standard username and password protection with secure signed session cookies.
- 📁 **Bidirectional File Sharing**:
  - FreeRDP drive redirection mounts the container's `/shared` directory as `\\tsclient\SharedFolder` in Windows.
  - Integrated **WebUI File Manager drawer** for drag-and-drop file uploads and instant downloads from the remote Windows machine.
- 🔊 **Audio Playback**: FreeRDP sound redirection (`/sound:sys:pulse`) routed through PulseAudio and WebRTC directly to your browser.
- 📋 **Clipboard Synchronization**: Bidirectional text and clipboard sync between local browser and Windows, including pasting images into RDP sessions, with an on-screen clipboard helper.
- 🎮 **In-Stream Floating Toolbar**:
  - One-click **Disconnect** (terminates RDP session cleanly and returns to the dashboard).
  - Special key injection: **Ctrl + Alt + Del**, **Windows Key (⊞)**, and **Alt + Tab**.
  - Slide-out drawers for **Files** and **Clipboard**.
  - True fullscreen toggle.

---

## Architecture Overview

```mermaid
flowchart TD
    subgraph Client["Client Browser"]
        WebUI["WebUI Dashboard & File Manager"]
        Stream["Embedded Selkies WebRTC Stream"]
    end

    subgraph Container["ConnectHub Docker Container (:8080)"]
        subgraph WebProxy["Nginx Reverse Proxy & Auth Guard"]
            API["FastAPI Backend (Session & File Controller)"]
            AuthEngine["Auth Module (Basic / OIDC / Forward Auth)"]
            Files["/shared (Shared Files) & /config (Profiles)"]
        end

        subgraph StreamingEngine["Selkies Streaming Core"]
            X11["Virtual X11 Display (:1)"]
            Pulse["PulseAudio Server"]
            GStreamer["GStreamer Pipeline"]
            Encoders{"Encoder Selection"}
        end

        subgraph FreeRDPClient["FreeRDP 3 (xfreerdp)"]
            RDPProcess["xfreerdp /v:host /sound /clipboard:files-to:off /drive:SharedFolder,/shared"]
        end
    end

    subgraph Hardware["Host Hardware"]
        CPU["CPU Software (x264)"]
        NV["NVIDIA (NVENC)"]
        AMD["AMD / Intel (VA-API)"]
    end

    subgraph Windows["Remote Target"]
        Win["Windows PC (Port 3389 / RDP)"]
    end

    Client -->|HTTP / WebRTC| WebProxy
    WebProxy --> API
    API --> AuthEngine
    API --> Files
    API -->|Spawns & Monitors| RDPProcess

    GStreamer --> Encoders
    Encoders --> CPU
    Encoders --> NV
    Encoders --> AMD

    RDPProcess -->|Renders Display| X11
    RDPProcess -->|Redirects Audio| Pulse
    RDPProcess -->|Mounts Shared Folder| Files
    RDPProcess -->|RDP Protocol (TLS/NLA)| Win

    X11 --> GStreamer
    Pulse --> GStreamer
```

---

## Quick Start

### 1. Clone & Prepare
```bash
git clone https://github.com/rangoDJ/ConnectHub.git
cd ConnectHub
cp .env.example .env
```

### 2. Launch the Container
The image `ghcr.io/rangodj/connecthub:latest` (linux/amd64 + linux/arm64) is built by GitHub Actions on every push to `main`. Every merged pull request is also released as a new version, published as `X.Y.Z` and `X.Y` tags with a matching [GitHub Release](https://github.com/rangoDJ/ConnectHub/releases). Pin a version tag (e.g. `:1.1`) instead of `latest` if you want to choose when to upgrade. `sha-<commit>` tags are published too.
```bash
docker compose pull
docker compose up -d
```

Access the WebUI at:
```
http://<your-server-ip>:8080
https://<your-server-ip>:8443   (self-signed; required for browser clipboard access)
```

---

## Hardware Acceleration (GPU Setup)

The container uses `SELKIES_ENCODER=h264enc`, which encodes on the GPU (NVENC or VA-API) when one is passed to the container and falls back to CPU software encoding (`x264`) when not. `AUTO_GPU` picks the GPU (`true` for automatic, or `nvidia`, `amdgpu`, `intel`).

### Profile A: CPU Software Encoding (Default)
No extra configuration required! The default `docker-compose.yml` runs anywhere without GPU requirements. To force CPU encoding on a host that does have a GPU, set `SELKIES_USE_CPU=true`.

### Profile B: AMD Radeon or Intel Iris/Arc/UHD (VA-API)
1. Ensure the user running Docker is in the `video` and `render` groups.
2. In `docker-compose.yml`, uncomment the `devices:` block and the `DRINODE` / `DRI_NODE` lines inside the existing `environment:` list (don't add a second `environment:` key — YAML would discard the first one):
```yaml
devices:
  - /dev/dri:/dev/dri
```

### Profile C: NVIDIA GPU (NVENC)
1. Install [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) on the host.
2. In `docker-compose.yml`, uncomment the `deploy:` block and the `NVIDIA_*` and `AUTO_GPU=nvidia` lines inside the existing `environment:` list:
```yaml
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]
```

---

## Authentication Configuration

Edit `.env` to select your preferred authentication model:

### Mode 1: No Authentication (`AUTH_MODE=none`)
Direct open access to the dashboard. Ideal for isolated local subnets or systems behind Tailscale/WireGuard.
```ini
AUTH_MODE=none
```

### Mode 2: Basic Authentication (`AUTH_MODE=basic`)
Username and password accounts, managed in the WebUI:
```ini
AUTH_MODE=basic
ALLOW_SIGNUPS=false
```
- **First run:** there is no default account and no password in the environment. The first visitor to the login page is asked to create an account. **Do this right after starting the container**, before anyone else can reach it.
- **More accounts:** sign-up is off by default. Set `ALLOW_SIGNUPS=true` to show a **Create one** link on the login page, and set it back to `false` once everyone has an account. Creating the first account is always allowed.
- **Sessions:** a login lasts 7 days. **Logout** ends that session on the server, so a copy of its cookie stops working too; other devices stay signed in. Sessions (basic and OIDC) are listed in `/config/sessions.json`, so a restart keeps you signed in.
- **Changing a password:** click **Password** in the top bar. This signs out that user's other sessions.
- **Storage:** accounts live in `/config/users.json`, with passwords as salted scrypt hashes and the file readable only by the container user.
- **Forgotten password:** remove that user's entry from `/config/users.json` (or delete the file to start over) and restart the container.

After 10 failed attempts an IP is locked out for 15 minutes.

> **Upgrading from `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD`:** those variables are no longer used. After upgrading, the login page shows the first-account form, so create your account straight away, then remove the old variables from your compose file.

### Mode 3: Authentik Single Sign-On (OIDC)
Direct integration with Authentik via OpenID Connect:

1. **In Authentik Admin Interface**:
   - Go to **Applications** -> **Providers** -> **Create Provider**.
   - Type: **OAuth2/OpenID Provider**.
   - Name: `ConnectHub`.
   - Client type: **Confidential**.
   - Redirect URIs: `http://<your-server-ip-or-domain>:8080/auth/callback`.
   - Scopes: ensure `openid`, `email`, `profile` are selected.
   - Note the **Client ID** and **Client Secret**.
   - Go to **Applications** -> **Create Application**.
   - Attach the application to your new `ConnectHub` provider.
2. **In `.env`**:
```ini
AUTH_MODE=oidc
OIDC_ISSUER_URL=https://authentik.yourdomain.com/application/o/connecthub/
OIDC_CLIENT_ID=your_authentik_client_id
OIDC_CLIENT_SECRET=your_authentik_client_secret
OIDC_REDIRECT_URI=http://<your-server-ip-or-domain>:8080/auth/callback
```

### Mode 4: Authentik Proxy Outpost / Forward Auth (`AUTH_MODE=forward_auth`)
If you place this container behind an Authentik Embedded/Proxy Outpost, Traefik ForwardAuth, Authelia, or Cloudflare Access:
```ini
AUTH_MODE=forward_auth
FORWARD_AUTH_HEADER=X-authentik-username
FORWARD_AUTH_TRUSTED_PROXIES=172.18.0.0/16
```
The application will automatically recognize the authenticated user and grant access without a secondary login prompt.

`FORWARD_AUTH_TRUSTED_PROXIES` is **required**: the header is only honoured when the request comes from one of these IPs/CIDRs (your proxy's address or Docker network). Otherwise anyone reaching the container port directly could forge the header. Also avoid publishing the container port publicly in this mode.

### Behind a reverse proxy or tunnel

If ConnectHub sits behind a reverse proxy or tunnel (Cloudflare Tunnel, Traefik, Nginx Proxy Manager, ...), every request reaches it from the proxy's address. List the proxy's IPs or network so ConnectHub uses the visitor's address from the proxy's `X-Forwarded-For` header instead:

```ini
TRUSTED_PROXIES=172.18.0.0/16
```

Without it, failed logins from anyone count against everyone: ten wrong passwords lock all visitors out for 15 minutes, and logs show the proxy's address. It also lets `COOKIE_SECURE=auto` see that the proxy serves HTTPS when it talks plain HTTP to ConnectHub, so the session cookie is marked `Secure`. Only list addresses that are proxies: a listed address can claim to forward for any visitor.

### Which pages may open the stream

Only ConnectHub's own page can open the remote desktop stream: Selkies checks that the browser's `Origin` matches the address you reached ConnectHub on, which keeps out pages on other sites, including other apps on sibling subdomains of the same domain. This works unchanged through a reverse proxy or tunnel, by LAN IP and on localhost. To allow another page (for example a portal that embeds ConnectHub from a different hostname), list its origin:

```yaml
SELKIES_ALLOWED_ORIGINS=https://portal.example.com
```

Avoid `*`, which lets any page connect if the browser sends your session cookie.

---

## Connection Types (RDP, VNC, SSH)

Pick the protocol at the top of the connection form. Each session runs fullscreen inside the container's display and is streamed to your browser the same way.

| Protocol | Client | Default port | Notes |
|---|---|---|---|
| **RDP** | FreeRDP 3 | 3389 | Audio, clipboard, shared folder drive, auto display scaling. |
| **VNC** | TigerVNC viewer | 5900 | VNC password only (no username). Optional view-only mode. The remote desktop follows the window size if the server supports it (e.g. TigerVNC/x11vnc); otherwise it keeps its own size. |
| **SSH** | `ssh` in an xterm | 22 | Password (via `sshpass`), private key, or type the password in the terminal. Host keys are trusted on first use and stored in `/config/.ssh/known_hosts`; a changed key is refused. Selecting text copies it to the clipboard. |

Passwords and SSH keys are stored in `/config/profiles.json` (readable only by the container user) and are never passed on a command line. SSH keys and VNC password files are written to a private temporary directory for the length of the session and deleted afterwards.

The WebUI never shows a saved password or key again. It is only reused for the host, port and protocol it was saved with: after changing any of those on a saved profile, enter the password (or SSH key) again.

### Disconnecting idle sessions

A session runs inside the container, not the browser, so closing the tab leaves it running: Windows stays logged in, and a Windows PC can't be used locally while it is. Set `IDLE_DISCONNECT_MINUTES` to end the session once no dashboard has been open for that long:

```yaml
IDLE_DISCONNECT_MINUTES=15
```

An open dashboard (either view, even in a background tab) keeps the session alive. Closing every tab, or the computer going to sleep, starts the countdown, and the **Session** log notes the disconnect. Unset or `0` (the default) keeps sessions running until you disconnect.

---

## How File Sharing Works

File sharing between your client browser and the remote Windows machine is bidirectional and automatic:

1. **In Windows**:
   - Open **This PC** (File Explorer).
   - Under **Network locations** / **Redirected drives**, you will see **`SharedFolder on <client>`** (path `\\tsclient\SharedFolder`).
   - Any files placed into this folder in Windows are saved into the container's `/shared` directory.
2. **In the WebUI**:
   - Click the **📁 Shared Files** button in the top navigation bar or the in-stream floating toolbar.
   - **Upload**: Drag-and-drop any file into the drawer to make it instantly accessible inside Windows.
   - **Download**: Click **Download** next to any file saved from Windows.
   - The file panel in Selkies' side menu (inside the stream) shows the same folder, and streams large downloads.
3. **On the Docker Host**:
   - Files are stored on your host in `./shared` (mounted volume).

---

## Copying and Pasting Images

Images can be pasted into an **RDP** session from Chrome, Edge or Brave. VNC and SSH sessions carry text only, since the TigerVNC viewer and xterm have no image clipboard.

1. Open the WebUI over **HTTPS** (`https://<your-server-ip>:8443`). Browsers only let a page read the clipboard over HTTPS.
2. Copy an image locally, click into the stream and press **Ctrl+V**. The first time, the browser asks to let the page see your clipboard. Choose **Allow**.
3. Keep **Clipboard** enabled in the connection profile (it is on by default).

Over plain HTTP (port 8080) a paste still sends the image, but the keystroke can reach Windows before the image does, so the first Ctrl+V may paste the previous clipboard.

Images reach Windows as bitmaps: Ubuntu's FreeRDP build can't convert PNG itself, so the gateway adds a BMP copy of every pasted image. Transparent areas become white.

Files can't be copied through the clipboard: a browser has no way to put a file on your local clipboard, so FreeRDP's file clipboard is turned off. Move files through the shared folder instead (`\\tsclient\SharedFolder` in Windows, **Shared Files** in the WebUI; see [How File Sharing Works](#how-file-sharing-works)).

---

## Windows Machine Setup

To connect to a Windows machine:
1. On the Windows computer, go to **Settings** -> **System** -> **Remote Desktop** and toggle **Enable Remote Desktop** to **On**.
2. If connecting with a local account without a password, Windows Remote Desktop may block it by default. Ensure the account has a password.
3. If Network Level Authentication (NLA) is enabled (default), provide the valid Windows Username and Password in the WebUI.
4. If using a self-signed certificate on Windows (standard for Windows Pro), keep the **🛡️ Bypass SSL/NLA Warnings** checkbox enabled.

---

## Directory Structure

```
ConnectHub/
├── Dockerfile                        # Multi-stage image with FreeRDP 3, VA-API & Python
├── docker-compose.yml                # CPU, AMD/Intel VA-API, and NVIDIA profiles
├── .env.example                      # Template for authentication and GPU settings
├── .gitignore
├── root/
│   ├── defaults/
│   │   ├── autostart                 # Virtual X11 session desktop startup
│   │   └── default.conf              # Nginx proxy for WebUI, API, and Selkies stream
│   ├── etc/
│   │   └── s6-overlay/s6-rc.d/
│   │       ├── 02-rdp-webui/         # FastAPI backend service supervisor
│   │       └── user/contents.d/
│   └── app/
│       ├── backend/
│       │   ├── main.py               # FastAPI router and static server
│       │   ├── auth.py               # Basic Auth, Authentik OIDC & Forward Auth
│       │   ├── session_manager.py    # RDP/VNC/SSH process supervisor & key injector
│       │   ├── file_manager.py       # Upload/download API for /shared
│       │   └── requirements.txt
│       └── frontend/
│           ├── index.html            # Single-Page App (Dashboard & Stream View)
│           ├── login.html            # Login portal (Basic Auth / Authentik SSO)
│           ├── css/style.css         # Dark glassmorphism styling
│           └── js/app.js             # Client state, WebRTC iframe, and toolbar controls
├── config/                           # Persistent volume: saved profiles and secrets
├── shared/                           # Persistent volume: shared files with Windows
└── README.md
```

---

## Image Size

The image is built on LinuxServer's Selkies base, minus the parts ConnectHub never uses: Docker-in-Docker, the C/C++ compilers and build tools, every locale except `en_US.UTF-8` (and `C.UTF-8`), and the CJK serif fonts (the sans fonts remain, so SSH terminals still show CJK text). That saves about 800 MB. Setting another `LANG` or `LC_*` locale in the container therefore has no effect; it doesn't change anything on the remote machine either way.

---

## Troubleshooting

The **Logs** window on the dashboard shows everything the container logs, in order: the session's client (`xfreerdp3`, the VNC viewer or SSH, tagged `[RDP]`, `[VNC]` or `[SSH]`), ConnectHub's web server, the Selkies streaming server, the X server, audio, nginx errors and start-up. It keeps the last 2,000 lines; type in its **Filter** box to show only lines containing that text (for example `RDP`, `ERROR` or `clipboard`). Colours mark the session's client, ConnectHub's own messages, warnings and errors.

The same lines go to `docker logs connecthub`. The dashboard's status and log requests, which repeat every 2 seconds, are left out. Inside the container the log is kept, rotated, in `/var/log/connecthub/all`.

Set `CLIPBOARD_DEBUG=true` on the container to log clipboard transfers in detail: FreeRDP's clipboard channel (from the next connect) and Selkies' debug log (after a restart). Selkies' debug log is verbose, so turn it off again when you're done.

- **Black screen after clicking Connect**:
  Check the **Logs** window (filter on `RDP`). Common reasons include incorrect Windows credentials or Remote Desktop not being enabled on the target PC.
- **Audio not playing**:
  Ensure the **🔊 Audio Playback** toggle is enabled before connecting, and that your browser allows autoplay on the gateway URL.
- **Pasting an image does nothing**:
  Use the HTTPS address (port 8443) and an RDP session. If you once blocked clipboard access, re-allow it from the site settings (the icon left of the address bar), then reload the page. A group policy on the Windows machine can also turn off clipboard redirection (**Do not allow Clipboard redirection** under Remote Desktop Session Host → Device and Resource Redirection).
- **Ctrl + Alt + Del**:
  Click the **Ctrl+Alt+Del** button on the floating stream toolbar. It sends the key sequence (`Ctrl+Alt+End`) recognized by FreeRDP to trigger the Windows security screen without triggering your local host's task manager.

---

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for how to report bugs, set up a development environment and open a pull request.
