FROM node:22-bookworm

WORKDIR /app

RUN corepack enable

COPY package.json ./

RUN yarn install --non-interactive

COPY . .

ENV HOST=0.0.0.0
ENV PORT=3000
ENV REACT_APP_BACKEND_URL=http://localhost:8000

EXPOSE 3000

CMD ["yarn", "start"]
