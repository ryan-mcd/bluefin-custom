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

install -Dm0644 /ctx/npmrc /etc/npmrc
install -Dm0644 /ctx/ai-agents.just /usr/share/ublue-os/just/90-ai-agents.just
install -Dm0644 /ctx/sysctl-ai-agent.conf /etc/sysctl.d/90-ai-agent-workstation.conf
install -Dm0644 /ctx/openclaw-gateway-localhost.conf /etc/systemd/user/openclaw-gateway.service.d/10-localhost-defaults.conf

#### Example for enabling a System Unit File

systemctl enable podman.socket
