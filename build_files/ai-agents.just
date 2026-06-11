[group('AI agent host')]
ai-doctor:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "== core tools =="
    for cmd in brew fnm node npm podman docker ramalama openclaw clinfo; do
      if command -v "${cmd}" >/dev/null 2>&1; then
        printf "%-10s %s\n" "${cmd}" "$(command -v "${cmd}")"
      else
        printf "%-10s missing\n" "${cmd}"
      fi
    done

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
ai-node-bootstrap NODE_VERSION="24":
    #!/usr/bin/env bash
    set -euo pipefail

    if ! command -v brew >/dev/null 2>&1; then
      echo "Homebrew is required and should be provided by Bluefin DX."
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
openclaw-install VERSION="latest":
    #!/usr/bin/env bash
    set -euo pipefail

    if ! command -v fnm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
      ujust ai-node-bootstrap
      eval "$(fnm env --shell bash)"
    fi

    echo "Inspecting package metadata before install:"
    npm view "openclaw@{{ VERSION }}" name version dist.integrity dist.tarball --json
    npm install --global --ignore-scripts "openclaw@{{ VERSION }}"
    openclaw --version || true

[group('AI agent host')]
openclaw-onboard-local:
    #!/usr/bin/env bash
    set -euo pipefail

    echo "Run OpenClaw onboarding as your normal user:"
    echo "  openclaw onboard --install-daemon"
    echo
    echo "After onboarding, keep the gateway local unless you have reviewed the exposure runbook:"
    echo "  openclaw gateway status"
    echo "  ss -ltnp | grep 18789"
    echo
    echo "For non-main sessions, configure sandboxing in OpenClaw:"
    echo '  agents.defaults.sandbox.mode: "non-main"'

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
