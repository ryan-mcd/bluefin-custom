# Bluefin Agent Host

Custom Bluefin image for a Framework Desktop used to run OpenClaw, Hermes,
local LLM services, and adjacent agent workflows with conservative supply-chain
defaults. It starts from stock Bluefin and recreates the DX capabilities needed
for this host instead of depending directly on the `bluefin-dx` image tag.

The image intentionally does not preinstall fast-moving npm agent packages.
OpenClaw and similar tools should be installed as the normal user through
Homebrew's `fnm`-managed Node runtime, with npm lifecycle scripts disabled by
default.

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
  Podman helpers, and performance diagnostics.
- Adds a small additional host package set: `clinfo`, `git-lfs`, `jq`,
  `openssl`, `ripgrep`, and `tmux`.
- Adds `/etc/npmrc` supply-chain defaults:
  - `ignore-scripts=true`
  - `strict-allow-scripts=true`
  - `save-exact=true`
  - `provenance=true`
  - `audit=true`
- Adds `ujust` recipes for AI workstation setup:
  - `ujust ai-doctor`
  - `ujust ai-node-bootstrap`
  - `ujust openclaw-install`
  - `ujust openclaw-onboard-local`
  - `ujust ramalama-serve`
- Adds conservative sysctl hardening that does not disable rootless containers.
- Adds a systemd user drop-in expressing the intended OpenClaw gateway bind as
  loopback-only.
- Keeps OpenClaw, Hermes, model files, npm packages, and skills out of the
  immutable OS image.

## First Boot Setup

After rebasing and rebooting:

```bash
ujust ai-doctor
ujust ai-node-bootstrap
```

This uses Bluefin's Homebrew installation to install or activate `fnm`, then
installs Node 24 for your user.

## OpenClaw

Install OpenClaw as your normal user:

```bash
ujust openclaw-install
openclaw onboard --install-daemon
openclaw doctor
ss -ltnp | grep 18789
```

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

## Hermes

There are multiple active projects named Hermes. This image prepares the host
for Hermes-style LLM/reasoning workloads without guessing which one you intend
to run:

- Node 24 via `fnm` for TypeScript/agent projects.
- Python/container-friendly Bluefin base with local DX tooling for research
  repos.
- RamaLama and Podman paths for local OpenAI-compatible endpoints.
- `clinfo` for checking Framework Desktop GPU/OpenCL visibility.

For a specific Hermes repo, install it in a dedicated workspace or container,
commit lockfiles, and avoid global package installs unless the tool is a CLI you
explicitly trust.

## Local LLMs

Use RamaLama first for local model serving:

```bash
brew install ramalama
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

Docker-enabled local build:

```bash
BLUEFIN_AGENT_ENABLE_DOCKER=true just build
```

The GitHub Actions workflow publishes the image to GHCR using the repository
name. It defaults to `BLUEFIN_AGENT_ENABLE_DOCKER=false`; change that workflow
environment value to `"true"` for Docker-enabled published images. Configure
`SIGNING_SECRET` if you want Cosign signing to succeed.

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
