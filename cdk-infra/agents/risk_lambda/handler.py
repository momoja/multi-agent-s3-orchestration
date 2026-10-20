import os
import json

from strands import Agent
from strands.models.bedrock import BedrockModel

MOUNT = "/mnt/s3files"
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_REGION", "us-east-1")

model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
agent = Agent(
    model=model,
    system_prompt="""
You are a risk analysis agent. Analyze documents for risk factors. For each
document: assign a risk_score (1-10), list specific risk_factors, extract key
data points, set flags (HIGH_VALUE, LONG_TERM, OVERDUE), provide a
recommendation. Return valid JSON.
""",
)


def handler(event, context):
    processed = []
    intake_dir = f"{MOUNT}/intake"
    if not os.path.isdir(intake_dir):
        return {"processed": []}

    for doc_file in os.listdir(intake_dir):
        if not doc_file.endswith(".json"):
            continue
        with open(f"{intake_dir}/{doc_file}") as f:
            doc = json.load(f)

        output_path = f"{MOUNT}/analyzed/analysis-{doc['id']}.json"
        if os.path.exists(output_path):
            continue

        result = agent(f"Analyze this {doc['type']} for risks: {json.dumps(doc['content'])}")
        envelope = {
            "id": doc["id"],
            "stage": "analyzed",
            "type": doc["type"],
            "content": json.loads(result.message["content"][0]["text"]),
        }
        with open(output_path, "w") as f:
            json.dump(envelope, f, indent=2)
        processed.append(doc["id"])

    return {"processed": processed}
