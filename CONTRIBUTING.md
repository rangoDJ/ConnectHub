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

Open an issue describing the problem you want to solve and how you imagine it working. Every change starts with an issue (see [Workflow](#workflow-one-issue-one-pull-request)). For larger changes (new protocols, new auth modes, changes to the container layout) please discuss the idea in an issue before starting on a pull request.

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

### Running the Tests
The backend has a pytest suite in `tests/`. Run it from the repository root in a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r root/app/backend/requirements.txt -r requirements-dev.txt
pytest
```

The tests use temporary directories for `/config` and `/shared`, so they don't need Docker or a running container. On Windows, one file-manager test that creates symlinks is skipped unless you have the privilege to create them.

### Project Layout
| Path | What lives there |
|---|---|
| `root/app/backend/` | FastAPI backend: routing (`main.py`), authentication (`auth.py`), session supervision (`session_manager.py`) and the shared-files API (`file_manager.py`) |
| `root/app/frontend/` | Static single-page UI (plain HTML, CSS and JavaScript, no build step) |
| `root/defaults/` | Nginx config and the desktop session autostart script |
| `root/etc/s6-overlay/` | s6 service definitions that start the backend inside the container |
| `.github/workflows/` | CI that builds and publishes the multi-arch image to GHCR |

---

## Workflow: One Issue, One Pull Request

Every change follows the same path:

1. **Create the issue first.** Describe the problem or feature before writing any code, even if you plan to fix it yourself.
2. **Open one pull request for that issue.** Each PR fixes exactly one issue, and each issue is fixed by one PR. Reference it in the PR description with `Fixes #N`.
3. **Found something else along the way?** Don't fold it into the current PR. Open a new issue for it, then a separate PR. If the new fix depends on an open PR, base it on that PR's branch and say which to merge first.
4. **Test before merging.** A PR is merged only once it has been tested (see [Making Changes](#making-changes)).
5. **Merge, then close the issue.** `Fixes #N` closes the issue automatically when the PR lands on `main`. If it was merged into another branch instead, close the issue by hand with a link to the PR.

---

## Making Changes

1. Fork the repository and create a branch from `main` with a descriptive name, e.g. `fix/vnc-resize` or `feature/ssh-agent-forwarding`.
2. Keep the pull request to the one issue it fixes (see [Workflow](#workflow-one-issue-one-pull-request)). Unrelated refactors get their own issue and PR.
3. Run `pytest` and add or update tests for any backend change.
4. Test your change in a running container against at least one real target for every protocol it affects.
5. Update `README.md` if you add or change environment variables, ports, volumes or user-facing behaviour.
6. Make sure `docker build .` still succeeds.

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

- Link the one issue it fixes with `Fixes #12`, and describe what changed and why.
- Explain how you tested it (protocol, target OS, auth mode, GPU/CPU encoder).
- Include screenshots or a short recording for UI changes.
- Make sure `pytest` passes locally and the GitHub Actions image build succeeds.

A maintainer will review your PR and may ask for changes. Once approved, it will be merged into `main`, and a new `latest` image is published automatically.
