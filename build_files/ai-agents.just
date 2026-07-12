ai-doctor:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "== agent user =="
    if command -v bluefin-agent-user >/dev/null 2>&1; then
      bluefin-agent-user status || true
    else
      echo "bluefin-agent-user helper missing"
    fi
    echo

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
      npm config get save-exact
    else
      echo "npm missing; run: ujust ai-node-bootstrap"
    fi

    echo
    echo "== OpenClaw host wrapper =="
    if [[ -x /usr/bin/bluefin-openclaw-run ]]; then
      if openclaw_version=$(/usr/bin/bluefin-openclaw-run --version 2>/dev/null); then
        echo "bluefin-openclaw-run ${openclaw_version}"
        /usr/bin/bluefin-openclaw-run gateway status 2>/dev/null || true
      else
        echo "bluefin-openclaw-run cannot start OpenClaw; run: ujust openclaw-install"
      fi
    else
      echo "bluefin-openclaw-run missing"
    fi

    echo
    echo "== listening ports =="
    ss -ltnp 2>/dev/null | grep -E '(:18789|:8080|:12434)' || true

agent-user-status:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user status

agent-user-set-password:
    #!/usr/bin/env bash
    set -euo pipefail

    agent_user="$(bluefin-agent-user name)"
    sudo passwd "$agent_user"

agent-user-gui-check:
    #!/usr/bin/env bash
    set -euo pipefail

    agent_user="$(bluefin-agent-user name)"
    bluefin-agent-user status || true
    echo
    echo "== password status =="
    sudo passwd -S "$agent_user" || true
    echo
    echo "If the password status is locked, run: ujust agent-user-set-password"

agent-user-enter:
    #!/usr/bin/env bash
    set -euo pipefail

    agent_user="$(bluefin-agent-user name)"
    if [[ "$(id -un)" == "$agent_user" ]]; then
      exec "${SHELL:-/bin/bash}"
    fi
    exec sudo -iu "$agent_user"

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

agent-container-create:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    distrobox assemble create --file /usr/share/bluefin-agent/distrobox/agent-ubuntu.ini

agent-container-enter:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    distrobox enter bluefin-agent-ubuntu

agent-container-bootstrap-node:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    if ! distrobox list 2>/dev/null | grep -q 'bluefin-agent-ubuntu'; then
      distrobox assemble create --file /usr/share/bluefin-agent/distrobox/agent-ubuntu.ini
    fi

    distrobox enter bluefin-agent-ubuntu -- /run/host/usr/bin/bluefin-agent-ubuntu-setup --install-node

agent-container-openclaw-install VERSION="latest" ALLOW_SCRIPTS="false":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    if ! distrobox list 2>/dev/null | grep -q 'bluefin-agent-ubuntu'; then
      distrobox assemble create --file /usr/share/bluefin-agent/distrobox/agent-ubuntu.ini
    fi

    if [[ "{{ ALLOW_SCRIPTS }}" == "true" ]]; then
      script_flag="--ignore-scripts=false"
      echo "Installing with npm lifecycle scripts enabled for this command."
    else
      script_flag="--ignore-scripts"
    fi

    distrobox enter bluefin-agent-ubuntu -- bash -lc 'source /etc/profile.d/bluefin-agent.sh; if ! command -v node >/dev/null 2>&1; then /run/host/usr/bin/bluefin-agent-ubuntu-setup --install-node; source /etc/profile.d/bluefin-agent.sh; fi; npm view "openclaw@{{ VERSION }}" name version dist.integrity dist.tarball --json; npm install --global '"${script_flag}"' "openclaw@{{ VERSION }}"; openclaw --version || true'

agent-container-npm-install-trusted PACKAGE VERSION="latest":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    echo "This enables npm lifecycle scripts for one reviewed package install inside the Ubuntu agent container."
    distrobox enter bluefin-agent-ubuntu -- bash -lc 'source /etc/profile.d/bluefin-agent.sh; npm view "{{ PACKAGE }}@{{ VERSION }}" name version dist.integrity dist.tarball --json; npm install --global --ignore-scripts=false "{{ PACKAGE }}@{{ VERSION }}"'

ai-node-bootstrap NODE_VERSION="24":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    if command -v brew >/dev/null 2>&1; then
      eval "$(brew shellenv)"
    elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
      eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
    fi

    if ! command -v fnm >/dev/null 2>&1; then
      echo "fnm is required but is not installed or not on PATH."
      echo "Install fnm through the system-managed Homebrew setup, then rerun this recipe."
      exit 1
    fi
    eval "$(fnm env --shell bash)"
    fnm install "{{ NODE_VERSION }}"
    fnm default "{{ NODE_VERSION }}"

    echo "Node: $(node --version)"
    echo "npm:  $(npm --version)"
    echo "System npm policy: ignore-scripts=$(npm config get ignore-scripts)"

openclaw-install VERSION="latest" ALLOW_SCRIPTS="false":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    if command -v brew >/dev/null 2>&1; then
      eval "$(brew shellenv)"
    elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
      eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
    fi
    if ! command -v fnm >/dev/null 2>&1; then
      echo "fnm is required but is not installed or not on PATH."
      echo "Install fnm through the system-managed Homebrew setup, then rerun this recipe."
      exit 1
    fi
    eval "$(fnm env --shell bash)"
    if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
      ujust ai-node-bootstrap
      eval "$(fnm env --shell bash)"
    fi
    if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
      echo "Node and npm are required. Install fnm through the system-managed Homebrew setup, then rerun this recipe."
      exit 1
    fi

    echo "Inspecting package metadata before install:"
    npm view "openclaw@{{ VERSION }}" name version dist.integrity dist.tarball --json
    if [[ "{{ ALLOW_SCRIPTS }}" == "true" ]]; then
      npm install --global --ignore-scripts=false "openclaw@{{ VERSION }}"
    else
      npm install --global --ignore-scripts "openclaw@{{ VERSION }}"
    fi
    openclaw --version || true

openclaw-gateway-enable:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    echo "Advanced path: enabling the image-managed OpenClaw gateway service."
    echo "For the lower-maintenance default, use: ujust openclaw-onboard-local"
    ujust openclaw-gateway-configure-local

    systemctl --user daemon-reload
    systemctl --user reset-failed openclaw-gateway.service 2>/dev/null || true
    systemctl --user enable --now openclaw-gateway.service
    systemctl --user --no-pager --full status openclaw-gateway.service || true
    ss -ltnp 2>/dev/null | grep ':18789' || true

openclaw-gateway-configure-local:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    echo "Advanced path: configuring OpenClaw for the image-managed gateway service."
    echo "For the lower-maintenance default, use: ujust openclaw-onboard-local"
    if ! /usr/bin/bluefin-openclaw-run --version >/dev/null 2>&1; then
      ujust openclaw-install
    fi

    config_has_value() {
      local value
      value="$(/usr/bin/bluefin-openclaw-run config get "$1" 2>/dev/null || true)"
      value="${value%\"}"
      value="${value#\"}"
      [[ -n "${value}" && "${value}" != "null" ]]
    }

    mkdir -p "${HOME}/.openclaw/workspace"
    /usr/bin/bluefin-openclaw-run config set gateway.mode local
    auth_mode="$(/usr/bin/bluefin-openclaw-run config get gateway.auth.mode 2>/dev/null || true)"
    auth_mode="${auth_mode%\"}"
    auth_mode="${auth_mode#\"}"
    [[ "${auth_mode}" == "null" ]] && auth_mode=""
    if [[ "${auth_mode}" == "token" ]]; then
      if ! config_has_value gateway.auth.token; then
        OPENCLAW_SERVICE_REPAIR_POLICY=external /usr/bin/bluefin-openclaw-run doctor --generate-gateway-token
      fi
    elif [[ -z "${auth_mode}" ]]; then
      if ! config_has_value gateway.auth.token && ! config_has_value gateway.auth.password; then
        OPENCLAW_SERVICE_REPAIR_POLICY=external /usr/bin/bluefin-openclaw-run doctor --generate-gateway-token
      fi
    fi
    /usr/bin/bluefin-openclaw-run config validate
    OPENCLAW_SERVICE_REPAIR_POLICY=external /usr/bin/bluefin-openclaw-run doctor || true

openclaw-dashboard:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    /usr/bin/bluefin-openclaw-run dashboard

openclaw-gateway-status:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    /usr/bin/bluefin-openclaw-run gateway status

openclaw-gateway-setup:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "openclaw-gateway-setup is a compatibility alias for the recommended onboarding path."
    ujust openclaw-onboard-local

openclaw-onboard-local:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    if ! /usr/bin/bluefin-openclaw-run --version >/dev/null 2>&1; then
      ujust openclaw-install
    fi

    OPENCLAW_WRAPPER=/usr/bin/bluefin-openclaw-run /usr/bin/bluefin-openclaw-run onboard --mode local --install-daemon
    /usr/bin/bluefin-openclaw-run gateway status || true

hermes-workspace-create NAME="hermes-workspace":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    mkdir -p "${HOME}/src/{{ NAME }}"
    echo "Created ${HOME}/src/{{ NAME }}. Enter the Ubuntu agent container before cloning the Hermes repo:"
    echo "  ujust agent-container-enter"
    echo "  cd ~/src/{{ NAME }}"
    echo "  git clone <hermes-repo>"

ramalama-serve MODEL="llama3.2" PORT="8080":
    #!/usr/bin/env bash
    set -euo pipefail

    model="{{ MODEL }}"
    port="{{ PORT }}"

    bluefin-agent-user require
    if command -v brew >/dev/null 2>&1; then
      eval "$(brew shellenv)"
    elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
      eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
    fi
    if ! command -v ramalama >/dev/null 2>&1; then
      echo "RamaLama is missing. Install it through the system-managed Homebrew setup, then rerun this recipe."
      exit 1
    fi

    echo "Serving ${model} on port ${port}. Check firewall exposure before using this on untrusted networks."
    ramalama serve --host 127.0.0.1 --port "${port}" "${model}"

ramalama-service-enable MODEL="llama3.2" PORT="8080" NAME="bluefin-ramalama":
    #!/usr/bin/env bash
    set -euo pipefail

    model="{{ MODEL }}"
    port="{{ PORT }}"
    name="{{ NAME }}"

    bluefin-agent-user require
    if command -v brew >/dev/null 2>&1; then
      eval "$(brew shellenv)"
    elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
      eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
    fi
    if ! command -v ramalama >/dev/null 2>&1; then
      echo "RamaLama is missing. Install it through the system-managed Homebrew setup, then rerun this recipe."
      exit 1
    fi

    mkdir -p "${HOME}/.config/containers/systemd"
    ramalama serve --host 127.0.0.1 --port "${port}" --webui off --name "${name}" --generate="quadlet:${HOME}/.config/containers/systemd" "${model}"
    systemctl --user daemon-reload
    systemctl --user enable "${name}.service"
    systemctl --user restart "${name}.service"
    systemctl --user --no-pager --full status "${name}.service" || true
    ss -ltnp 2>/dev/null | grep ":${port}" || true

ramalama-service-disable NAME="bluefin-ramalama":
    #!/usr/bin/env bash
    set -euo pipefail

    name="{{ NAME }}"

    bluefin-agent-user require
    if command -v brew >/dev/null 2>&1; then
      eval "$(brew shellenv)"
    elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
      eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
    fi
    systemctl --user disable --now "${name}.service" || true
    if command -v ramalama >/dev/null 2>&1; then
      ramalama stop "${name}" || true
    fi

ramalama-bootstrap:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    if command -v brew >/dev/null 2>&1; then
      eval "$(brew shellenv)"
    elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
      eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
    fi
    if ! command -v ramalama >/dev/null 2>&1; then
      echo "RamaLama is required but is not installed or not on PATH."
      echo "Install ramalama through the system-managed Homebrew setup, then rerun this recipe."
      exit 1
    fi
    ramalama --version

ramalama-smoke MODEL="llama3.2":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    if command -v brew >/dev/null 2>&1; then
      eval "$(brew shellenv)"
    elif [[ -x /home/linuxbrew/.linuxbrew/bin/brew ]]; then
      eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
    fi
    command -v ramalama >/dev/null 2>&1 || ujust ramalama-bootstrap
    ujust ai-gpu-doctor
    ramalama run "{{ MODEL }}" "Reply with only: ok"
