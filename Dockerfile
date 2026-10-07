# Imagem única para a API e para o servidor MCP do ERP (o comando define o papel).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependências primeiro (camada cacheável), depois o código.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[postgres]"

# Nunca rodar como root.
RUN useradd --create-home --uid 10001 agent
USER agent

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status == 200 else 1)"

ENV HOST=0.0.0.0 PORT=8080
CMD ["python", "-m", "purchase_agent"]
