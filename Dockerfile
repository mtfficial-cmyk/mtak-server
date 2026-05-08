FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer is cached unless requirements change)
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY api/ .

# Render injects PORT at runtime (default 10000).
# The app reads PORT from env so no hard-coding needed.
ENV PORT=10000
EXPOSE 10000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
