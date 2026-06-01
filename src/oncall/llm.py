"""Thin shared wrapper over the Bedrock Converse API."""
import os
import time

import boto3
from botocore.exceptions import ClientError


def bedrock_runtime(region: str | None = None):
    return boto3.client(
        "bedrock-runtime",
        region_name=region or os.environ.get("AWS_REGION", "us-east-1"),
    )


def converse(client, model_id, system, user_msg,
             max_tokens=1500, temperature=0, max_retries=4):
    """Call Converse with backoff on throttling; return the text response."""
    for attempt in range(max_retries):
        try:
            resp = client.converse(
                modelId=model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user_msg}]}],
                inferenceConfig={"temperature": temperature, "maxTokens": max_tokens},
            )
            return resp["output"]["message"]["content"][0]["text"]
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("ThrottlingException", "TooManyRequestsException") and attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
