import os
import json
import time
import urllib.parse

import boto3
from strands import Agent
from strands.models.bedrock import BedrockModel

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
QUEUE_URL = os.environ["QUEUE_URL"]
MOUNT = "/mnt/s3files"
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

sqs = boto3.client("sqs", region_name=REGION)
model = BedrockModel(model_id=MODEL_ID, region_name=REGION)

agent = Agent(
    model=model,
    system_prompt="""
You are a document intake agent. Read customer forms and prepare structured
intake records. For each document, extract key metadata, classify priority
(high/medium/low), identify flags. Return valid JSON with: document_type,
title, priority, flags, key_entities, summary.
""",
)


def main() -> None:
    print(f"intake-agent starting, queue={QUEUE_URL}, mount={MOUNT}", flush=True)
    while True:
        response = sqs.receive_message(QueueUrl=QUEUE_URL, MaxNumberOfMessages=10, WaitTimeSeconds=10)
        messages = response.get("Messages", [])
        if not messages:
            continue

        for msg in messages:
            try:
                handle_message(msg)
            except Exception as exc:  # noqa: BLE001 - keep the poller alive
                print(f"error handling message: {exc}", flush=True)
                continue
            sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])


def handle_message(msg: dict) -> None:
    body = json.loads(msg["Body"])
    records = body.get("Records")
    if not records:
        return  # e.g. S3 test event
    record = records[0]
    s3_key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
    filename = s3_key.split("/")[-1]
    if not filename.endswith(".json"):
        return

    doc_id = filename.replace(".json", "")
    output_path = f"{MOUNT}/intake/doc-{doc_id}.json"
    if os.path.exists(output_path):
        return  # already processed

    with open(f"{MOUNT}/{s3_key}") as f:
        form = json.load(f)

    result = agent(f"Process this document for intake: {json.dumps(form.get('content', form))}")
    envelope = {
        "id": doc_id,
        "stage": "intake",
        "type": form.get("document_type", "unknown"),
        "content": json.loads(result.message["content"][0]["text"]),
    }
    with open(output_path, "w") as f:
        json.dump(envelope, f, indent=2)
    print(f"wrote {output_path}", flush=True)


if __name__ == "__main__":
    main()
