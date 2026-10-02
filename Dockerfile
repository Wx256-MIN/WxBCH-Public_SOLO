FROM node:22-bookworm-slim AS build
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3 make g++ ca-certificates && rm -rf /var/lib/apt/lists/*
COPY package.json ./
RUN npm install --omit=dev

FROM node:22-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
RUN useradd --create-home --uid 10001 pool
COPY --from=build /app/node_modules ./node_modules
COPY package.json ./
COPY src ./src
COPY test ./test
RUN mkdir -p /data && chown -R pool:pool /app /data
USER pool
EXPOSE 3336 3337
VOLUME ["/data"]
CMD ["node","src/index.js"]
