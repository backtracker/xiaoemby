# 多阶段构建：与 .python-version(3.10) 对齐，避免 3.14 上依赖无 wheel 被迫源码编译
ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

# pdm.lock 锁的 aiohttp 3.8.6 在 3.12 上没有 wheel，需要 gcc 源码编译
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV PDM_CHECK_UPDATE=false
RUN pip install --no-cache-dir -U pdm

WORKDIR /app
COPY pyproject.toml pdm.lock README.md ./
RUN pdm install --prod --no-editable -v

# -------------------------- 运行阶段 --------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runner

# Pillow 等依赖需要的基础运行库
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libjpeg62-turbo zlib1g libfreetype6 liblcms2-2 libwebp7 libtiff6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY xiaomusic/ ./xiaomusic/
COPY app.py .
COPY conf/replace.json /app/conf/

# 代码用 /app/.dockerenv 判断是否运行在 Docker 中
RUN touch /app/.dockerenv && mkdir -p /app/conf /app/music

VOLUME /app/conf
VOLUME /app/music
EXPOSE 8090

ENV TZ=Asia/Shanghai
ENV PATH=/app/.venv/bin:/usr/local/bin:$PATH

CMD ["/app/.venv/bin/python3", "/app/app.py"]
