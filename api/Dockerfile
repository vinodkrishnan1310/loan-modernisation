FROM python:3.12-slim
# Layout matches the repo: api/ is the app root; service/ is a sibling package.
WORKDIR /app/api
COPY api/requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt
COPY api/ .
COPY service/ /app/service/
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
