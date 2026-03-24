# 定义构建参数，用于指定基础镜像
ARG PYTHON_VERSION=3.14

# 使用amd64架构的Alpine基础镜像
FROM python:${PYTHON_VERSION}-alpine AS builder

# 安装构建依赖（Alpine系统）
RUN apk add --no-cache \
    build-base \
    zlib-dev \
    jpeg-dev \
    freetype-dev \
    lcms2-dev \
    openjpeg-dev \
    tiff-dev \
    libwebp-dev

# 安装PDM
RUN pip install -U pdm
ENV PDM_CHECK_UPDATE=false

WORKDIR /app
COPY pyproject.toml README.md ./

# 安装Python依赖
RUN pdm install --prod --no-editable -v

# 复制应用代码
COPY xiaomusic/ ./xiaomusic/
COPY app.py .

# -------------------------- 运行阶段 --------------------------
# 使用amd64架构的Alpine运行时镜像
FROM python:${PYTHON_VERSION}-alpine AS runner

# 安装运行时依赖（Alpine系统）
RUN apk add --no-cache

# 设置工作目录
WORKDIR /app

# 从构建阶段复制产物
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/xiaomusic/ ./xiaomusic/
COPY --from=builder /app/app.py .
COPY --from=builder /app/xiaomusic/__init__.py /base_version.py

# 创建配置目录
RUN mkdir -p /app/conf

# 复制配置文件
COPY conf/replace.json /app/conf/

RUN touch /app/.dockerenv

# 设置卷和暴露端口
VOLUME /app/conf
VOLUME /app/music
EXPOSE 8090

# 设置环境变量
ENV TZ=Asia/Shanghai
ENV PATH=/app/.venv/bin:/usr/local/bin:$PATH

# 直接启动应用
CMD ["/app/.venv/bin/python3", "/app/app.py"]
