FROM node:22-bookworm-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 make g++ ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install --omit=dev

COPY app.js ./

RUN mkdir -p /data && chown -R node:node /data

USER node
ENV NODE_ENV=production

VOLUME ["/data"]
EXPOSE 41837 41838

CMD ["node", "app.js"]
