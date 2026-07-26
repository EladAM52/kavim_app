# ══════════════════════════════════════════════════════════════════════════
#  Kavim frontend
#  Build context is the REPO ROOT (see infra/docker-compose.yml).
# ══════════════════════════════════════════════════════════════════════════

FROM node:22-alpine AS base
WORKDIR /app
ENV CI=true

# ── dependency layer ──────────────────────────────────────────────────────
FROM base AS deps
COPY frontend/package.json frontend/package-lock.json* ./
# `npm ci` when a lockfile is present (reproducible), `npm install` otherwise.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# ── development (Vite dev server) ─────────────────────────────────────────
# Used by the "frontend" compose profile. The source is bind-mounted over /app,
# with node_modules kept in a named volume so the container's install survives.
FROM deps AS dev
COPY frontend/ ./
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# ── build ─────────────────────────────────────────────────────────────────
FROM deps AS build
COPY frontend/ ./
RUN npm run build

# ── static output ─────────────────────────────────────────────────────────
# Not served from here. The release pipeline copies /dist into the backend
# image's app/static so the SPA and API share one origin (SPEC §5.5):
#   docker build --target dist -t kavim-frontend:dist .
#   docker create --name x kavim-frontend:dist && docker cp x:/dist ./static
FROM scratch AS dist
COPY --from=build /app/dist /dist
