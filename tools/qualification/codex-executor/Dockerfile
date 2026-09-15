FROM alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b
COPY package/vendor/aarch64-unknown-linux-musl/ /opt/codex/
ENV PATH=/opt/codex/bin:/opt/codex/codex-path:/usr/bin:/bin HOME=/tmp/home CODEX_HOME=/tmp/codex SHELL=/bin/sh
WORKDIR /work/source
USER 65534:65534
ENTRYPOINT ["/opt/codex/bin/codex"]
CMD ["exec-server", "--listen", "stdio"]
