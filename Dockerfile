FROM python:3.11-bookworm AS requirements-stage

WORKDIR /tmp

ENV POETRY_HOME="/opt/poetry" PATH="${PATH}:/opt/poetry/bin"

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple/ && \
    pip install --upgrade pip

RUN curl -sSL https://install.python-poetry.org | python - -y && \
    poetry self add poetry-plugin-export

COPY ./pyproject.toml ./poetry.lock* /tmp/

RUN poetry export \
      -f requirements.txt \
      --output requirements.txt \
      --without-hashes \
      --without-urls

FROM python:3.11-bookworm AS build-stage

WORKDIR /wheel

COPY --from=requirements-stage /tmp/requirements.txt /wheel/requirements.txt

# RUN python3 -m pip config set global.index-url https://mirrors.aliyun.com/pypi/simple
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple/ && \
    pip install --upgrade pip

RUN pip wheel --wheel-dir=/wheel --no-cache-dir --requirement /wheel/requirements.txt

FROM python:3.11-bookworm AS metadata-stage

WORKDIR /tmp

RUN --mount=type=bind,source=./.git/,target=/tmp/.git/ \
  git describe --tags --exact-match > /tmp/VERSION 2>/dev/null \
  || git rev-parse --short HEAD > /tmp/VERSION \
  && echo "Building version: $(cat /tmp/VERSION)"

FROM python:3.11-slim-bookworm

WORKDIR /app/zhenxun

ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1
#COPY ./scripts/docker/start.sh /start.sh
#RUN chmod +x /start.sh

EXPOSE 8080

# 1. 确保目录存在
RUN mkdir -p /etc/apt/sources.list.d
# 2. 直接写入清华源（主源+安全源）
RUN echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list && \
    apt update && \
    apt install -y --no-install-recommends curl fontconfig fonts-noto-color-emoji && \
    apt clean && \
    fc-cache -fv && \
    apt-get purge -y --auto-remove curl && \
    rm -rf /var/lib/apt/lists/*

# 复制依赖项和应用代码
COPY --from=build-stage /wheel /wheel
COPY . .

RUN pip install --no-cache-dir --no-index --find-links=/wheel -r /wheel/requirements.txt && rm -rf /wheel

RUN playwright install --with-deps chromium \
  && rm -rf /var/lib/apt/lists/* /tmp/*

COPY --from=metadata-stage /tmp/VERSION /app/VERSION

VOLUME ["/app/zhenxun/data", "/app/zhenxun/resources", "/app/zhenxun/log"]

CMD ["python", "bot.py"]
