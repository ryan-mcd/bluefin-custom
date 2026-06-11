#!/usr/bin/bash

set -ouex pipefail

DX_PACKAGES=(
  android-tools
  bcc
  bpftop
  bpftrace
  cascadia-code-fonts
  cockpit-bridge
  cockpit-machines
  cockpit-networkmanager
  cockpit-ostree
  cockpit-podman
  cockpit-selinux
  cockpit-storaged
  cockpit-system
  dbus-x11
  distrobox
  edk2-ovmf
  flatpak-builder
  genisoimage
  git-subtree
  git-svn
  incus
  incus-agent
  iotop
  libvirt
  libvirt-nss
  lxc
  nicstat
  numactl
  osbuild-selinux
  p7zip
  p7zip-plugins
  podman-compose
  podman-machine
  podman-tui
  qemu
  qemu-char-spice
  qemu-device-display-virtio-gpu
  qemu-device-display-virtio-vga
  qemu-device-usb-redirect
  qemu-img
  qemu-system-x86-core
  qemu-user-binfmt
  qemu-user-static
  rocm-hip
  rocm-opencl
  rocm-smi
  rocminfo
  sysprof
  tiptop
  trace-cmd
  udica
  util-linux-script
  virt-manager
  virt-v2v
  virt-viewer
  vulkan-tools
  ydotool
)

dnf5 install -y "${DX_PACKAGES[@]}"

tee /etc/yum.repos.d/vscode.repo <<'EOF'
[code]
name=Visual Studio Code
baseurl=https://packages.microsoft.com/yumrepos/vscode
enabled=0
gpgcheck=1
gpgkey=https://packages.microsoft.com/keys/microsoft.asc
EOF

dnf -y install --enablerepo=code code

if [[ "${BLUEFIN_AGENT_ENABLE_DOCKER:-false}" == "true" ]]; then
  dnf config-manager addrepo --from-repofile=https://download.docker.com/linux/fedora/docker-ce.repo
  sed -i "s/enabled=.*/enabled=0/g" /etc/yum.repos.d/docker-ce.repo
  dnf -y install --enablerepo=docker-ce-stable \
    containerd.io \
    docker-buildx-plugin \
    docker-ce \
    docker-ce-cli \
    docker-compose-plugin \
    docker-model-plugin

  install -Dm0644 /ctx/docker-ce.conf /usr/lib/sysctl.d/docker-ce.conf
  install -Dm0644 /ctx/ip_tables.conf /etc/modules-load.d/ip_tables.conf
  systemctl enable docker.socket
else
  echo "Docker disabled. Set BLUEFIN_AGENT_ENABLE_DOCKER=true at build time to include it."
fi

install -Dm0755 /ctx/bluefin-dx-groups /usr/bin/bluefin-dx-groups
install -Dm0644 /ctx/bluefin-dx-groups.service /usr/lib/systemd/system/bluefin-dx-groups.service
install -Dm0644 /ctx/libvirt-workaround.service /usr/lib/systemd/system/libvirt-workaround.service
install -Dm0644 /ctx/incus-workaround.service /usr/lib/systemd/system/incus-workaround.service
install -Dm0644 /ctx/libvirt-workaround.conf /usr/lib/tmpfiles.d/libvirt-workaround.conf
install -Dm0644 /ctx/incus-workaround.conf /usr/lib/tmpfiles.d/incus-workaround.conf
install -Dm0644 /ctx/vscode-settings.json /etc/skel/.config/Code/User/settings.json

systemctl enable podman.socket
systemctl enable libvirt-workaround.service
systemctl enable bluefin-dx-groups.service

sed -i 's@enabled=1@enabled=0@g' /etc/yum.repos.d/fedora-cisco-openh264.repo || true

for repo in /etc/yum.repos.d/rpmfusion-*.repo; do
  if [[ -f "$repo" ]]; then
    sed -i 's@enabled=1@enabled=0@g' "$repo"
  fi
done
