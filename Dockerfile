FROM python:3.12-slim

WORKDIR /app

# 必要最小限のパッケージをインストール
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 依存ライブラリのインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ソースコードのコピー
COPY . .

# Cloud Run 指定ポート (8080) の開口
EXPOSE 8080

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# Streamlit 起動コマンド
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0", "--server.headless=true"]
