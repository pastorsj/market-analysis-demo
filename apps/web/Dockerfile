FROM node:22.17.0-alpine AS build
WORKDIR /src
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY index.html tsconfig.json vite.config.ts ./
COPY src src
RUN pnpm build
FROM nginxinc/nginx-unprivileged:1.29.0-alpine
ARG MARKET_SHOCK_BUILD_INPUT_SHA256=unverified
LABEL com.nvidia.market-shock.build-input-schema="market-shock-build-input-v1" \
      com.nvidia.market-shock.build-input-sha256="${MARKET_SHOCK_BUILD_INPUT_SHA256}"
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 3000
