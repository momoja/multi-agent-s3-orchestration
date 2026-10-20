FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir strands-agents boto3 python-docx
COPY report_agent.py .

CMD ["python", "report_agent.py"]
