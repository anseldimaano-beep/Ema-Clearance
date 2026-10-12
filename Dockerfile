FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data
RUN SECRET_KEY=x python manage.py collectstatic --noinput
CMD ["sh","-c","python manage.py backup; python manage.py migrate --noinput && python manage.py bootstrap && gunicorn config.wsgi --workers ${WEB_CONCURRENCY:-3} --threads 2 --timeout 300 -b 0.0.0.0:${PORT:-8000}"]
