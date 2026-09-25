# codemem in a container: MCP at http://localhost:8055/mcp, web UI at http://localhost:8055/
#
#   docker build -t codemem .
#   docker run --rm -p 127.0.0.1:8055:8055 codemem
#
# The database lives in /data inside the container and is thrown away with it. Mount a volume
# to keep it:  -v codemem-data:/data
# codemem has no authentication. Publish the port on 127.0.0.1 unless the network is trusted.
FROM python:3.12-slim

# git is used by the scanner and discovery; everything else optional stays off.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY codemem ./codemem
RUN pip install --no-cache-dir . && rm -rf /src

RUN useradd --create-home --uid 10001 codemem && mkdir /data && chown codemem /data
USER codemem
WORKDIR /data

# Bind every interface inside the container; the host decides what is reachable.
# No git host, Gitea or Ollama is assumed: embeddings are off and GIT_ROOT is an empty directory.
ENV CODEMEM_DATA=/data \
    CODEMEM_HOST=0.0.0.0 \
    CODEMEM_PORT=8055 \
    CODEMEM_EMBED=0 \
    GIT_ROOT=/data/git \
    PYTHONUNBUFFERED=1

EXPOSE 8055
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8055/health', timeout=4)"

ENTRYPOINT ["codemem"]
CMD ["serve"]
