# Agent image: a coding agent (Codex CLI and Claude Code) with the Python CAD tools the task statements name.
# At run time the container has no network (--network none). Its only way out is a Unix socket mounted at /proxy,
# behind which the proxy container lets through the model endpoint and nothing else. Only the workspace is mounted:
# no ground truth and no source drawing is in the image or the container.
#
#   docker build -t cadbench/agent -f agent.Dockerfile .
#
# Where the public registries are slow or closed, name a base image you already have and package mirrors:
#   --build-arg BASE=<image> --build-arg PIP_INDEX=<url> --build-arg NPM_REGISTRY=<url> --build-arg APT_MIRROR=<url>
# The default base is the full Debian image, which brings ImageMagick, git and a C compiler; a slim base leaves them out.
ARG BASE=python:3.12-bookworm
FROM ${BASE}
ARG CLAUDE_CODE_VERSION=2.1.309
ARG CODEX_VERSION=latest
ARG PIP_INDEX=
ARG NPM_REGISTRY=
ARG APT_MIRROR=
USER root
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s#http://deb.debian.org#$APT_MIRROR#g" /etc/apt/sources.list.d/debian.sources; fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends nodejs npm libgl1 libglib2.0-0 poppler-utils socat ripgrep \
 && rm -rf /var/lib/apt/lists/*
RUN npm install -g ${NPM_REGISTRY:+--registry="$NPM_REGISTRY"} @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} @openai/codex@${CODEX_VERSION} && npm cache clean --force
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir ${PIP_INDEX:+-i "$PIP_INDEX"} \
      ezdxf==1.4.5 pymupdf opencv-python-headless scikit-image numpy scipy pillow matplotlib \
 && /opt/venv/bin/pip freeze > /opt/pip-freeze.txt
# entry point: bridge 127.0.0.1:8888 to the proxy socket, then run the command
RUN printf '#!/bin/sh\nsocat TCP-LISTEN:8888,fork,reuseaddr,bind=127.0.0.1 UNIX-CONNECT:/proxy/proxy.sock 2>/dev/null &\nsleep 0.3\nexec "$@"\n' > /usr/local/bin/cadbench-entry \
 && chmod +x /usr/local/bin/cadbench-entry
RUN useradd -m -u 1000 -s /bin/bash agent
# Codex runs its commands in login shells, which set PATH anew: put the environment with the CAD tools first there too
RUN printf 'export PATH=/opt/venv/bin:$PATH\n' > /etc/profile.d/cadbench.sh
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    HTTP_PROXY=http://127.0.0.1:8888 HTTPS_PROXY=http://127.0.0.1:8888 \
    http_proxy=http://127.0.0.1:8888 https_proxy=http://127.0.0.1:8888 NO_PROXY= \
    DISABLE_AUTOUPDATER=1 DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
USER agent
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/cadbench-entry"]
CMD ["bash"]
