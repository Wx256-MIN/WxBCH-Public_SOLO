FROM python:3.12-alpine
RUN apk add --no-cache ca-certificates git autoconf automake libtool make gcc g++ pkgconf openssl-dev jansson-dev zeromq-dev musl-dev && git clone https://github.com/skaisser/ckpool.git /usr/src/ckpool && cd /usr/src/ckpool && git checkout 0479f860e439d835d56779d2881312668994750c && sed -i '1i #define _GNU_SOURCE' src/stratifier.c src/ckpool.c && sed -i '/#include <dirent.h>/a #include <libgen.h>' src/stratifier.c src/ckpool.c && ./autogen.sh && ./configure && make -j"$(nproc)" && install -m 0755 src/ckpmsg /usr/local/bin/ckpmsg && rm -rf /usr/src/ckpool
WORKDIR /app
COPY server.py index.html ./
EXPOSE 8080
CMD ["python","server.py"]
