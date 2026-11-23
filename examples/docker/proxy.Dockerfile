# Allowlist proxy: lets through the hosts of the file `allowlist` and refuses everything else.
# tinyproxy listens on the loopback of this container only; the agent container reaches it through the Unix socket
# /proxy/proxy.sock, which both containers mount.
#
#   cp allowlist.example allowlist      # then put the host of your model endpoint in it
#   docker build -t cadbench/proxy -f proxy.Dockerfile .
# --build-arg BASE=<image> and --build-arg APT_MIRROR=<url> as for the agent image; any Debian bookworm image will do.
ARG BASE=debian:bookworm-slim
FROM ${BASE}
ARG APT_MIRROR=
RUN if [ -n "$APT_MIRROR" ]; then sed -i "s#http://deb.debian.org#$APT_MIRROR#g" /etc/apt/sources.list.d/debian.sources; fi \
 && apt-get update && apt-get install -y --no-install-recommends tinyproxy socat && rm -rf /var/lib/apt/lists/*
COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY allowlist /etc/tinyproxy/allowlist
CMD ["sh", "-c", "mkdir -p /proxy && socat UNIX-LISTEN:/proxy/proxy.sock,fork,mode=777,unlink-early TCP:127.0.0.1:8888 & exec tinyproxy -d -c /etc/tinyproxy/tinyproxy.conf"]
