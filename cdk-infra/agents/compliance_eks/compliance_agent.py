import os
import json
import time

from strands import Agent
from strands.models.bedrock import BedrockModel

MOUNT = "/mnt/s3files"
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
CLAIMS = f"{MOUNT}/.claims-eks"

model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
agent = Agent(
    model=model,
    system_prompt="""
You are a compliance validation agent. Validate analyzed documents against compliance rules.
Check value thresholds, term limits, required clauses.
Return compliance_status (PASS/FAIL/REVIEW_REQUIRED), checks_performed, violations, warnings,
compliance_score (0-100). Return valid JSON.
""",
)


def claim(name: str) -> bool:
    try:
        fd = os.open(os.path.join(CLAIMS, name), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def main() -> None:
    os.makedirs(CLAIMS, exist_ok=True)
    print(f"compliance-agent starting, mount={MOUNT}", flush=True)

    while True:
        analyzed_dir = f"{MOUNT}/analyzed"
        if not os.path.isdir(analyzed_dir):
            time.sleep(10)
            continue

        for analysis_file in os.listdir(analyzed_dir):
            if not analysis_file.endswith(".json"):
                continue
            if not claim(analysis_file):
                continue  # another replica already took it
            try:
                handle_file(analyzed_dir, analysis_file)
            except Exception as exc:  # noqa: BLE001
                print(f"error processing {analysis_file}: {exc}", flush=True)

        time.sleep(10)


def handle_file(analyzed_dir: str, analysis_file: str) -> None:
    with open(f"{analyzed_dir}/{analysis_file}") as f:
        doc = json.load(f)

    result = agent(f"Validate this {doc['type']} for compliance: {json.dumps(doc['content'])}")
    envelope = {
        "id": doc["id"],
        "stage": "validated",
        "type": doc["type"],
        "content": json.loads(result.message["content"][0]["text"]),
    }
    with open(f"{MOUNT}/validated/compliance-{doc['id']}.json", "w") as f:
        json.dump(envelope, f, indent=2)
    print(f"wrote compliance-{doc['id']}", flush=True)


if __name__ == "__main__":
    main()
