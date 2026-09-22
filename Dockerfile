FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt
COPY . .

EXPOSE 8501
CMD ["sh", "-c", "streamlit run web_app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --server.maxUploadSize=512"]
