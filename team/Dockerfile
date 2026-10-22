FROM python:3.12.7-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home stepnoe && mkdir /data && chown stepnoe /data
COPY . .
USER stepnoe
ENV PYTHONUNBUFFERED=1 STATE_DB=/data/stepnoe.db
EXPOSE 8000
CMD ["python", "service.py"]
