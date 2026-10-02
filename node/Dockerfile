FROM debian:bookworm-slim

ARG BCHN_VERSION=29.2.0
ARG TARGETARCH

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar libevent-2.1-7 libevent-pthreads-2.1-7 libevent-extra-2.1-7 libevent-core-2.1-7 libevent-openssl-3-1-7 \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) ARCH="x86_64" ;; \
      arm64) ARCH="aarch64" ;; \
      *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    URL="https://github.com/bitcoin-cash-node/bitcoin-cash-node/releases/download/v${BCHN_VERSION}/bitcoin-cash-node-${BCHN_VERSION}-${ARCH}-linux-gnu.tar.gz"; \
    curl -fsSL "${URL}" -o /tmp/bchn.tar.gz; \
    case "${ARCH}" in \
      x86_64) EXPECTED="23bbe9ff749136a9b832a2a59023ce76a5391322c1ea2f4f24b5b91f035081bc" ;; \
      aarch64) EXPECTED="27e177e5a07dc78c2c0d18798ea2e3925111955509ecdc476e828e7ce8f46383" ;; \
    esac; \
    echo "${EXPECTED}  /tmp/bchn.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/bchn.tar.gz -C /tmp; \
    install -m 0755 /tmp/bitcoin-cash-node-${BCHN_VERSION}/bin/bitcoind /usr/local/bin/bitcoind; \
    install -m 0755 /tmp/bitcoin-cash-node-${BCHN_VERSION}/bin/bitcoin-cli /usr/local/bin/bitcoin-cli; \
    rm -rf /tmp/bchn.tar.gz /tmp/bitcoin-cash-node-${BCHN_VERSION}

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8333
ENTRYPOINT ["/entrypoint.sh"]
