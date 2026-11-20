FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN if [ -s requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

COPY . .

CMD ["python", "import_jsonl.py"]
