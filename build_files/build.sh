#!/bin/bash

set -ouex pipefail

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

#### Supply-chain and agent-runtime defaults

: "${BLUEFIN_AGENT_USER:=claudex}"
: "${BLUEFIN_AGENT_USER_GROUPS:=render}"
: "${BLUEFIN_AGENT_DENY_GROUPS:=wheel sudo docker libvirt incus-admin lxd kvm qemu mock wireshark input}"
: "${BLUEFIN_AGENT_ENABLE_LINGER:=false}"

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

install -Dm0644 /ctx/npmrc /etc/npmrc
install -Dm0644 /ctx/npmrc /usr/share/bluefin-agent/npmrc
install -d /etc/bluefin-agent /usr/share/bluefin-agent
cat >/usr/share/bluefin-agent/agent-user.conf <<EOF
BLUEFIN_AGENT_USER="$BLUEFIN_AGENT_USER"
BLUEFIN_AGENT_USER_GROUPS="$BLUEFIN_AGENT_USER_GROUPS"
BLUEFIN_AGENT_DENY_GROUPS="$BLUEFIN_AGENT_DENY_GROUPS"
BLUEFIN_AGENT_ENABLE_LINGER="$BLUEFIN_AGENT_ENABLE_LINGER"
EOF
install -Dm0644 /ctx/ai-agents.just /usr/share/ublue-os/just/90-ai-agents.just
install -Dm0644 /ctx/agent-ubuntu.ini /usr/share/bluefin-agent/distrobox/agent-ubuntu.ini
install -Dm0755 /ctx/bluefin-agent-ubuntu-setup /usr/local/bin/bluefin-agent-ubuntu-setup
install -Dm0755 /ctx/bluefin-openclaw-run /usr/local/bin/bluefin-openclaw-run
install -Dm0755 /ctx/bluefin-agent-user /usr/local/bin/bluefin-agent-user
install -Dm0644 /ctx/bluefin-agent-user.service /usr/lib/systemd/system/bluefin-agent-user.service
install -Dm0644 /ctx/openclaw-gateway.service /etc/systemd/user/openclaw-gateway.service
install -Dm0644 /ctx/sysctl-ai-agent.conf /etc/sysctl.d/90-ai-agent-workstation.conf
install -Dm0644 /ctx/openclaw-gateway-localhost.conf /etc/systemd/user/openclaw-gateway.service.d/10-localhost-defaults.conf

#### Example for enabling a System Unit File

systemctl enable podman.socket
systemctl enable bluefin-agent-user.service
