# racklint/examples/no-hardware/Dockerfile
# racklint installed from this repository's source. Build context: the repository root (see compose.yaml).
FROM python:3.12-slim
WORKDIR /src
COPY pyproject.toml requirements.txt requirements-network.txt README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[network]"
WORKDIR /example
ENTRYPOINT ["racklint"]
