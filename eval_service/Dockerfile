FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN git clone --depth=1 --filter=blob:none --sparse \
        --branch openai-v2-hook-and-fixes \
        https://github.com/lmolkova/opentelemetry-python-contrib.git /tmp/otel-contrib && \
    cd /tmp/otel-contrib && \
    git sparse-checkout set \
        util/opentelemetry-util-genai \
        instrumentation-genai/opentelemetry-instrumentation-openai-v2 && \
    pip install --no-cache-dir \
        /tmp/otel-contrib/util/opentelemetry-util-genai \
        /tmp/otel-contrib/instrumentation-genai/opentelemetry-instrumentation-openai-v2 && \
    rm -rf /tmp/otel-contrib

COPY eval_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY eval_service/ .

CMD ["python", "main.py"]
