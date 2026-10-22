# KiteLog cloud service (Cloud Run).  Build context = repository root.
FROM python:3.12-slim
WORKDIR /app
COPY cloud/requirements.txt cloud/requirements.txt
RUN pip install --no-cache-dir -r cloud/requirements.txt
COPY tools/ tools/
COPY web/ web/
COPY cloud/ cloud/
ENV PORT=8080 MPLBACKEND=Agg
CMD exec uvicorn cloud.main:app --host 0.0.0.0 --port ${PORT}
