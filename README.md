# センサー状態管理API

API Gateway (HTTP API) + Lambda (Python 3.12) + DynamoDB によるサーバーレスAPIを、AWS CDK (Python) で構築します。

## 構成

```text
.
├── app.py                  # CDKエントリーポイント
├── cdk.json                # CDK設定ファイル
├── requirements.txt        # CDK依存関係
├── lambda/
│   ├── requirements.txt
│   └── handler.py          # POST / GET のルーティングおよびDynamoDB操作
├── stacks/
│   └── sensor_stack.py     # API Gateway, Lambda, DynamoDB の定義
└── tests/
    └── test_handler.py     # motoによるハンドラーのローカルテスト
```

## 環境（ステージング / 本番）

同一スタック定義を環境ごとにインスタンス化し、**API・Lambda・DynamoDBテーブルを完全に分離**しています。
環境ごとの差分は `app.py` の `ENVIRONMENTS`（`EnvironmentConfig`）に集約されています。

| 項目 | staging | prod |
| --- | --- | --- |
| スタック名 | `SensorStack-staging` | `SensorStack-prod` |
| APIステージ名 | `staging` | `prod` |
| エンドポイント | `https://{api-id}.execute-api.ap-northeast-1.amazonaws.com/staging` | `.../prod` |
| DynamoDBテーブル | `SensorStates-staging` | `SensorStates-prod` |
| テーブル削除ポリシー | `DESTROY`（destroyで消える） | `RETAIN`（destroyしても残る） |
| PITR（復旧） | 無効 | 有効 |
| ログ保持 | 1週間 / `DEBUG` | 1ヶ月 / `INFO` |
| スロットリング | 10 rps（バースト20） | 100 rps（バースト200） |
| Lambdaメモリ | 256 MB | 512 MB |
| APIキー | 環境ごとに別のシークレットを自動生成 | 同左 |

全リソースに `Environment` / `Application` タグが付与されます。

## API仕様

| メソッド | パス | 説明 | レスポンス |
| --- | --- | --- | --- |
| POST | `/sensors/{sensor_id}/state` | ボディのJSONに `updated_at` を自動付与して保存（upsert） | `200 OK` |
| GET | `/sensors/{sensor_id}/state` | 指定センサーの最新状態を取得 | `200 OK` / `404 Not Found` |

- 任意のJSON属性（`temperature`, `humidity`, `battery` など）をそのまま格納できます。
- ボディ内の `sensor_id` / `updated_at` は無視され、パスパラメータとサーバー時刻で上書きされます。
- 認証は `x-api-key` ヘッダー方式です（下記参照）。キー不一致・未指定は `401`、不正JSONは `400` を返します。

### 認証について

API Gateway の「APIキー + 使用量プラン」は **REST API 専用** の機能で、HTTP API では利用できません。
そのため本プロジェクトでは、デプロイ時に Secrets Manager が自動生成したランダムキーを
`x-api-key` カスタムヘッダーで検証する方式を採用しています（Lambda 内で `hmac.compare_digest` により比較）。

## 前提ツール

| ツール | バージョン目安 | 確認コマンド |
| --- | --- | --- |
| Python | 3.9以上（Lambdaランタイムは3.12） | `python3 --version` |
| Node.js | 22以上（CDK CLIの実行に必要） | `node --version` |
| AWS CDK CLI | v2 | `npx aws-cdk@2 --version` |
| AWS CLI | v2 | `aws --version` |

AWS 認証情報が設定済みであること（`aws sts get-caller-identity` が成功すること）。

## セットアップ & デプロイ

```bash
# 1. 仮想環境の作成と依存関係のインストール
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. CDK CLI（グローバルに入れる場合。npx で都度実行してもOK）
npm install -g aws-cdk

# 3. CDKブートストラップ（アカウント・リージョンごとに初回1回のみ）
cdk bootstrap aws://$(aws sts get-caller-identity --query Account --output text)/ap-northeast-1

# 4. スタック一覧と差分確認（任意）
cdk list                       # SensorStack-staging / SensorStack-prod
cdk diff SensorStack-staging

# 5. デプロイ（まずステージング、確認後に本番）
cdk deploy SensorStack-staging
cdk deploy SensorStack-prod

# 両方まとめてデプロイする場合
cdk deploy --all
```

> **注意:** `cdk deploy --all` は本番にも同時に反映されます。通常は staging → 動作確認 → prod の順で個別にデプロイしてください。

デプロイ完了時、スタックごとに以下が Outputs として表示されます。

- `SensorStack-<env>.ApiEndpoint` — APIのベースURL（ステージ名まで含む）
- `SensorStack-<env>.ApiKeySecretArn` — APIキーが格納されたシークレットのARN
- `SensorStack-<env>.TableName` — DynamoDBテーブル名

## 動作確認（curl）

### 0. 環境変数のセットアップ

`ENV` を `staging` / `prod` で切り替えます。**まずはこのブロックを実行**してください。

```bash
export AWS_DEFAULT_REGION=ap-northeast-1
ENV=staging                      # ← prod にすれば本番を対象にできます
STACK=SensorStack-$ENV

API_URL=$(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)
SECRET_ARN=$(aws cloudformation describe-stacks --stack-name $STACK \
  --query "Stacks[0].Outputs[?OutputKey=='ApiKeySecretArn'].OutputValue" --output text)
API_KEY=$(aws secretsmanager get-secret-value --secret-id "$SECRET_ARN" \
  --query SecretString --output text | python3 -c 'import json,sys; print(json.load(sys.stdin)["api_key"])')

echo "$API_URL"
```

`jq` 派であれば最後の `python3 -c ...` は `jq -r .api_key` に置き換えられます。

> **注意**
> - URLには**ステージ名（`/staging` または `/prod`）が必須**です。省略すると API Gateway が 404 を返します。
> - APIキーは環境ごとに別物です。`ENV` を切り替えたら**セットアップブロックを再実行**してください（staging のキーで prod を叩くと 401）。

### 1. 状態の登録 (POST)

```bash
curl -s -X POST "$API_URL/sensors/sensor-001/state" \
  -H "x-api-key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "status": "ok",
        "temperature": 23.5,
        "humidity": 48.2,
        "battery": 87
      }'
```

レスポンス例:

```json
{
  "message": "ok",
  "sensor_id": "sensor-001",
  "updated_at": "2026-10-22T04:12:34.567Z",
  "item": {
    "status": "ok",
    "temperature": 23.5,
    "humidity": 48.2,
    "battery": 87,
    "sensor_id": "sensor-001",
    "updated_at": "2026-10-22T04:12:34.567Z"
  }
}
```

任意のJSONを格納できます（ネストや配列もそのまま保存されます）。

```bash
curl -s -X POST "$API_URL/sensors/sensor-002/state" \
  -H "x-api-key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"status":"warning","location":{"lat":35.681,"lon":139.767},"tags":["室内","1F"]}'
```

### 2. 状態の取得 (GET)

```bash
curl -s "$API_URL/sensors/sensor-001/state" -H "x-api-key: $API_KEY"
```

### 3. エラーケースの確認

```bash
# 404: 未登録のセンサー
curl -s -w " [%{http_code}]\n" "$API_URL/sensors/unknown/state" -H "x-api-key: $API_KEY"

# 401: APIキーなし
curl -s -w " [%{http_code}]\n" "$API_URL/sensors/sensor-001/state"

# 401: APIキー誤り
curl -s -w " [%{http_code}]\n" "$API_URL/sensors/sensor-001/state" -H "x-api-key: wrong"

# 400: 不正なJSON
curl -s -w " [%{http_code}]\n" -X POST "$API_URL/sensors/sensor-001/state" \
  -H "x-api-key: $API_KEY" -H "Content-Type: application/json" -d '{not-json'
```

### 4. 便利な小技

```bash
# HTTPステータスも一緒に表示
curl -s -w "\nHTTP %{http_code}\n" "$API_URL/sensors/sensor-001/state" -H "x-api-key: $API_KEY"

# レスポンスを整形
curl -s "$API_URL/sensors/sensor-001/state" -H "x-api-key: $API_KEY" | python3 -m json.tool

# ヘッダーも含めて確認
curl -i "$API_URL/sensors/sensor-001/state" -H "x-api-key: $API_KEY"
```

### 5. DynamoDBを直接確認

```bash
aws dynamodb get-item --table-name SensorStates-$ENV \
  --key '{"sensor_id": {"S": "sensor-001"}}'

aws dynamodb scan --table-name SensorStates-$ENV
```

## URL（API ID）を作り直す

エンドポイントURLを誤って公開してしまった場合など、API GatewayのAPI IDを変更したいときは
`app.py` の該当環境の `api_revision` をインクリメントして再デプロイします。

```bash
# app.py の api_revision を 2 -> 3 などに変更したうえで
cdk diff SensorStack-staging     # 置き換え対象がAPI Gateway関連のみであることを確認
cdk deploy SensorStack-staging
```

- 新しいAPIが作成され、**旧APIは削除される**ため、旧URLは即座に到達不能になります。
- DynamoDBテーブル・Lambda関数・APIキーのシークレットは**置き換わらず、データも保持されます**。
- 置き換え中は短時間ダウンタイムが発生します（旧API削除〜新API作成の間）。

> **注意:** エンドポイントURLはコミットしないでください。URL自体は認証情報ではありませんが、
> 一度GitHubへpushすると履歴から消えません。URLは常に CloudFormation の Outputs から取得してください
> （「動作確認（curl）」の手順0を参照）。
>
> APIキーをローテーションしたい場合は、シークレットを新しい値で更新します（Lambdaは最大5分でキャッシュが切れて追従）。
>
> ```bash
> aws secretsmanager put-secret-value --secret-id "$SECRET_ARN" \
>   --secret-string "{\"header\":\"x-api-key\",\"api_key\":\"$(openssl rand -hex 20)\"}"
> ```

## ローカルテスト

```bash
pip install "moto[dynamodb,secretsmanager]" boto3 pytest
pytest tests/ -q
```

## クリーンアップ

```bash
cdk destroy SensorStack-staging    # テーブルごと削除される
cdk destroy SensorStack-prod       # テーブルは残る（RETAIN）
```

本番のDynamoDBテーブル（`SensorStates-prod`）は `RemovalPolicy.RETAIN` のため **削除されずに残ります**。
不要であれば手動で削除するか、`app.py` の prod 設定 `table_removal_policy` を
`RemovalPolicy.DESTROY` に変更してから再デプロイ・削除してください。

## 補足

- DynamoDBはオンデマンドキャパシティ（`PAY_PER_REQUEST`）＋ポイントインタイムリカバリ有効。
- Lambdaのロググループは保持期間1週間、スタック削除時に削除されます。
- GETは `ConsistentRead=True`（強い整合性のある読み込み）で、POST直後の値が確実に取得できます。
- Lambdaに付与しているIAM権限は、対象テーブルの読み書きとAPIキーシークレットの読み取りのみです。
