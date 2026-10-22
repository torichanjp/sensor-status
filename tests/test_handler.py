"""handler.py のローカルテスト（moto でAWSをモック）。

実行方法:
    pip install "moto[dynamodb,secretsmanager]" boto3 pytest
    pytest tests/
"""

import json
import os
import sys

import boto3
import pytest
from moto import mock_aws

LAMBDA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lambda")
sys.path.insert(0, LAMBDA_DIR)

API_KEY = "test-api-key-1234567890"


@pytest.fixture()
def handler_module():
    with mock_aws():
        os.environ.update(
            {
                "AWS_DEFAULT_REGION": "ap-northeast-1",
                "AWS_ACCESS_KEY_ID": "testing",
                "AWS_SECRET_ACCESS_KEY": "testing",
                "TABLE_NAME": "SensorStates",
            }
        )

        boto3.client("dynamodb").create_table(
            TableName="SensorStates",
            KeySchema=[{"AttributeName": "sensor_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "sensor_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        secret = boto3.client("secretsmanager").create_secret(
            Name="SensorApiKey",
            SecretString=json.dumps({"header": "x-api-key", "api_key": API_KEY}),
        )
        os.environ["API_KEY_SECRET_ARN"] = secret["ARN"]

        # 環境変数をモジュールロード時に読むため、インポートはフィクスチャ内で行う
        for name in ("handler",):
            sys.modules.pop(name, None)
        import handler

        yield handler


def _event(method, sensor_id, body=None, api_key=API_KEY):
    headers = {"content-type": "application/json"}
    if api_key is not None:
        headers["x-api-key"] = api_key
    return {
        "version": "2.0",
        "routeKey": f"{method} /sensors/{{sensor_id}}/state",
        "rawPath": f"/sensors/{sensor_id}/state",
        "headers": headers,
        "pathParameters": {"sensor_id": sensor_id},
        "requestContext": {"http": {"method": method, "path": f"/sensors/{sensor_id}/state"}},
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }


def test_post_then_get(handler_module):
    post = handler_module.lambda_handler(
        _event("POST", "sensor-001", {"status": "ok", "temperature": 23.5, "battery": 87}),
        None,
    )
    assert post["statusCode"] == 200

    get = handler_module.lambda_handler(_event("GET", "sensor-001"), None)
    assert get["statusCode"] == 200
    item = json.loads(get["body"])
    assert item["sensor_id"] == "sensor-001"
    assert item["status"] == "ok"
    assert item["temperature"] == 23.5
    assert item["battery"] == 87
    assert item["updated_at"].endswith("Z")


def test_get_not_found(handler_module):
    res = handler_module.lambda_handler(_event("GET", "unknown-sensor"), None)
    assert res["statusCode"] == 404


def test_unauthorized(handler_module):
    res = handler_module.lambda_handler(_event("GET", "sensor-001", api_key="wrong"), None)
    assert res["statusCode"] == 401

    res = handler_module.lambda_handler(_event("GET", "sensor-001", api_key=None), None)
    assert res["statusCode"] == 401


def test_invalid_json_body(handler_module):
    event = _event("POST", "sensor-001")
    event["body"] = "{not-json"
    res = handler_module.lambda_handler(event, None)
    assert res["statusCode"] == 400


def test_sensor_id_in_body_is_ignored(handler_module):
    handler_module.lambda_handler(
        _event("POST", "sensor-002", {"sensor_id": "spoofed", "status": "warn"}), None
    )
    res = handler_module.lambda_handler(_event("GET", "sensor-002"), None)
    assert json.loads(res["body"])["sensor_id"] == "sensor-002"

    spoofed = handler_module.lambda_handler(_event("GET", "spoofed"), None)
    assert spoofed["statusCode"] == 404
