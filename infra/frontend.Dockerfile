FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run test:production

FROM nginx:stable-alpine
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY infra/proxy-teamwiki.conf /etc/nginx/proxy-teamwiki.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

