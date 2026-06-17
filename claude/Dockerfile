FROM python:3.12-slim

# Метаданные
LABEL description="Cryptorg AI Trader MCP Server (Bybit Liquidity)"
LABEL version="1.0"

# Рабочая директория
WORKDIR /app

# Зависимости — отдельным слоем для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Исходный код
COPY . .

# Логи — монтируем снаружи для персистентности
VOLUME ["/app/logs"]
ENV LOG_FILE=/app/logs/cryptorg_mcp.log

# Переменные окружения (переопределяются через docker-compose или -e)
ENV CRYPTORG_BASE_URL=https://api2.cryptorg.net
ENV BYBIT_BASE_URL=https://api.bybit.com
ENV LOG_LEVEL=INFO

# Healthcheck — проверяем, что Python жив
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import mcp; print('ok')" || exit 1

# MCP работает через stdio — не нужен EXPOSE
CMD ["python", "server.py"]
