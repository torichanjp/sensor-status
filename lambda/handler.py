"""センサー状態管理APIのLambdaハンドラー。

ルーティング:
    POST /sensors/{sensor_id}/state  -> 状態を登録（upsert）
    GET  /sensors/{sensor_id}/state  -> 最新状態を取得

API Gateway HTTP API (payload format version 2.0) を前提としています。
"""

import hmac
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ["TABLE_NAME"]
API_KEY_SECRET_ARN = os.environ.get("API_KEY_SECRET_ARN")
API_KEY_HEADER = os.environ.get("API_KEY_HEADER", "x-api-key").lower()
API_KEY_CACHE_TTL_SECONDS = int(os.environ.get("API_KEY_CACHE_TTL_SECONDS", "300"))

_TABLE = boto3.resource("dynamodb").Table(TABLE_NAME)
_SECRETS = boto3.client("secretsmanager")

# APIキーはコールドスタートごとに取りに行かず、一定時間キャッシュする
_api_key_cache = {"value": None, "expires_at": 0.0}

# リクエストボディに含まれていても無視し、サーバー側の値で上書きする属性
_RESERVED_ATTRIBUTES = ("sensor_id", "updated_at")


class _DecimalEncoder(json.JSONEncoder):
    """DynamoDBが返すDecimalをJSONへ変換する。"""

    def default(self, o):
        if isinstance(o, Decimal):
            # 整数で表現できる場合はintに、それ以外はfloatに落とす
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(body, cls=_DecimalEncoder, ensure_ascii=False),
    }


def _get_api_key() -> str | None:
    """Secrets ManagerからAPIキーを取得（TTL付きキャッシュ）。"""
    if not API_KEY_SECRET_ARN:
        return None

    now = time.time()
    if _api_key_cache["value"] is not None and now < _api_key_cache["expires_at"]:
        return _api_key_cache["value"]

    secret = _SECRETS.get_secret_value(SecretId=API_KEY_SECRET_ARN)
    payload = json.loads(secret["SecretString"])
    api_key = payload["api_key"]

    _api_key_cache["value"] = api_key
    _api_key_cache["expires_at"] = now + API_KEY_CACHE_TTL_SECONDS
    return api_key


def _is_authorized(event: dict) -> bool:
    expected = _get_api_key()
    if expected is None:
        # 認証が未設定の場合は素通し（通常は発生しない）
        return True

    # HTTP APIのheadersキーは小文字に正規化されるが、念のため自前でも正規化する
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    provided = headers.get(API_KEY_HEADER)
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _parse_body(event: dict) -> dict:
    """リクエストボディをdictとして取り出す。不正ならValueError。"""
    raw = event.get("body")
    if raw is None or raw == "":
        return {}

    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")

    try:
        # floatではなくDecimalで読み込む（DynamoDBはfloatを受け付けないため）
        parsed = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
    except json.JSONDecodeError as exc:
        raise ValueError(f"リクエストボディが不正なJSONです: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("リクエストボディはJSONオブジェクトである必要があります")
    return parsed


def _put_state(sensor_id: str, event: dict) -> dict:
    try:
        body = _parse_body(event)
    except ValueError as exc:
        return _response(400, {"message": str(exc)})

    # 任意の計測値をそのまま格納しつつ、キーとタイムスタンプはサーバー側で確定させる
    item = {k: v for k, v in body.items() if k not in _RESERVED_ATTRIBUTES}
    updated_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    item["sensor_id"] = sensor_id
    item["updated_at"] = updated_at

    try:
        _TABLE.put_item(Item=item)
    except ClientError as exc:
        LOGGER.exception("DynamoDBへの書き込みに失敗しました")
        code = exc.response.get("Error", {}).get("Code", "UnknownError")
        if code == "ValidationException":
            return _response(
                400,
                {"message": "DynamoDBに保存できない値が含まれています", "detail": code},
            )
        raise

    return _response(
        200,
        {"message": "ok", "sensor_id": sensor_id, "updated_at": updated_at, "item": item},
    )


def _get_state(sensor_id: str) -> dict:
    result = _TABLE.get_item(Key={"sensor_id": sensor_id}, ConsistentRead=True)
    item = result.get("Item")
    if item is None:
        return _response(404, {"message": "指定されたsensor_idの状態は存在しません", "sensor_id": sensor_id})
    return _response(200, item)


def lambda_handler(event, context):
    http = (event.get("requestContext") or {}).get("http") or {}
    method = (http.get("method") or "").upper()
    route_key = event.get("routeKey", "")

    if not _is_authorized(event):
        return _response(401, {"message": "APIキーが無効です"})

    sensor_id = (event.get("pathParameters") or {}).get("sensor_id")
    if not sensor_id:
        return _response(400, {"message": "sensor_idが指定されていません"})

    try:
        if method == "POST":
            return _put_state(sensor_id, event)
        if method == "GET":
            return _get_state(sensor_id)
    except Exception:  # noqa: BLE001 - 予期せぬ例外は500として返す
        LOGGER.exception("リクエスト処理中にエラーが発生しました (route=%s)", route_key)
        return _response(500, {"message": "内部エラーが発生しました"})

    return _response(405, {"message": f"サポートされていないメソッドです: {method}"})
