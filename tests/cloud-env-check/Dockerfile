FROM ubuntu:24.04

# Approximates Claude Code Cloud's documented base image:
#   Ubuntu 24.04 + the toolchain listed in env/CLAUDE.md "Tools available
#   in this env" ("Plus what Anthropic ships by default: Node 20/21/22 +
#   npm/yarn/pnpm/bun, Python 3.x + pip/poetry/uv, Ruby, PHP, Java 21, Go,
#   Rust + cargo, C/C++ toolchain, Docker, Postgres 16, Redis 7").
#
# Deliberately NOT installed (env/setup.sh's job to add these):
#   gh, lefthook, bats, vsce, ovsx, lua5.4, luarocks, busted, vusted,
#   plus any candidate additions (libgtk-3-dev, xvfb, neovim, ...).
#
# Deliberately omitted (not relevant to apt-package discovery):
#   Postgres/Redis daemons, Docker-in-Docker. Adding them later if a
#   consumer's tests actually need them.

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/home/ubuntu/.cargo/bin:/usr/local/go/bin:/usr/local/bin:/usr/bin:/bin \
    CLAUDE_CODE_REMOTE=true

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl wget git sudo jq xz-utils \
      build-essential pkg-config \
      python3 python3-pip python3-venv python3-dev \
      ruby ruby-dev bundler \
      php-cli \
      openjdk-21-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu 24.04 ships an `ubuntu` user (UID 1000, in sudo group) by default.
# Setup scripts in real cloud run as root, but the session/test shell does
# not — running tests as root causes permission tests like "fails on
# unreadable source" to misbehave because root bypasses file modes. Grant
# passwordless sudo so env/setup.sh (apt) still works.
RUN echo "ubuntu ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ubuntu && \
    chmod 0440 /etc/sudoers.d/ubuntu

# Node 22 (NodeSource) + corepack-managed yarn/pnpm.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get update && apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    corepack enable && \
    corepack prepare pnpm@latest --activate && \
    corepack prepare yarn@stable --activate

# uv (preinstalled by Anthropic per docs).
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv && \
    mv /root/.local/bin/uvx /usr/local/bin/uvx || true

# Go (preinstalled by Anthropic per docs).
RUN GO_VERSION=1.23.4 && \
    curl -fsSL https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz | tar -C /usr/local -xz

# Rust toolchain (rustup → cargo) installed under the ubuntu user so
# cargo can write to its registry cache without root. `--profile default`
# gives us clippy + rustfmt + docs — real cloud ships these and lefthook
# hooks routinely invoke clippy/rustfmt, so minimal would be unfaithful.
USER ubuntu
WORKDIR /home/ubuntu
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --default-toolchain stable --profile default

# /workspace is the harness scratch dir; ubuntu owns it.
USER root
RUN mkdir -p /workspace && chown ubuntu:ubuntu /workspace
USER ubuntu
WORKDIR /workspace

# The harness mounts env/setup.sh and the consumer repo at runtime; nothing
# pre-baked here. That way `docker build` runs once and every per-repo
# check is a fresh `docker run`.
CMD ["/bin/bash"]
