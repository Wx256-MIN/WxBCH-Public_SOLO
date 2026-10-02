FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git autoconf automake libtool make gcc g++ pkg-config libssl-dev libjansson-dev libzmq3-dev libc6-dev && rm -rf /var/lib/apt/lists/*
ARG CKPOOL_COMMIT=0479f860e439d835d56779d2881312668994750c
RUN git clone https://github.com/skaisser/ckpool.git /usr/src/ckpool && cd /usr/src/ckpool && git checkout "${CKPOOL_COMMIT}" && ./autogen.sh && ./configure && make -j"$(nproc)" && make install && mkdir -p /etc/ckpool /var/lib/ckpool /run/ckpool
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 3333
ENTRYPOINT ["/entrypoint.sh"]
