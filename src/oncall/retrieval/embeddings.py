"""Text embeddings via Amazon Bedrock (Titan Text Embeddings)."""
import json
import os

from oncall.llm import bedrock_runtime


def embed_text(client, text: str, model_id: str | None = None) -> list[float]:
    """Return the embedding vector for a string."""
    model_id = model_id or os.environ.get("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
    resp = client.invoke_model(modelId=model_id, body=json.dumps({"inputText": text}))
    return json.loads(resp["body"].read())["embedding"]


def embed_client(region: str | None = None):
    return bedrock_runtime(region)
