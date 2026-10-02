FROM node:22-bookworm-slim
WORKDIR /app
COPY package.json ./
COPY src ./src
RUN useradd --system --uid 1000 --create-home pool
USER 1000
EXPOSE 3334 8080
CMD ["node","src/main.js"]
