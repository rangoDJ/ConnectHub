# Contributing to ConnectHub

Thanks for your interest in improving ConnectHub! Bug reports, feature ideas, documentation fixes and code changes are all welcome.

---

## Reporting Bugs

Before opening an issue, search the [existing issues](https://github.com/rangoDJ/ConnectHub/issues) to see if it has already been reported. When filing a new one, include:

- What you expected to happen and what actually happened.
- The connection type involved (RDP, VNC or SSH) and the target OS.
- Your `AUTH_MODE` and encoder setup (CPU, VA-API or NVENC).
- Relevant output from the **Connection Logs & Diagnostics** panel and `docker logs <container>`.
- The image tag you are running (e.g. `latest` or `sha-<commit>`).

**Never paste passwords, OIDC client secrets, SSH keys or the contents of `/config/profiles.json`.** Redact hostnames and IPs if they are sensitive.

## Reporting Security Issues

Please do **not** open a public issue for security vulnerabilities (for example an authentication bypass or path traversal in the file manager). Instead, use GitHub's [private vulnerability reporting](https://github.com/rangoDJ/ConnectHub/security/advisories/new) so the issue can be fixed before it is disclosed.

## Suggesting Features

Open an issue describing the problem you want to solve and how you imagine it working. For larger changes (new protocols, new auth modes, changes to the container layout) please discuss the idea in an issue before starting on a pull request.

---

## Development Setup

### Prerequisites
- Docker with Docker Compose
- Python 3.12+ (to run the backend outside the container)
- A test machine to connect to (a Windows PC with Remote Desktop enabled, a VNC server, or an SSH host)

### Build and Run Locally
```bash
git clone https://github.com/rangoDJ/ConnectHub.git
cd ConnectHub
cp .env.example .env
```

In `docker-compose.yml`, replace the `image:` line with `build: .`, then:
```bash
docker compose up -d --build
```

The WebUI is served at `http://localhost:8080`.

### Project Layout
| Path | What lives there |
|---|---|
| `root/app/backend/` | FastAPI backend: routing (`main.py`), authentication (`auth.py`), session supervision (`session_manager.py`) and the shared-files API (`file_manager.py`) |
| `root/app/frontend/` | Static single-page UI (plain HTML, CSS and JavaScript, no build step) |
| `root/defaults/` | Nginx config and the desktop session autostart script |
| `root/etc/s6-overlay/` | s6 service definitions that start the backend inside the container |
| `.github/workflows/` | CI that builds and publishes the multi-arch image to GHCR |

---

## Making Changes

1. Fork the repository and create a branch from `main` with a descriptive name, e.g. `fix/vnc-resize` or `feature/ssh-agent-forwarding`.
2. Keep each pull request focused on a single change. Unrelated refactors are easier to review as separate PRs.
3. Test your change in a running container against at least one real target for every protocol it affects.
4. Update `README.md` if you add or change environment variables, ports, volumes or user-facing behaviour.
5. Make sure `docker build .` still succeeds.

### Coding Style
- **Python**: follow PEP 8, use type hints on new functions and keep the existing module structure.
- **JavaScript/CSS**: vanilla JS only, no frameworks or bundlers. Match the formatting of the surrounding code.
- **Comments**: explain *why* something is done, not *what* the code does.
- **Security**: never pass passwords or keys on a command line, never log secrets, and validate all user-supplied paths and hostnames on the backend.

### Commit Messages
- Use the imperative mood in the subject line: "Add VNC view-only toggle", not "Added" or "Adds".
- Keep the subject line under about 72 characters.
- Use the body to explain the motivation for the change when it isn't obvious.

---

## Pull Requests

When opening a pull request:

- Describe what changed and why, and link any related issue (e.g. `Fixes #12`).
- Explain how you tested it (protocol, target OS, auth mode, GPU/CPU encoder).
- Include screenshots or a short recording for UI changes.
- Make sure the GitHub Actions image build passes.

A maintainer will review your PR and may ask for changes. Once approved, it will be merged into `main`, and a new `latest` image is published automatically.
