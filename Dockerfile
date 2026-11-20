FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt && useradd -m appuser && mkdir /data && chown appuser /data
COPY backend/ backend/
COPY --from=frontend /build/dist frontend/dist
USER appuser
ENV DATABASE=/data/inventory.db PORT=5000
EXPOSE 5000
CMD ["python", "backend/app.py"]
