# Next.js dev image · hot reload for development.
# Production uses a separate Dockerfile.web.prod (deferred to v1.1).

FROM node:20-alpine

WORKDIR /app

# Copy package files first for layer caching
COPY apps/web/package*.json ./

# Install (npm ci would be stricter but we want flexibility in dev)
RUN npm install

# Application code (volume-mounted in compose for hot reload)
COPY apps/web .

EXPOSE 3000

CMD ["npm", "run", "dev"]
