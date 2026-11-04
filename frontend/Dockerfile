# syntax=docker/dockerfile:1.7
#
# Vite dev server for docker-compose (source is bind-mounted, node_modules stays in the image).
# Production builds happen in the root Dockerfile, where FastAPI serves the built SPA.
FROM node:22-alpine AS dev
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
