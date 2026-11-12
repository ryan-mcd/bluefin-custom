#!/bin/bash

set -ouex pipefail

#### Supply-chain and agent-runtime defaults

: "${BLUEFIN_AGENT_USER:=claudex}"
: "${BLUEFIN_AGENT_USER_GROUPS:=render}"
: "${BLUEFIN_AGENT_DENY_GROUPS:=wheel sudo docker libvirt incus-admin lxd kvm qemu mock wireshark input}"
: "${BLUEFIN_AGENT_ENABLE_LINGER:=true}"

if [[ ! "$BLUEFIN_AGENT_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo "Invalid BLUEFIN_AGENT_USER: $BLUEFIN_AGENT_USER" >&2
  exit 1
fi

for value_name in BLUEFIN_AGENT_USER_GROUPS BLUEFIN_AGENT_DENY_GROUPS; do
  value="${!value_name}"
  if [[ ! "$value" =~ ^[-A-Za-z0-9_[:space:]]*$ ]]; then
    echo "Invalid $value_name: $value" >&2
    exit 1
  fi
done

case "$BLUEFIN_AGENT_ENABLE_LINGER" in
  [Tt][Rr][Uu][Ee]|[Ff][Aa][Ll][Ss][Ee]|[Yy][Ee][Ss]|[Nn][Oo]|1|0) ;;
  *) echo "BLUEFIN_AGENT_ENABLE_LINGER must be true or false." >&2; exit 1 ;;
esac


### Install packages

# Packages can be installed from any enabled yum repo on the image.
# RPMfusion repos are available by default in ublue main images
# List of rpmfusion packages can be found here:
# https://mirrors.rpmfusion.org/mirrorlist?path=free/fedora/updates/43/x86_64/repoview/index.html&protocol=https&redirect=1

# Keep host packages small. Language runtimes and fast-moving agent CLIs are
# installed in the user's home via Homebrew/fnm or containers.
dnf5 install -y \
  clinfo \
  git-lfs \
  jq \
  openssl \
  ripgrep \
  tmux

/usr/bin/bash /ctx/dx-layer.sh

# Refresh inherited packages with known available security fixes. Keep the
# base image's kernel and its matching Bluefin kernel modules together.
# Reassess this list against the image scan on each base upgrade.
dnf5 upgrade -y \
  python3-pillow \
  libsoup3 \
  glib-networking \
  python3


install -Dm0644 /ctx/npmrc /etc/npmrc
install -Dm0644 /ctx/npmrc /usr/share/bluefin-agent/npmrc
install -Dm0644 /ctx/deployment.conf /usr/share/bluefin-agent/deployment.conf
source /ctx/hermes-release.conf
if [[ ! "$HERMES_RELEASE_TAG" =~ ^v[0-9][0-9A-Za-z._-]*$ ]]; then
  echo "Invalid HERMES_RELEASE_TAG: $HERMES_RELEASE_TAG" >&2
  exit 1
fi
if [[ ! "$HERMES_RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid HERMES_RELEASE_COMMIT: $HERMES_RELEASE_COMMIT" >&2
  exit 1
fi
if [[ ! "$HERMES_INSTALLER_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "Invalid HERMES_INSTALLER_SHA256: $HERMES_INSTALLER_SHA256" >&2
  exit 1
fi
install -Dm0644 /ctx/hermes-release.conf /usr/share/bluefin-agent/hermes-release.conf
install -d /etc/bluefin-agent /usr/share/bluefin-agent
cat >/usr/share/bluefin-agent/agent-user.conf <<EOF
BLUEFIN_AGENT_USER="$BLUEFIN_AGENT_USER"
BLUEFIN_AGENT_USER_GROUPS="$BLUEFIN_AGENT_USER_GROUPS"
BLUEFIN_AGENT_DENY_GROUPS="$BLUEFIN_AGENT_DENY_GROUPS"
BLUEFIN_AGENT_ENABLE_LINGER="$BLUEFIN_AGENT_ENABLE_LINGER"
EOF
install -Dm0644 /ctx/ai-agents.just /usr/share/ublue-os/just/60-custom.just
# Bluefin versions use different ujust entry files. Prefer the current entry
# and preserve older images that still use the legacy location.
if [[ -f /usr/share/ublue-os/just/00-entry.just ]]; then
  ujust_entry=/usr/share/ublue-os/just/00-entry.just
elif [[ -f /usr/share/ublue-os/justfile ]]; then
  ujust_entry=/usr/share/ublue-os/justfile
else
  echo "Cannot find Bluefin's ujust entry file." >&2
  exit 1
fi
if ! grep -Fq '/usr/share/ublue-os/just/60-custom.just' "$ujust_entry"; then
  cat >>"$ujust_entry" <<'EOF'

# Bluefin agent custom recipes
import "/usr/share/ublue-os/just/60-custom.just"
EOF
fi
install -Dm0644 /ctx/agent-ubuntu.ini /usr/share/bluefin-agent/distrobox/agent-ubuntu.ini
install -Dm0755 /ctx/bluefin-agent-ubuntu-setup /usr/bin/bluefin-agent-ubuntu-setup
install -Dm0755 /ctx/bluefin-openclaw-run /usr/bin/bluefin-openclaw-run
install -Dm0755 /ctx/bluefin-hermes-run /usr/bin/bluefin-hermes-run
install -Dm0755 /ctx/bluefin-ai-profile /usr/bin/bluefin-ai-profile
install -Dm0755 /ctx/bluefin-strata-run /usr/bin/bluefin-strata-run
install -Dm0755 /ctx/bluefin-strata-proxy /usr/bin/bluefin-strata-proxy
install -Dm0755 /ctx/bluefin-strata-check /usr/bin/bluefin-strata-check
install -Dm0755 /ctx/bluefin-strata-prepare /usr/bin/bluefin-strata-prepare
install -Dm0755 /ctx/bluefin-strata-service-enable /usr/bin/bluefin-strata-service-enable
install -Dm0755 /ctx/bluefin-model-switch /usr/bin/bluefin-model-switch
install -Dm0755 /ctx/bluefin-model-benchmark /usr/bin/bluefin-model-benchmark
install -Dm0755 /ctx/bluefin-ai-dashboard /usr/bin/bluefin-ai-dashboard
for asset in index.html style.css app.js; do
  install -Dm0644 "/ctx/dashboard/$asset" "/usr/share/bluefin-agent/dashboard/$asset"
done
install -Dm0755 /ctx/bluefin-ai-firewall /usr/bin/bluefin-ai-firewall
install -Dm0755 /ctx/bluefin-model-gateway /usr/bin/bluefin-model-gateway
install -Dm0755 /ctx/bluefin-ramalama-quadlet-fix /usr/bin/bluefin-ramalama-quadlet-fix
install -Dm0755 /ctx/bluefin-agent-user /usr/bin/bluefin-agent-user
install -Dm0644 /ctx/bluefin-agent-user.service /usr/lib/systemd/system/bluefin-agent-user.service
install -Dm0644 /ctx/openclaw-gateway.service /etc/systemd/user/openclaw-gateway.service
install -Dm0644 /ctx/hermes-gateway.service /etc/systemd/user/bluefin-hermes-gateway.service
install -Dm0644 /ctx/hermes-serve.service /etc/systemd/user/bluefin-hermes-serve.service
install -Dm0644 /ctx/bluefin-model-gateway.service /etc/systemd/user/bluefin-model-gateway.service
install -Dm0644 /ctx/bluefin-strata.service /etc/systemd/user/bluefin-strata.service
install -Dm0644 /ctx/bluefin-strata-proxy.service /etc/systemd/user/bluefin-strata-proxy.service
install -Dm0644 /ctx/sysctl-ai-agent.conf /etc/sysctl.d/90-ai-agent-workstation.conf
install -Dm0644 /ctx/openclaw-gateway-localhost.conf /etc/systemd/user/openclaw-gateway.service.d/10-localhost-defaults.conf

test -x /usr/bin/bluefin-agent-user
test -x /usr/bin/bluefin-agent-ubuntu-setup
test -x /usr/bin/bluefin-openclaw-run
test -x /usr/bin/bluefin-hermes-run
test -x /usr/bin/bluefin-ai-profile
test -x /usr/bin/bluefin-model-switch
test -x /usr/bin/bluefin-model-benchmark
test -x /usr/bin/bluefin-strata-run
test -x /usr/bin/bluefin-strata-prepare
test -x /usr/bin/bluefin-strata-proxy
test -x /usr/bin/bluefin-strata-check
test -x /usr/bin/bluefin-strata-service-enable
test -x /usr/bin/bluefin-ai-dashboard
test -f /usr/share/bluefin-agent/dashboard/index.html
test -x /usr/bin/bluefin-ai-firewall
test -x /usr/bin/bluefin-model-gateway
test -x /usr/bin/bluefin-ramalama-quadlet-fix
test -f /usr/share/ublue-os/just/60-custom.just
test -f /usr/share/bluefin-agent/hermes-release.conf
test -f /etc/systemd/user/bluefin-hermes-gateway.service
test -f /etc/systemd/user/bluefin-hermes-serve.service
test -f /etc/systemd/user/bluefin-model-gateway.service
test -f /etc/systemd/user/bluefin-strata.service
test -f /etc/systemd/user/bluefin-strata-proxy.service
ujust_recipes="$(/usr/bin/ujust --summary)"
for recipe in ai-dashboard ai-doctor ai-model-list ai-model-benchmark ai-model-select ai-profile-set ai-profile-status hermes-release hermes-install hermes-doctor hermes-gateway-enable hermes-lan-enable hermes-local-smoke; do
  if ! tr ' ' '\n' <<<"$ujust_recipes" | grep -Fxq "$recipe"; then
    echo "Custom recipe $recipe is not visible through ujust." >&2
    echo "$ujust_recipes" >&2
    exit 1
  fi
done

#### Example for enabling a System Unit File

systemctl enable podman.socket
systemctl enable bluefin-agent-user.service
