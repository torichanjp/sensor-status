"""センサー状態管理APIのインフラ定義（DynamoDB + Lambda + API Gateway HTTP API）。

環境（staging / prod）ごとに本スタックをインスタンス化し、
API・Lambda・DynamoDBテーブルを完全に分離します。
"""

import os
from dataclasses import dataclass

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_dynamodb as dynamodb,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct

# リポジトリルート（stacks/ の1つ上）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

API_KEY_HEADER = "x-api-key"


@dataclass(frozen=True)
class EnvironmentConfig:
    """環境ごとの差分設定。"""

    name: str  # "staging" / "prod" — スタック名・ステージ名・リソース名に使用
    table_removal_policy: RemovalPolicy
    point_in_time_recovery: bool
    log_retention: logs.RetentionDays
    log_level: str
    throttle_rate_limit: int  # 定常 リクエスト/秒
    throttle_burst_limit: int  # バースト
    lambda_memory_size: int = 256
    # API GatewayのURL（API ID）を作り直したいときにインクリメントする。
    # 値を変えるとCFN上の論理IDが変わり、新しいAPIが作成され旧APIは削除される。
    # DynamoDBテーブルやLambda関数には影響しない。
    api_revision: int = 1

    @property
    def api_construct_id(self) -> str:
        return (
            "SensorHttpApi"
            if self.api_revision <= 1
            else f"SensorHttpApiV{self.api_revision}"
        )

    @property
    def stage_construct_id(self) -> str:
        return (
            "SensorStage"
            if self.api_revision <= 1
            else f"SensorStageV{self.api_revision}"
        )


class SensorStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: EnvironmentConfig,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        env_name = config.name

        # スタック配下の全リソースに環境タグを付与（コスト配分・検索用）
        Tags.of(self).add("Environment", env_name)
        Tags.of(self).add("Application", "sensor-state-api")

        # ------------------------------------------------------------------
        # DynamoDB: センサー状態テーブル（環境ごとに別テーブル）
        # ------------------------------------------------------------------
        table = dynamodb.Table(
            self,
            "SensorStatesTable",
            table_name=f"SensorStates-{env_name}",
            partition_key=dynamodb.Attribute(
                name="sensor_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,  # オンデマンドキャパシティ
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=config.point_in_time_recovery
            ),
            removal_policy=config.table_removal_policy,
        )

        # ------------------------------------------------------------------
        # APIキー（Secrets Managerでデプロイ時に自動生成・環境ごとに別キー）
        #
        # API Gateway の "APIキー + 使用量プラン" は REST API 限定の機能で、
        # HTTP API では利用できないため、カスタムヘッダー認証を採用しています。
        # ------------------------------------------------------------------
        api_key_secret = secretsmanager.Secret(
            self,
            "SensorApiKey",
            description=f"センサー状態管理API({env_name})のカスタムヘッダー認証キー",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"header": "%s"}' % API_KEY_HEADER,
                generate_string_key="api_key",
                password_length=40,
                exclude_punctuation=True,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------------------------------------------------------------
        # Lambda: POST/GET のルーティングとDynamoDB操作
        # ------------------------------------------------------------------
        log_group = logs.LogGroup(
            self,
            "SensorStateHandlerLogs",
            retention=config.log_retention,
            removal_policy=RemovalPolicy.DESTROY,
        )

        handler = lambda_.Function(
            self,
            "SensorStateHandler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(os.path.join(PROJECT_ROOT, "lambda")),
            timeout=Duration.seconds(10),
            memory_size=config.lambda_memory_size,
            log_group=log_group,
            environment={
                "TABLE_NAME": table.table_name,
                "API_KEY_SECRET_ARN": api_key_secret.secret_arn,
                "API_KEY_HEADER": API_KEY_HEADER,
                "LOG_LEVEL": config.log_level,
                "ENV_NAME": env_name,
            },
        )

        table.grant_read_write_data(handler)
        api_key_secret.grant_read(handler)

        # ------------------------------------------------------------------
        # API Gateway (HTTP API) — 環境名のステージを明示的に作成する
        # URLは https://{api-id}.execute-api.{region}.amazonaws.com/{env_name}/...
        # ------------------------------------------------------------------
        http_api = apigwv2.HttpApi(
            self,
            config.api_construct_id,
            api_name=f"sensor-state-api-{env_name}",
            description=f"センサー状態の登録・取得API ({env_name})",
            create_default_stage=False,  # $default ではなく環境名ステージを使う
        )

        integration = apigwv2_integrations.HttpLambdaIntegration(
            "SensorStateIntegration", handler
        )

        http_api.add_routes(
            path="/sensors/{sensor_id}/state",
            methods=[apigwv2.HttpMethod.POST, apigwv2.HttpMethod.GET],
            integration=integration,
        )

        stage = apigwv2.HttpStage(
            self,
            config.stage_construct_id,
            http_api=http_api,
            stage_name=env_name,
            auto_deploy=True,
            throttle=apigwv2.ThrottleSettings(
                rate_limit=config.throttle_rate_limit,
                burst_limit=config.throttle_burst_limit,
            ),
        )

        # ------------------------------------------------------------------
        # 出力
        # ------------------------------------------------------------------
        CfnOutput(
            self,
            "ApiEndpoint",
            value=stage.url,
            description=f"APIのベースURL ({env_name})",
        )
        CfnOutput(
            self,
            "ApiKeySecretArn",
            value=api_key_secret.secret_arn,
            description="APIキーが格納されたSecrets ManagerシークレットのARN",
        )
        CfnOutput(self, "TableName", value=table.table_name, description="DynamoDBテーブル名")
