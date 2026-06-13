# Bluefin Agent Host

Custom Bluefin image for a Framework Desktop used to run OpenClaw, Hermes,
local LLM services, and adjacent agent workflows with conservative supply-chain
defaults. It starts from stock Bluefin and recreates the DX capabilities needed
for this host instead of depending directly on the `bluefin-dx` image tag.

The image intentionally does not preinstall fast-moving npm agent packages.
OpenClaw and similar tools should be installed as the dedicated agent user
through Homebrew's `fnm`-managed Node runtime, with npm lifecycle scripts
disabled by default.

Agent work is intended to run as a dedicated unprivileged user. The default
username is `claudex`, configurable at build time with `BLUEFIN_AGENT_USER`.
The image creates that account on first boot, keeps it out of admin-equivalent
groups, and makes the agent `ujust` recipes refuse to run under the wrong user.

## Base

```Dockerfile
FROM ghcr.io/ublue-os/bluefin:stable
```

Stock Bluefin is the base. The repo then layers a local DX compatibility subset
from upstream Bluefin's current `build_files/dx/00-dx.sh` and `system_files/dx`
patterns:

- VS Code from Microsoft's RPM repository.
- Podman, QEMU/KVM, libvirt, virt-manager, Incus/LXC, Cockpit, flatpak-builder,
  devcontainer-adjacent tooling, and DX performance tools from Fedora repos.
- Bluefin DX-style group setup and SELinux relabel helpers for libvirt and
  Incus.
- Optional Docker Engine, Compose, Buildx, and Docker Model Runner when
  explicitly enabled at build time.

This avoids a hard dependency on the upstream `bluefin-dx` image continuing to
exist. The tradeoff is that this repo now owns the DX package list and must
periodically compare it with upstream.

## What Changes From Stock Bluefin

- Adds a local DX layer for VS Code, virtualization, Cockpit, Incus/LXC,
  Podman helpers, Vulkan diagnostics, and performance diagnostics.
- Adds a small additional host package set: `clinfo`, `git-lfs`, `jq`,
  `openssl`, `ripgrep`, and `tmux`.
- Adds `/etc/npmrc` supply-chain defaults:
  - `ignore-scripts=true`
  - `save-exact=true`
  - `provenance=true`
  - `audit=true`
- Adds `ujust` recipes for AI workstation setup:
  - `ujust ai-doctor`
  - `ujust ai-gpu-doctor`
  - `ujust agent-user-status`
  - `ujust agent-user-set-password`
  - `ujust agent-user-gui-check`
  - `ujust agent-user-enter`
  - `ujust agent-container-create`
  - `ujust agent-container-enter`
  - `ujust agent-container-bootstrap-node`
  - `ujust agent-container-openclaw-install`
  - `ujust agent-container-npm-install-trusted`
  - `ujust ai-node-bootstrap`
  - `ujust openclaw-install`
  - `ujust openclaw-gateway-setup`
  - `ujust openclaw-gateway-enable`
  - `ujust openclaw-onboard-local`
  - `ujust hermes-workspace-create`
  - `ujust ramalama-bootstrap`
  - `ujust ramalama-serve`
  - `ujust ramalama-smoke`
- Adds conservative sysctl hardening that does not disable rootless containers.
- Adds a first-boot system service that creates the configured unprivileged
  agent user, defaulting to `claudex`.
- Adds a systemd user drop-in expressing the intended OpenClaw gateway bind as
  loopback-only.
- Keeps OpenClaw, Hermes, model files, npm packages, and skills out of the
  immutable OS image.

## First Boot Setup

After rebasing and rebooting:

```bash
ujust ai-doctor
ujust agent-user-status
ujust agent-user-set-password
ujust agent-user-enter
```

Run the remaining agent setup from the configured unprivileged user:

```bash
ujust agent-container-create
ujust agent-container-bootstrap-node
```

This creates a rootless Ubuntu 24.04 Distrobox for agent tooling, then prepares
Node 24 inside that container. Host-level OpenClaw and RamaLama recipes require
`fnm` and `ramalama` to be provisioned first through Bluefin's system-managed
Homebrew setup from an account allowed to install Homebrew packages. The
container path is preferred for npm-heavy agent work.

Enter the environment:

```bash
ujust agent-container-enter
```

## OpenClaw

Install OpenClaw as the dedicated agent user:

```bash
ujust ai-node-bootstrap
ujust openclaw-install
ujust openclaw-gateway-setup
ujust openclaw-gateway-enable
openclaw doctor
ss -ltnp | grep 18789
```

The host gateway path is preferred for the always-on local service because it
uses user systemd directly. The Distrobox install path remains available for
CLI experimentation, but it does not install or manage the gateway daemon:

```bash
ujust agent-container-openclaw-install
```

If OpenClaw requires npm lifecycle scripts to install correctly, use the
reviewed exception form:

```bash
ujust openclaw-install latest true
```

If the gateway reports that an existing config is missing `gateway.mode`, do
not start it with `--allow-unconfigured` for normal use. Run
`ujust openclaw-gateway-setup` to initialize or repair the baseline local
gateway config, or run `ujust openclaw-onboard-local` for the full interactive
local setup.

Expected posture:

- Unknown DMs require pairing.
- The gateway listens on loopback unless you deliberately expose it.
- Non-main sessions should use OpenClaw sandboxing:

```yaml
agents:
  defaults:
    sandbox:
      mode: "non-main"
```

Treat OpenClaw skills as executable code. Review skill contents, pin sources,
and avoid broad filesystem or network permissions for untrusted skills.

The host recipes do not install Homebrew packages as the agent user. If `fnm`
or `ramalama` is missing, install it through the system-managed Bluefin
Homebrew flow, then rerun the relevant `ujust` recipe.

## Hermes

There are multiple active projects named Hermes. This image prepares the host
for Hermes-style LLM/reasoning workloads without guessing which one you intend
to run:

- Node 24 inside the Ubuntu Distrobox for TypeScript/agent projects.
- Ubuntu Distrobox bootstrap for project-local JavaScript and Python
  dependencies.
- Python/container-friendly Bluefin base with local DX tooling for research
  repos.
- RamaLama and Podman paths for local OpenAI-compatible endpoints.
- `clinfo` for checking Framework Desktop GPU/OpenCL visibility.

For a specific Hermes repo, install it in a dedicated workspace or container,
commit lockfiles, and avoid global package installs unless the tool is a CLI you
explicitly trust.

Preferred workflow:

```bash
ujust agent-container-enter
git clone <hermes-repo>
```

## Local LLMs

Use RamaLama first for local model serving:

```bash
ujust ai-gpu-doctor
ujust ramalama-serve llama3.2 8080
```

For Framework Desktop Strix Halo, Bluefin documents Vulkan-oriented RamaLama
images as a useful option when ROCm is not the best path.

## Supply-Chain Operating Rules

- Prefer user-level or container-level installs over layering fast-moving tools
  into the bootc image.
- Use exact versions and lockfiles for projects.
- Prefer `npm ci` over `npm install` in existing projects.
- Keep npm lifecycle scripts disabled until reviewed.
- Run `npm audit signatures` inside npm projects after dependency installation.
- Prefer npm trusted publishing/provenance, but do not treat provenance as proof
  that code is safe.
- Store API keys in a real secret manager, not in shell startup files.
- Keep OpenClaw gateways and model servers local-only unless you add explicit
  auth, TLS, firewalling, and monitoring.

Docker is configurable and disabled by default. Podman and RamaLama cover the
default local container/model workflows without adding Docker's privileged
daemon/socket.

Enable Docker only for a specific workflow that cannot run under Podman:

```bash
podman build \
  --build-arg BLUEFIN_AGENT_ENABLE_DOCKER=true \
  --tag bluefin-agent-host:docker \
  .
```

Additional rationale is in [docs/SECURITY-RESEARCH.md](docs/SECURITY-RESEARCH.md).
The host-level `ujust openclaw-install` is kept as a fallback for OpenClaw
features that cannot run correctly through an integrated Distrobox. Prefer the
host gateway for the always-on service and the container path for project work.

The containerization/security tradeoffs are in
[docs/SECURITY-EVALUATION.md](docs/SECURITY-EVALUATION.md).
The operational pass/fail checks are in
[docs/FUNCTIONAL-READINESS.md](docs/FUNCTIONAL-READINESS.md).
The post-rebase and first-boot runbook is in
[docs/POST-REBASE-FIRST-BOOT.md](docs/POST-REBASE-FIRST-BOOT.md).

## Maintaining The DX Layer

The local DX layer is in [build_files/dx-layer.sh](build_files/dx-layer.sh).
It was derived from upstream Bluefin's `build_files/dx/00-dx.sh` and supporting
`system_files/dx` files. Periodically compare this file with upstream Bluefin
before major Fedora or Bluefin upgrades.

## Build

Local build:

```bash
just build
```

Custom agent username:

```bash
BLUEFIN_AGENT_USER=agentbox just build
```

Docker-enabled local build:

```bash
BLUEFIN_AGENT_ENABLE_DOCKER=true just build
```

The GitHub Actions workflow publishes the image to GHCR using the repository
name. It defaults to `BLUEFIN_AGENT_ENABLE_DOCKER=false`; change that workflow
environment value to `"true"` for Docker-enabled published images.

Agent user build variables:

- `BLUEFIN_AGENT_USER`, default `claudex`.
- `BLUEFIN_AGENT_USER_GROUPS`, default `render`.
- `BLUEFIN_AGENT_DENY_GROUPS`, default `wheel sudo docker libvirt incus-admin lxd kvm qemu mock wireshark input`.
- `BLUEFIN_AGENT_ENABLE_LINGER`, default `false`.

`video` is intentionally not in the default deny list because some GPU stacks
may still need it. It is also not granted by default. Start with `render`; if
`ujust ai-gpu-doctor` shows that local model acceleration requires `video`,
build with `BLUEFIN_AGENT_USER_GROUPS="render video"` or add it deliberately
after install.

Build-time configuration is preferred. A root-only runtime override can be
placed in `/etc/bluefin-agent/agent-user.conf`, then applied with:

```bash
sudo systemctl restart bluefin-agent-user.service
```

Configure `SIGNING_SECRET` if you want Cosign signing to succeed.

## Rebase

After the image is published:

```bash
sudo bootc switch ghcr.io/<github-user-or-org>/<repo-name>:latest
systemctl reboot
```

Verify after reboot:

```bash
bootc status
ujust ai-doctor
clinfo
```
