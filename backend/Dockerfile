FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# 通过 PyPI 安装固定版本的 uv（部分网络无法访问 ghcr.io）
RUN pip install --no-cache-dir uv==0.12.18
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --no-install-project --frozen
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --no-dev --frozen
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home --uid 10001 app
USER app
