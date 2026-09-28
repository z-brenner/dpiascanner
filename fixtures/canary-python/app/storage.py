import os
import uuid

import boto3
from botocore.config import Config

from app.config import settings

_s3 = boto3.client(
    "s3",
    region_name=settings.s3_region,
    endpoint_url=settings.s3_endpoint_url,
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE"),
    aws_secret_access_key=os.environ.get(
        "AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    ),
    config=Config(s3={"addressing_style": "path"}) if settings.s3_endpoint_url else None,
)


def upload_export(document: bytes) -> str:
    key = f"exports/{uuid.uuid4().hex}.json"
    _s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=document)
    return key
