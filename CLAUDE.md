# センサー状態管理API 構築プロジェクト プロンプト

以下の要件と仕様に基づき、AWS CDK (Python) を用いたインフラ定義コードおよび、PythonによるLambdaハンドラーの実装コードを一式作成してください。

## 1. プロジェクト概要
- **目的:** HTTPリクエスト（POST/GET）を通じて、複数のセンサーの状態（ステータスや計測値など）を登録・取得するサーバーレスAPIの構築。
- **インフラ管理:** AWS CDK (Python) を使用し、AWSリソースを一括で構築・デプロイできるようにする。

## 2. アーキテクチャおよび使用技術
- **API Gateway:** Amazon API Gateway (HTTP API)
- **Compute:** AWS Lambda (Python 3.11以上)
- **Database:** Amazon DynamoDB (オンデマンドキャパシティ)
- **認証:** API GatewayのAPIキー（または簡易的なカスタムヘッダー認証）

## 3. データモデル仕様 (DynamoDB)
- **テーブル名:** `SensorStates`
- **パーティションキー (PK):** `sensor_id` (String)
- **属性例:** 
  - `sensor_id` (String)
  - `updated_at` (String / ISO8601形式のタイムスタンプ)
  - `status` (String)
  - その他任意のセンサー計測値（温度、湿度、バッテリー残量など、柔軟にJSONを受け取って格納できるようにする）

## 4. API 仕様
### 4.1. 状態登録 (POST)
- **パス:** `POST /sensors/{sensor_id}/state`
- **動作:** リクエストボディのJSONデータに `updated_at`（現在時刻）を自動付与し、DynamoDBに保存する。成功時は `200 OK` を返す。

### 4.2. 状態取得 (GET)
- **パス:** `GET /sensors/{sensor_id}/state`
- **動作:** 指定された `sensor_id` の最新状態をDynamoDBから取得し、JSONで返す。存在しない場合は `404 Not Found` を返す。

## 5. ディレクトリ構成の要件
以下の構成でプロジェクトファイルを作成してください。

```text
sensor-api-infra/
├── app.py                  # CDKエントリーポイント
├── cdk.json                # CDK設定ファイル
├── requirements.txt        # CDK依存関係
├── lambda/                 # Lambda関数コード
│   ├── requirements.txt
│   └── handler.py          # POST / GET のルーティングおよびDynamoDB操作ロジック
└── stacks/
    └── sensor_stack.py     # API Gateway, Lambda, DynamoDBの定義

## 6. 出力してほしい内容

1. **プロジェクトファイル一式:**
   - `app.py`
   - `cdk.json`
   - `requirements.txt`
   - `stacks/sensor_stack.py`
   - `lambda/requirements.txt`
   - `lambda/handler.py`
   各ファイルの完全なコードを省略せずに記述してください。

2. **デプロイおよび動作確認手順:**
   - 必要な前提ツール（AWS CLI, Node.js, Python, AWS CDKなど）
   - 初期セットアップから `cdk deploy` までの具体的なコマンド
   - `curl` コマンドなどを用いた `POST`（登録）および `GET`（取得）の動作確認用サンプルコード
