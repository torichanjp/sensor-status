#!/usr/bin/env python3
"""CDKエントリーポイント。

ステージングと本番を別スタックとして定義します。

    cdk deploy SensorStack-staging   # ステージングのみ
    cdk deploy SensorStack-prod      # 本番のみ
    cdk deploy --all                 # 両方
"""

import os

import aws_cdk as cdk
from aws_cdk import RemovalPolicy, aws_logs as logs

from stacks.sensor_stack import EnvironmentConfig, SensorStack

# 両環境とも同一アカウント・ap-northeast-1 に配置する
DEFAULT_REGION = "ap-northeast-1"

ENVIRONMENTS = [
    EnvironmentConfig(
        name="staging",
        # 検証用途のため cdk destroy でテーブルも削除する
        table_removal_policy=RemovalPolicy.DESTROY,
        point_in_time_recovery=False,
        log_retention=logs.RetentionDays.ONE_WEEK,
        log_level="DEBUG",
        throttle_rate_limit=10,
        throttle_burst_limit=20,
        lambda_memory_size=256,
        # URLを作り直したいときにインクリメントする（旧APIは削除される）
        api_revision=2,
    ),
    EnvironmentConfig(
        name="prod",
        # 本番データ保護のためスタック削除時もテーブルは残す
        table_removal_policy=RemovalPolicy.RETAIN,
        point_in_time_recovery=True,
        log_retention=logs.RetentionDays.ONE_MONTH,
        log_level="INFO",
        throttle_rate_limit=100,
        throttle_burst_limit=200,
        lambda_memory_size=512,
        # URLを作り直したいときにインクリメントする（旧APIは削除される）
        api_revision=2,
    ),
]

app = cdk.App()

for config in ENVIRONMENTS:
    SensorStack(
        app,
        f"SensorStack-{config.name}",
        config=config,
        env=cdk.Environment(
            account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
            region=os.environ.get("CDK_DEFAULT_REGION") or DEFAULT_REGION,
        ),
        description=(
            f"センサー状態管理API [{config.name}] "
            "(API Gateway HTTP API + Lambda + DynamoDB)"
        ),
    )

app.synth()
