# Synaptika apps (demos + messenger) — production image
FROM node:22-bookworm-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
# Skip Playwright browser download (devDependency) to save disk/RAM.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm ci --ignore-scripts \
  && npm cache clean --force

COPY . .

ENV NODE_ENV=production
ENV TRUST_PROXY=1

# Default command overridden per-service in compose.
CMD ["npx", "tsx", "messenger/index.ts"]
