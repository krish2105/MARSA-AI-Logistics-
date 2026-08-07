# Development image. A multi-stage production build (standalone output) lands
# with the deploy phase; Vercel is the primary production target.
FROM node:22-alpine

WORKDIR /app

# Install deps first so the layer caches across source edits.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 3000
CMD ["npm", "run", "dev"]
