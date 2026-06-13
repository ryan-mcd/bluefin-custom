# Post-Rebase First Boot Guide

This guide covers switching an existing Bluefin Framework Desktop install to
this custom image, validating the first boot, starting OpenClaw and Hermes work,
and locking down the exposed surfaces.

Replace the image reference below with the image built by this repository:

```bash
export IMAGE_REF="ghcr.io/<github-user-or-org>/<repo-name>:latest"
```

For routine use, prefer a dated or otherwise immutable tag after you have one
that passes validation. `latest` is convenient for testing, but it is mutable.

## Before Rebasing

Confirm the currently deployed system and keep a rollback path:

```bash
bootc status || rpm-ostree status
```

Verify the image exists and inspect the digest:

```bash
podman pull "${IMAGE_REF}"
podman image inspect "${IMAGE_REF}" --format '{{.Digest}}'
```

If the image was signed with the repository Cosign key, verify it before
switching:

```bash
cosign verify --key ./cosign.pub "${IMAGE_REF}"
```

Do not treat this as policy enforcement unless you have also configured
container signature policy on the host. It is still a useful preflight check.

Recommended preflight:

```bash
ujust ai-doctor || true
ujust ai-gpu-doctor || true
systemctl status docker docker.socket --no-pager || true
systemctl status cockpit.socket --no-pager || true
```

## Rebase

Use `bootc` if it is available:

```bash
sudo bootc switch "${IMAGE_REF}"
systemctl reboot
```

If this Bluefin install still uses the older rpm-ostree rebase flow:

```bash
sudo rpm-ostree rebase "ostree-unverified-registry:${IMAGE_REF}"
systemctl reboot
```

After reboot, confirm the new deployment:

```bash
bootc status || rpm-ostree status
cat /etc/os-release
ujust ai-doctor
ujust ai-gpu-doctor
```

If `ujust --list` shows the custom recipes but they fail because
`bluefin-agent-user` is missing, the system is running an image that installed
image-owned helpers into `/usr/local/bin`. On Bluefin/Fedora atomic systems,
`/usr/local` is local mutable state and can hide files baked into the image.
Rebuild and rebase to an image where these helpers live in `/usr/bin`:

```bash
ls -l /usr/bin/bluefin-agent-user
ls -l /usr/bin/bluefin-openclaw-run
ls -l /usr/bin/bluefin-agent-ubuntu-setup
```

Confirm the dedicated agent user was created and is unprivileged:

```bash
ujust agent-user-status
ujust agent-user-gui-check
id claudex
```

If you changed `BLUEFIN_AGENT_USER` at build time, replace `claudex` with that
configured username or run:

```bash
bluefin-agent-user name
```

Build-time configuration is preferred. If you must change the account after
install, write a root-owned override in `/etc/bluefin-agent/agent-user.conf` and
restart `bluefin-agent-user.service`. This creates or constrains the newly
configured account, but it does not delete any old account or migrate its home
directory.

If the boot fails or the image is not usable, choose the previous deployment in
the boot menu. From a working booted system, use the rollback command available
on that install:

```bash
sudo bootc rollback || sudo rpm-ostree rollback
systemctl reboot
```

## First Boot Setup

Agent work should run as the configured unprivileged user, `claudex` by default.
The account is created as a regular local user before GDM starts, but its
password is locked until you set one from an admin account. This is intentional:
the image should not ship with a password or password hash.
The generated account should have UID 1000 or higher. If `ujust
agent-user-status` warns about a UID below 1000, recreate the account as a
normal user before expecting GDM to list it.

Enable GNOME login:

```bash
ujust agent-user-set-password
```

Then log out, switch user, or use GDM's "Not listed?" flow and sign in as the
configured username. If you changed the build-time username, get it with:

```bash
bluefin-agent-user name
```

From an admin shell, you can still enter the user without a separate GUI login:

```bash
ujust agent-user-enter
```

Before relying on host-level OpenClaw or RamaLama recipes, provision the host
Homebrew packages from an account allowed to manage Bluefin's Homebrew setup.
The agent recipes can use these packages, but they intentionally do not install
or upgrade Homebrew packages as the unprivileged agent user:

```bash
if command -v brew >/dev/null 2>&1; then
  eval "$(brew shellenv)"
elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
  eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
fi
brew install fnm ramalama
```

Confirm the configured agent user can see the host-managed tools:

```bash
sudo -iu "$(bluefin-agent-user name)" bash -lc '
if command -v brew >/dev/null 2>&1; then
  eval "$(brew shellenv)"
elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
  eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
fi
command -v fnm
command -v ramalama
'
```

The account has no privileged group memberships.
Do not add this user to `wheel`, `sudo`, `docker`, `libvirt`, `incus-admin`,
`lxd`, `kvm`, `qemu`, `mock`, `wireshark`, or `input` unless you are
intentionally changing the threat model. The default supplemental group is
`render` so local GPU/model workloads can use render devices without granting
admin-equivalent access.

For a local GNOME login, `input` group membership is not required. The active
seat gets device access through `systemd-logind`; adding persistent `input`
membership would give the account raw input access outside the normal graphical
session boundary.

`video` is handled differently: do not grant it automatically, but do not treat
it as forbidden. Some GPU stacks still require `video` for acceleration. Start
without it, run `ujust ai-gpu-doctor`, and only add it if diagnostics or the
model runtime prove it is needed:

```bash
sudo usermod -aG video "$(bluefin-agent-user name)"
```

For a build-time default:

```bash
BLUEFIN_AGENT_USER_GROUPS="render video" just build
```

The agent account does not enable systemd linger by default. If OpenClaw or
another user service must keep running after the agent user logs out, enable
linger deliberately:

```bash
sudo loginctl enable-linger "$(bluefin-agent-user name)"
```

You can also build that default into the image with
`BLUEFIN_AGENT_ENABLE_LINGER=true`.

From the agent user, create the Ubuntu 24.04 Distrobox environment used for
high-churn agent and project dependencies:

```bash
ujust agent-container-create
ujust agent-container-bootstrap-node
ujust agent-container-enter
ujust ai-gpu-doctor
```

Inside the container, confirm Node and npm policy:

```bash
node --version
npm --version
npm config get ignore-scripts
npm config get save-exact
```

Expected defaults:

- `ignore-scripts` should be `true`.
- `save-exact` should be `true`.

Use Distrobox for Hermes project checkouts, npm experiments, Python tooling, and
agent CLI testing. Treat it as an operational boundary, not a strong sandbox:
the default container intentionally integrates with your home directory,
display session, SSH agent, devices, and host command paths.

## OpenClaw Startup

The supported always-on OpenClaw gateway path is host-level user systemd. Install
OpenClaw as the dedicated agent user after the host `fnm` prerequisite above has
been verified:

```bash
ujust ai-node-bootstrap
ujust openclaw-install
ujust openclaw-gateway-setup
ujust openclaw-gateway-enable
/usr/bin/bluefin-openclaw-run doctor
systemctl --user --no-pager --full status openclaw-gateway.service
ss -ltnp | grep ':18789'
```

Open a new shell before relying on `openclaw` being directly on `PATH`. The
`/usr/bin/bluefin-openclaw-run` wrapper is the stable path used by the
systemd user service because it initializes Homebrew and `fnm` first.

If OpenClaw fails to install because it requires npm lifecycle scripts, review
the package metadata and source, then use the explicit exception form:

```bash
npm view openclaw@latest name version dist.integrity dist.tarball --json
ujust openclaw-install latest true
ujust openclaw-gateway-enable
```

If the gateway service fails with a missing `gateway.mode`, treat the config as
incomplete or clobbered rather than bypassing the guardrail:

```bash
ujust openclaw-gateway-setup
systemctl --user reset-failed openclaw-gateway.service
ujust openclaw-gateway-enable
```

If you want the full guided setup instead of the baseline config repair, run:

```bash
ujust openclaw-onboard-local
```

Use the host onboarding recipe, not the Distrobox path. The image provides the
systemd user service, so this recipe runs local onboarding without asking
OpenClaw to install a second daemon.

The Distrobox OpenClaw install is for CLI experimentation only:

```bash
ujust agent-container-openclaw-install
```

## Hermes Startup

There are multiple active projects named Hermes, so this image does not bake one
specific Hermes repo into the OS. Run this as the dedicated agent user, start
from the prepared workspace, and install the exact project you intend to run:

```bash
ujust hermes-workspace-create
ujust agent-container-enter
cd ~/src/hermes-workspace
git clone <hermes-repo-url> hermes
cd hermes
```

For JavaScript or TypeScript Hermes projects, prefer lockfile-based installs:

```bash
npm ci --ignore-scripts
npm audit signatures || true
```

Only enable lifecycle scripts for a reviewed dependency that actually needs
them:

```bash
ujust agent-container-npm-install-trusted <package> <version>
```

For Python Hermes projects, use a project-local virtual environment inside the
container:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Before granting broad filesystem, shell, browser, or network access to a Hermes
agent, run its smallest smoke test and inspect its dependency lockfiles.

## Local Model Startup

Start with RamaLama and the GPU diagnostics shipped by the image:

```bash
ujust ai-gpu-doctor
ujust ramalama-smoke llama3.2
```

Serve a local OpenAI-compatible endpoint:

```bash
ujust ramalama-serve llama3.2 8080
ss -ltnp | grep ':8080'
```

Confirm model endpoints bind only to loopback before using them with agents. If
you see `0.0.0.0:8080` or another non-loopback bind, stop the service and add an
explicit local-only bind option for that model server, or block the port at the
firewall before continuing.

## Lockdown Checklist

Run this once after the first successful boot, then repeat after major image
updates or agent changes.

### Agent User Boundary

Verify the configured agent user and fail closed if it picked up privileged
groups:

```bash
ujust agent-user-status
sudo bluefin-agent-user status
bluefin-agent-user require
```

The first command can be run from any account. The root-level status check can
also detect direct sudoers entries. The `require` command should pass only when
run as the configured agent user and should fail from your admin account.

The host-level agent recipes intentionally call `bluefin-agent-user require`.
If a recipe says to run as `claudex`, switch users instead of relaxing the
recipe:

```bash
ujust agent-user-enter
```

### Network Exposure

List listening TCP ports:

```bash
ss -ltnp
```

The normal first-pass posture should be:

- OpenClaw gateway: loopback only, usually `127.0.0.1:18789`.
- Local model server: loopback only unless deliberately exposed.
- Cockpit: disabled unless you need remote browser administration.
- SSH: disabled unless you need remote shell access.

Disable Cockpit if you do not need it:

```bash
sudo systemctl disable --now cockpit.socket
sudo firewall-cmd --remove-service=cockpit --permanent || true
sudo firewall-cmd --reload || true
```

Disable SSH if you do not need it:

```bash
systemctl list-unit-files 'sshd*'
sudo systemctl disable --now sshd.service sshd.socket
```

Do not disable SSH from an SSH-only session unless you already have local
console access.

If firewalld is active, inspect exposed services and ports:

```bash
sudo firewall-cmd --state
sudo firewall-cmd --get-active-zones || true
sudo firewall-cmd --list-all || true
```

Remove accidental agent/model ports from the permanent firewall config:

```bash
sudo firewall-cmd --remove-port=18789/tcp --permanent || true
sudo firewall-cmd --remove-port=8080/tcp --permanent || true
sudo firewall-cmd --reload || true
```

### OpenClaw Gateway

Verify the service and environment:

```bash
systemctl --user cat openclaw-gateway.service
systemctl --user show openclaw-gateway.service -p Environment
ss -ltnp | grep ':18789'
```

If it is not loopback-only, create a user override:

```bash
mkdir -p ~/.config/systemd/user/openclaw-gateway.service.d
cat > ~/.config/systemd/user/openclaw-gateway.service.d/20-localhost.conf <<'EOF'
[Service]
Environment=HOST=127.0.0.1
Environment=OPENCLAW_HOST=127.0.0.1
Environment=OPENCLAW_GATEWAY_HOST=127.0.0.1
Environment=OPENCLAW_GATEWAY_PORT=18789
EOF
systemctl --user daemon-reload
systemctl --user restart openclaw-gateway.service
```

Keep OpenClaw pairing enabled for unknown DMs, review all skills as executable
code, and avoid giving untrusted skills broad filesystem or network access.

### Docker, Podman, and Virtualization

Docker is disabled by default in this image. Confirm it stayed that way:

```bash
systemctl status docker.service docker.socket --no-pager || true
getent group docker || true
```

If Docker was enabled for testing and you no longer need it:

```bash
sudo systemctl disable --now docker.service docker.socket
```

Podman, libvirt, Incus, and Cockpit are powerful local administration surfaces.
Keep group membership tight:

```bash
id
getent group libvirt
getent group incus-admin || true
```

Only add the daily user to these groups when the workflow actually needs it.
Do not add the configured agent user to them.

Check for direct sudoers entries before treating the user as isolated:

```bash
sudo grep -R "^[[:space:]]*$(bluefin-agent-user name)[[:space:]]" /etc/sudoers /etc/sudoers.d 2>/dev/null || true
```

### npm and Project Dependencies

On the host and inside the Ubuntu agent container:

```bash
npm config get ignore-scripts
npm config get save-exact
```

Use exact versions and lockfiles. Prefer:

```bash
npm ci --ignore-scripts
npm audit signatures || true
```

Avoid global npm installs except for reviewed CLIs. When a package requires
lifecycle scripts, enable them only for that one reviewed command.

### Secrets

Do not store long-lived API keys in `.bashrc`, `.profile`, shell history, repo
files, or files shared into Distrobox by default. Prefer a password manager,
desktop Secret Service, or an agent-specific encrypted secret store.

Check for obvious accidental secrets before committing project changes:

```bash
git status --short
git diff --cached
```

### VS Code and Browser Access

Keep VS Code workspace trust enabled. Treat extension installs like package
installs: prefer known publishers, pin where practical, and remove extensions
that are not needed for the agent host.

For web interfaces, prefer `127.0.0.1` URLs and SSH tunnels over public binds.
If a tool must listen on the LAN, add authentication, TLS or a trusted tunnel,
firewall rules, and a documented reason for the exposure.

## Operational Pass Criteria

Do not consider the rebase complete until these pass on the Framework Desktop:

```bash
bootc status || rpm-ostree status
ujust ai-doctor
ujust ai-gpu-doctor
ujust agent-user-status
ujust agent-user-enter
ujust agent-container-create
ujust agent-container-bootstrap-node
ujust ai-node-bootstrap
ujust openclaw-install
ujust openclaw-gateway-setup
ujust openclaw-gateway-enable
/usr/bin/bluefin-openclaw-run doctor
ujust ramalama-smoke llama3.2
ss -ltnp
```

Record the working image digest:

```bash
podman image inspect "${IMAGE_REF}" --format '{{.Digest}}'
```

Use that digest or a dated tag for the next planned rebase instead of blindly
tracking `latest`.
