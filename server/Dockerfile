FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY asistan/ asistan/
ENV TZ=Europe/Istanbul DB_PATH=/data/nobetci.db
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "asistan.main:app", "--host", "0.0.0.0", "--port", "8000"]
