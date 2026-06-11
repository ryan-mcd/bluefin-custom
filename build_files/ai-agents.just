[group('AI agent host')]
ai-doctor:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "== core tools =="
    for cmd in brew fnm node npm podman docker distrobox ramalama openclaw clinfo rocminfo rocm-smi vulkaninfo; do
      if command -v "${cmd}" >/dev/null 2>&1; then
        printf "%-10s %s\n" "${cmd}" "$(command -v "${cmd}")"
      else
        printf "%-10s missing\n" "${cmd}"
      fi
    done

    echo
    echo "== agent container =="
    if command -v distrobox >/dev/null 2>&1; then
      distrobox list | grep -E 'bluefin-agent-ubuntu' || true
    else
      echo "distrobox missing"
    fi

    echo
    echo "== npm policy =="
    if command -v npm >/dev/null 2>&1; then
      npm config get registry
      npm config get ignore-scripts
      npm config get strict-allow-scripts
      npm config get save-exact
    else
      echo "npm missing; run: ujust ai-node-bootstrap"
    fi

    echo
    echo "== listening ports =="
    ss -ltnp 2>/dev/null | grep -E '(:18789|:8080|:12434)' || true

[group('AI agent host')]
ai-gpu-doctor:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "== pci gpu devices =="
    if command -v lspci >/dev/null 2>&1; then
      lspci | grep -Ei 'vga|3d|display|amd|ati' || true
    else
      echo "lspci missing"
    fi

    echo
    echo "== OpenCL =="
    if command -v clinfo >/dev/null 2>&1; then
      clinfo -l || clinfo || true
    else
      echo "clinfo missing"
    fi

    echo
    echo "== ROCm =="
    command -v rocminfo >/dev/null 2>&1 && rocminfo | grep -E 'Name:|Marketing Name|gfx' | head -40 || echo "rocminfo missing or failed"
    command -v rocm-smi >/dev/null 2>&1 && rocm-smi || echo "rocm-smi missing or failed"

    echo
    echo "== Vulkan =="
    command -v vulkaninfo >/dev/null 2>&1 && vulkaninfo --summary || echo "vulkaninfo missing or failed"

[group('AI agent host')]
agent-container-create:
    #!/usr/bin/env bash
    set -euo pipefail

    distrobox assemble create --file /usr/share/bluefin-agent/distrobox/agent-ubuntu.ini

[group('AI agent host')]
agent-container-enter:
    #!/usr/bin/env bash
    set -euo pipefail

    distrobox enter bluefin-agent-ubuntu

[group('AI agent host')]
agent-container-bootstrap-node:
    #!/usr/bin/env bash
    set -euo pipefail

    distrobox enter bluefin-agent-ubuntu -- /run/host/usr/local/bin/bluefin-agent-ubuntu-setup --install-node

[group('AI agent host')]
agent-container-openclaw-install VERSION="latest" ALLOW_SCRIPTS="false":
    #!/usr/bin/env bash
    set -euo pipefail

    if [[ "{{ ALLOW_SCRIPTS }}" == "true" ]]; then
      script_flag="--ignore-scripts=false --strict-allow-scripts=false"
      echo "Installing with npm lifecycle scripts enabled for this command."
    else
      script_flag="--ignore-scripts"
    fi

    distrobox enter bluefin-agent-ubuntu -- bash -lc 'source /etc/profile.d/bluefin-agent.sh; if ! command -v node >/dev/null 2>&1; then /run/host/usr/local/bin/bluefin-agent-ubuntu-setup --install-node; source /etc/profile.d/bluefin-agent.sh; fi; npm view "openclaw@{{ VERSION }}" name version dist.integrity dist.tarball --json; npm install --global '"${script_flag}"' "openclaw@{{ VERSION }}"; openclaw --version || true'

[group('AI agent host')]
agent-container-openclaw-onboard:
    #!/usr/bin/env bash
    set -euo pipefail

    agent-openclaw onboard --install-daemon

[group('AI agent host')]
agent-container-npm-install-trusted PACKAGE VERSION="latest":
    #!/usr/bin/env bash
    set -euo pipefail

    echo "This enables npm lifecycle scripts for one reviewed package install inside the Ubuntu agent container."
    distrobox enter bluefin-agent-ubuntu -- bash -lc 'source /etc/profile.d/bluefin-agent.sh; npm view "{{ PACKAGE }}@{{ VERSION }}" name version dist.integrity dist.tarball --json; npm install --global --ignore-scripts=false --strict-allow-scripts=false "{{ PACKAGE }}@{{ VERSION }}"'

[group('AI agent host')]
ai-node-bootstrap NODE_VERSION="24":
    #!/usr/bin/env bash
    set -euo pipefail

    if ! command -v brew >/dev/null 2>&1; then
      echo "Homebrew is required and should be provided by Bluefin."
      exit 1
    fi

    brew list fnm >/dev/null 2>&1 || brew install fnm
    eval "$(fnm env --shell bash)"
    fnm install "{{ NODE_VERSION }}"
    fnm default "{{ NODE_VERSION }}"

    echo "Node: $(node --version)"
    echo "npm:  $(npm --version)"
    echo "System npm policy: ignore-scripts=$(npm config get ignore-scripts), strict-allow-scripts=$(npm config get strict-allow-scripts)"

[group('AI agent host')]
openclaw-install VERSION="latest" ALLOW_SCRIPTS="false":
    #!/usr/bin/env bash
    set -euo pipefail

    if ! command -v fnm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
      ujust ai-node-bootstrap
      eval "$(fnm env --shell bash)"
    fi

    echo "Inspecting package metadata before install:"
    npm view "openclaw@{{ VERSION }}" name version dist.integrity dist.tarball --json
    if [[ "{{ ALLOW_SCRIPTS }}" == "true" ]]; then
      npm install --global --ignore-scripts=false --strict-allow-scripts=false "openclaw@{{ VERSION }}"
    else
      npm install --global --ignore-scripts "openclaw@{{ VERSION }}"
    fi
    openclaw --version || true

[group('AI agent host')]
openclaw-gateway-enable:
    #!/usr/bin/env bash
    set -euo pipefail

    if ! command -v openclaw >/dev/null 2>&1; then
      ujust openclaw-install
    fi

    systemctl --user daemon-reload
    systemctl --user enable --now openclaw-gateway.service
    systemctl --user --no-pager --full status openclaw-gateway.service || true
    ss -ltnp 2>/dev/null | grep ':18789' || true

[group('AI agent host')]
openclaw-onboard-local:
    #!/usr/bin/env bash
    set -euo pipefail

    openclaw onboard --install-daemon
    systemctl --user daemon-reload
    ss -ltnp 2>/dev/null | grep ':18789' || true

[group('AI agent host')]
hermes-workspace-create NAME="hermes-workspace":
    #!/usr/bin/env bash
    set -euo pipefail

    mkdir -p "${HOME}/src/{{ NAME }}"
    echo "Created ${HOME}/src/{{ NAME }}. Enter the Ubuntu agent container before cloning the Hermes repo:"
    echo "  ujust agent-container-enter"
    echo "  cd ~/src/{{ NAME }}"
    echo "  git clone <hermes-repo>"

[group('AI agent host')]
ramalama-serve MODEL="llama3.2" PORT="8080":
    #!/usr/bin/env bash
    set -euo pipefail

    if ! command -v ramalama >/dev/null 2>&1; then
      echo "RamaLama is missing. Install with: brew install ramalama"
      exit 1
    fi

    echo "Serving ${MODEL} on port ${PORT}. Check firewall exposure before using this on untrusted networks."
    ramalama serve --port "{{ PORT }}" "{{ MODEL }}"

[group('AI agent host')]
ramalama-bootstrap:
    #!/usr/bin/env bash
    set -euo pipefail

    if ! command -v brew >/dev/null 2>&1; then
      echo "Homebrew is required and should be provided by Bluefin."
      exit 1
    fi
    brew list ramalama >/dev/null 2>&1 || brew install ramalama
    ramalama --version

[group('AI agent host')]
ramalama-smoke MODEL="llama3.2":
    #!/usr/bin/env bash
    set -euo pipefail

    command -v ramalama >/dev/null 2>&1 || ujust ramalama-bootstrap
    ujust ai-gpu-doctor
    ramalama run "{{ MODEL }}" "Reply with only: ok"
