"""Entry point for the HR eval service."""

import asyncio
import os

import boto3
from openai import OpenAI

from otel_helpers import configure_otel
from service import EvalService
from tempo_service import TempoService


def _make_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


if __name__ == "__main__":
    configure_otel()
    service = EvalService(
        openai_client=OpenAI(api_key=os.environ.get("OPENAI_API_KEY_EVAL")),
        s3_client=_make_s3_client(),
        tempo=TempoService(),
    )
    asyncio.run(service.run())
