FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install opentelemetry-instrumentation-openai-v2 from source (not yet on PyPI).
# Sparse-checkout only the two needed packages to keep the clone small.
RUN git clone --depth=1 --filter=blob:none --sparse \
        https://github.com/open-telemetry/opentelemetry-python-contrib.git /tmp/otel-contrib && \
    cd /tmp/otel-contrib && \
    git sparse-checkout set \
        util/opentelemetry-util-genai \
        instrumentation-genai/opentelemetry-instrumentation-openai-v2 && \
    pip install --no-cache-dir \
        /tmp/otel-contrib/util/opentelemetry-util-genai \
        /tmp/otel-contrib/instrumentation-genai/opentelemetry-instrumentation-openai-v2 && \
    rm -rf /tmp/otel-contrib

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
