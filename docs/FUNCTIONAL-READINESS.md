# Functional Readiness

Security defaults are only useful if the host can still run the intended
workloads. This file records the operational decisions for OpenClaw, Hermes, and
local model serving.

## Agent User

Decision: run agent work as a dedicated unprivileged user. The default username
is `claudex`, configured at build time with `BLUEFIN_AGENT_USER`.

The image creates this account on first boot and removes memberships in
admin-equivalent groups such as `wheel`, `sudo`, `docker`, `libvirt`,
`incus-admin`, `lxd`, `kvm`, `qemu`, `mock`, `wireshark`, and `input`.
The default allowed supplemental group is `render` for GPU/model workloads.
`video` is optional for stacks that prove they need it.

Validation:

```bash
ujust agent-user-status
sudo bluefin-agent-user status
ujust agent-user-gui-check
ujust agent-user-set-password
ujust agent-user-enter
bluefin-agent-user require
```

The account is created as a regular local user suitable for GDM, but with a
locked password until an admin sets one. Do not bake a password into the image.
For GNOME login, `input` group membership is not required because the active
seat is handled by `systemd-logind`. Use `render` by default and add `video`
only when GPU diagnostics prove it is needed.

Run OpenClaw, Hermes, RamaLama, and Distrobox setup from that user. The agent
recipes enforce this with `bluefin-agent-user require`.

## OpenClaw

Decision: keep the Ubuntu Distrobox path for CLI experimentation, but provide a
host-level OpenClaw gateway service for the always-on desktop/session component.

Why:

- Distrobox is a good place for npm churn, project checkouts, and disposable
  experiments.
- A user systemd gateway is more likely to work reliably on the host because it
  needs stable access to the user's session, browser/web interfaces, local
  ports, and systemd.

Default secure install:

```bash
ujust openclaw-install
ujust openclaw-gateway-enable
```

If OpenClaw needs npm lifecycle scripts during install:

```bash
ujust openclaw-install latest true
ujust openclaw-gateway-enable
```

Container install remains available:

```bash
ujust agent-container-openclaw-install
ujust agent-container-openclaw-install latest true
```

The container path is CLI/project-only. It intentionally does not install or
manage the OpenClaw gateway daemon. Prefer the host gateway for normal
operation.

Validation:

```bash
ujust ai-doctor
systemctl --user status openclaw-gateway.service
ss -ltnp | grep 18789
```

The gateway service uses `/usr/bin/bluefin-openclaw-run`, which sources
Homebrew and `fnm` before executing `openclaw`. This avoids depending on an
interactive shell PATH inside systemd.

## npm Lifecycle Scripts

Decision: keep lifecycle scripts disabled globally, but add explicit reviewed
install paths that enable scripts for a single command.

Default:

```bash
npm install --ignore-scripts ...
```

Reviewed exception:

```bash
ujust agent-container-npm-install-trusted <package> <version>
```

The exception sets `--ignore-scripts=false` only for that command. Use it only
after inspecting the package metadata and source. This is less convenient, but
it prevents the common case where transitive packages run code during install
without review.

## Local Models

Decision: keep model runtimes user-managed, but make host GPU diagnostics first
class.

The host includes OpenCL, ROCm, and Vulkan diagnostic tooling. Run:

```bash
ujust ai-gpu-doctor
```

Install and test RamaLama:

```bash
ujust ramalama-smoke llama3.2
```

Use this result to decide whether the Framework Desktop should use Vulkan,
ROCm/OpenCL, CPU, or a containerized runtime image for a specific model.

## Hermes

Decision: do not bake an unspecified Hermes project into the image. There are
multiple active projects named Hermes, with different runtime expectations.

The image prepares the right environment:

- Ubuntu 24.04 Distrobox for mutable project dependencies.
- Node 24 through `fnm`, provisioned outside the agent recipes.
- Python, build tools, Git LFS, and common native build dependencies.
- Host GPU diagnostics and model-serving hooks.

Create a workspace:

```bash
ujust hermes-workspace-create
ujust agent-container-enter
cd ~/src/hermes-workspace
git clone <hermes-repo>
```

Once the exact Hermes repo is chosen, add a repo-specific recipe that installs
from a lockfile and runs its real smoke test.

## Pass Criteria

The image should not be considered operationally proven until these pass on the
Framework Desktop:

```bash
ujust ai-doctor
ujust ai-gpu-doctor
ujust agent-user-status
ujust agent-user-enter
ujust agent-container-create
ujust agent-container-bootstrap-node
ujust openclaw-install
ujust openclaw-gateway-enable
ujust ramalama-smoke llama3.2
```

If OpenClaw requires lifecycle scripts, repeat the install with:

```bash
ujust openclaw-install latest true
```
