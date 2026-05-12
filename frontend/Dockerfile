# ProcureAI Next.js 14 frontend — Cloud Run image.
# Build context: repo root (uses frontend/cloudbuild.yaml).
#
# Three-stage build:
#   1. deps    — npm ci against package-lock for a reproducible install
#   2. builder — next build with NEXT_PUBLIC_* baked in via --build-arg
#   3. runner  — minimal Node 20-alpine layer containing only standalone
#                output + static assets
#
# NEXT_PUBLIC_* env vars are bundled at build time, so they MUST be
# passed as build args. The Supabase anon key is public-facing by
# design (RLS protects the data), so committing it via build args
# is acceptable per the workflow's "These are PUBLIC" annotation.
#
# Backend Cloud Run URLs (M{1..4}_*_URL) are SERVER-SIDE only and are
# injected at deploy time as Cloud Run env vars — they never reach
# the browser bundle.

# ── Stage 1: deps ─────────────────────────────────────────────────────
FROM node:20-alpine AS deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund


# ── Stage 2: build ────────────────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app

# Public Supabase config — required at build time so the client bundle
# can reach the data layer. Default to empty so docker build doesn't
# fail in dev; the values come from cloudbuild.yaml substitutions.
ARG NEXT_PUBLIC_SUPABASE_URL=""
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY=""
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY \
    NEXT_TELEMETRY_DISABLED=1

COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
RUN npm run build


# ── Stage 3: runner ───────────────────────────────────────────────────
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=8080 \
    HOSTNAME=0.0.0.0

# Standalone Next.js output: server.js + .next/server + minimal node_modules
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
# `public/` doesn't exist in this repo (verified 2026-05-12); add the
# COPY back if you later introduce static assets at frontend/public/.

EXPOSE 8080
CMD ["node", "server.js"]
