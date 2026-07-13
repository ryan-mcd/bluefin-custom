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
    for cmd in brew fnm node npm podman docker distrobox ramalama openclaw agent-hermes clinfo rocminfo rocm-smi vulkaninfo; do
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
    echo "== Hermes Agent container wrapper =="
    if [[ -x /usr/bin/bluefin-hermes-run ]]; then
      /usr/bin/bluefin-hermes-run --version 2>/dev/null || echo "Hermes is not installed; run: ujust hermes-install <40-character-commit>"
    else
      echo "bluefin-hermes-run missing"
    fi

    echo
    echo "== listening ports =="
    ss -ltnp 2>/dev/null | grep -E '(:8642|:9119|:18789|:8080|:12434)' || true

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

hermes-install COMMIT INSTALLER_SHA256="":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    commit="{{ COMMIT }}"
    expected_sha256="{{ INSTALLER_SHA256 }}"
    if [[ ! "$commit" =~ ^[0-9a-fA-F]{40}$ ]]; then
      echo "COMMIT must be a full 40-character Hermes Agent Git commit." >&2
      exit 2
    fi
    if [[ -n "$expected_sha256" && ! "$expected_sha256" =~ ^[0-9a-fA-F]{64}$ ]]; then
      echo "INSTALLER_SHA256 must be empty or a 64-character SHA-256 digest." >&2
      exit 2
    fi
    if ! distrobox list 2>/dev/null | grep -q 'bluefin-agent-ubuntu'; then
      ujust agent-container-create
    fi

    distrobox enter bluefin-agent-ubuntu -- bash -lc '
      set -euo pipefail
      commit="$1"
      expected_sha256="$2"
      installer="$(mktemp)"
      trap '\''rm -f "$installer"'\'' EXIT
      curl -fsSL "https://raw.githubusercontent.com/NousResearch/hermes-agent/${commit}/scripts/install.sh" -o "$installer"
      actual_sha256="$(sha256sum "$installer" | awk '\''{print $1}'\'')"
      echo "Hermes installer commit: ${commit}"
      echo "Hermes installer SHA-256: ${actual_sha256}"
      if [[ -n "$expected_sha256" && "${actual_sha256,,}" != "${expected_sha256,,}" ]]; then
        echo "Hermes installer SHA-256 mismatch." >&2
        exit 1
      fi
      if ! grep -q -- "--commit" "$installer"; then
        echo "The selected Hermes installer does not support commit-pinned installation." >&2
        exit 1
      fi
      echo "Enabling npm lifecycle scripts only for the reviewed Hermes installer."
      NPM_CONFIG_IGNORE_SCRIPTS=false bash "$installer" --skip-setup --commit "$commit"
      "$HOME/.local/bin/hermes" --version
    ' bluefin-hermes-install "$commit" "$expected_sha256"

hermes-setup:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-hermes-run setup

hermes-model:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-hermes-run model

hermes-doctor:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    distrobox enter bluefin-agent-ubuntu -- bash -lc '
      set -euo pipefail
      source /etc/profile.d/bluefin-agent.sh
      for cmd in git rg ffmpeg node npm python3 podman; do
        command -v "$cmd" >/dev/null 2>&1 || { echo "Missing Hermes prerequisite: $cmd" >&2; exit 1; }
      done
      podman info >/dev/null
      test "$(npm config get ignore-scripts)" = "true"
      test "${HERMES_DOCKER_BINARY:-}" = "podman"
    '
    bluefin-hermes-run --version
    bluefin-hermes-run doctor
    bluefin-hermes-run status --deep

hermes-update COMMIT INSTALLER_SHA256="":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    bluefin-hermes-run backup
    ujust hermes-install "{{ COMMIT }}" "{{ INSTALLER_SHA256 }}"
    ujust hermes-doctor

hermes-gateway-setup:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-hermes-run gateway setup

hermes-gateway-enable:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    bluefin-hermes-run config check
    systemctl --user daemon-reload
    systemctl --user reset-failed bluefin-hermes-gateway.service 2>/dev/null || true
    systemctl --user enable --now bluefin-hermes-gateway.service
    systemctl --user --no-pager --full status bluefin-hermes-gateway.service || true

hermes-gateway-status:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    bluefin-hermes-run status --deep || true
    systemctl --user --no-pager --full status bluefin-hermes-gateway.service || true

hermes-gateway-disable:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    systemctl --user disable --now bluefin-hermes-gateway.service

hermes-dashboard-install:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    distrobox enter bluefin-agent-ubuntu -- bash -lc '
      set -euo pipefail
      cd "$HOME/.hermes/hermes-agent"
      if command -v uv >/dev/null 2>&1; then
        uv pip install -e ".[web,pty]"
      elif [[ -x "$HOME/.local/bin/uv" ]]; then
        "$HOME/.local/bin/uv" pip install -e ".[web,pty]"
      else
        echo "Hermes uv is missing; reinstall Hermes before enabling the dashboard." >&2
        exit 1
      fi
    '

hermes-lan-configure USERNAME="hermes":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    username="{{ USERNAME }}"
    if [[ ! "$username" =~ ^[A-Za-z0-9._-]+$ ]]; then
      echo "USERNAME contains unsupported characters." >&2
      exit 2
    fi

    env_file="${HOME}/.hermes/.env"
    mkdir -p "$(dirname "$env_file")"
    touch "$env_file"
    chmod 0600 "$env_file"

    set_env() {
      local key="$1"
      local value="$2"
      local temporary
      temporary="$(mktemp "${env_file}.XXXXXX")"
      grep -v "^${key}=" "$env_file" >"$temporary" || true
      printf '%s=%s\n' "$key" "$value" >>"$temporary"
      chmod 0600 "$temporary"
      mv -f "$temporary" "$env_file"
    }

    ensure_secret() {
      local key="$1"
      if ! grep -q "^${key}=." "$env_file"; then
        set_env "$key" "$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
      fi
    }

    set_env API_SERVER_ENABLED true
    set_env API_SERVER_HOST 0.0.0.0
    set_env API_SERVER_PORT 8642
    ensure_secret API_SERVER_KEY
    set_env HERMES_DASHBOARD_BASIC_AUTH_USERNAME "$username"
    ensure_secret HERMES_DASHBOARD_BASIC_AUTH_PASSWORD
    ensure_secret HERMES_DASHBOARD_BASIC_AUTH_SECRET
    bluefin-hermes-run config set terminal.backend docker
    bluefin-hermes-run config check
    ujust hermes-dashboard-install
    echo "Hermes LAN credentials are stored with mode 0600 in ${env_file}."

hermes-lan-firewall-enable:
    #!/usr/bin/env bash
    set -euo pipefail

    if (( EUID != 0 )); then
      echo "Run this administrator step with: sudo ujust hermes-lan-firewall-enable" >&2
      exit 1
    fi
    if ! systemctl is-active --quiet firewalld; then
      echo "firewalld is not active; refusing to expose Hermes without source filtering." >&2
      exit 1
    fi

    mapfile -t zones < <({ firewall-cmd --get-default-zone; firewall-cmd --get-active-zones | awk '/^[^[:space:]]/{print $1}'; } | sort -u)
    sources=(192.168.0.0/16 100.64.0.0/10 fd7a:115c:a1e0::/48)
    ports=(8642 9119)
    for zone in "${zones[@]}"; do
      for source in "${sources[@]}"; do
        family=ipv4
        [[ "$source" == *:* ]] && family=ipv6
        for port in "${ports[@]}"; do
          rule="rule family=${family} source address=${source} port port=${port} protocol=tcp accept"
          firewall-cmd --permanent --zone="$zone" --query-rich-rule="$rule" >/dev/null || \
            firewall-cmd --permanent --zone="$zone" --add-rich-rule="$rule"
        done
      done
    done
    firewall-cmd --reload

hermes-lan-firewall-disable:
    #!/usr/bin/env bash
    set -euo pipefail

    if (( EUID != 0 )); then
      echo "Run this administrator step with: sudo ujust hermes-lan-firewall-disable" >&2
      exit 1
    fi
    if ! systemctl is-active --quiet firewalld; then
      exit 0
    fi

    mapfile -t zones < <(firewall-cmd --get-zones | tr ' ' '\n' | sed '/^$/d')
    sources=(192.168.0.0/16 100.64.0.0/10 fd7a:115c:a1e0::/48)
    ports=(8642 9119)
    for zone in "${zones[@]}"; do
      for source in "${sources[@]}"; do
        family=ipv4
        [[ "$source" == *:* ]] && family=ipv6
        for port in "${ports[@]}"; do
          rule="rule family=${family} source address=${source} port port=${port} protocol=tcp accept"
          firewall-cmd --permanent --zone="$zone" --query-rich-rule="$rule" >/dev/null && \
            firewall-cmd --permanent --zone="$zone" --remove-rich-rule="$rule"
        done
      done
    done
    firewall-cmd --reload

hermes-lan-enable USERNAME="hermes":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    ujust hermes-lan-configure "{{ USERNAME }}"
    systemctl --user daemon-reload
    systemctl --user reset-failed bluefin-hermes-gateway.service bluefin-hermes-dashboard.service 2>/dev/null || true
    systemctl --user enable --now bluefin-hermes-gateway.service bluefin-hermes-dashboard.service
    echo "Hermes dashboard: http://$(hostname -I | awk '{print $1}'):9119"
    echo "Hermes API:       http://$(hostname -I | awk '{print $1}'):8642/v1"
    echo "Allowed sources:  192.168.0.0/16, 100.64.0.0/10, fd7a:115c:a1e0::/48"
    echo "Administrator step: sudo ujust hermes-lan-firewall-enable"

hermes-lan-status:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    systemctl --user --no-pager --full status bluefin-hermes-gateway.service bluefin-hermes-dashboard.service || true
    ss -ltnp 2>/dev/null | grep -E ':(8642|9119)' || true

hermes-lan-disable:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    systemctl --user disable --now bluefin-hermes-dashboard.service
    env_file="${HOME}/.hermes/.env"
    if [[ -f "$env_file" ]]; then
      temporary="$(mktemp "${env_file}.XXXXXX")"
      grep -v '^API_SERVER_ENABLED=' "$env_file" >"$temporary" || true
      printf '%s\n' 'API_SERVER_ENABLED=false' >>"$temporary"
      chmod 0600 "$temporary"
      mv -f "$temporary" "$env_file"
    fi
    systemctl --user try-restart bluefin-hermes-gateway.service
    echo "Administrator cleanup: sudo ujust hermes-lan-firewall-disable"

hermes-local-configure MODEL PORT="8080" CONTEXT_LENGTH="64000":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    model="{{ MODEL }}"
    port="{{ PORT }}"
    context_length="{{ CONTEXT_LENGTH }}"
    if [[ ! "$model" =~ ^[A-Za-z0-9._:/+-]+$ ]]; then
      echo "MODEL contains unsupported characters." >&2
      exit 2
    fi
    if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
      echo "PORT must be between 1 and 65535." >&2
      exit 2
    fi
    if [[ ! "$context_length" =~ ^[0-9]+$ ]] || (( context_length < 64000 )); then
      echo "Hermes agent workflows require CONTEXT_LENGTH of at least 64000." >&2
      exit 2
    fi
    endpoint="http://127.0.0.1:${port}/v1"
    curl -fsS "${endpoint}/models" | jq .
    bluefin-hermes-run config set model.provider custom
    bluefin-hermes-run config set model.default "$model"
    bluefin-hermes-run config set model.base_url "$endpoint"
    bluefin-hermes-run config set model.context_length "$context_length"
    bluefin-hermes-run config set terminal.backend docker
    bluefin-hermes-run config check

hermes-local-smoke:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    response="$(bluefin-hermes-run chat --quiet --toolsets terminal -q 'Use the terminal tool exactly once to run printf bluefin-hermes-ok, then reply with only that output.')"
    printf '%s\n' "$response"
    grep -Fq 'bluefin-hermes-ok' <<<"$response" || {
      echo "Hermes did not complete the required structured terminal tool call." >&2
      exit 1
    }

hermes-uninstall:
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    systemctl --user disable --now bluefin-hermes-gateway.service bluefin-hermes-dashboard.service 2>/dev/null || true
    bluefin-hermes-run uninstall

hermes-workspace-create NAME="hermes-workspace":
    #!/usr/bin/env bash
    set -euo pipefail

    bluefin-agent-user require
    mkdir -p "${HOME}/src/{{ NAME }}"
    echo "Created ${HOME}/src/{{ NAME }} for Hermes Agent projects."
    echo "  ujust agent-container-enter"
    echo "  cd ~/src/{{ NAME }}"

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
