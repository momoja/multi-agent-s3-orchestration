FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir strands-agents boto3
COPY compliance_agent.py .

CMD ["python", "compliance_agent.py"]
