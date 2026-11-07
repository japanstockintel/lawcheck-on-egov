# GitHub × Cloud Run × Cloudflare 連携アーキテクチャ仕様書

## 1. 概要と目的
本ドキュメントは、「e-Gov直結 法令整合性チェッカー」におけるインフラおよびデプロイメントパイプラインの全体像を整理したものである。

本システムでは、維持費0円〜極小コスト、メンテナンスフリー、かつ最高水準のセキュリティと独自ドメイン運用を実現するため、**「GitHub（コード管理・CI/CD）」「Google Cloud Run（実行エンジン）」「Cloudflare（玄関・DNS・セキュリティ）」**の3者を連携させたサーバーレスアーキテクチャを採用している。

---

## 2. コンポーネント別の役割分担

| コンポーネント | 主な役割 | 概要と技術的メリット |
| :--- | :--- | :--- |
| **GitHub** | **ソースコードの管理・保管庫** | ・`app.py` や `Dockerfile` などの全プログラムコードをバージョン管理<br>・リポジトリへの Push を検知し、Cloud Build へ自動ビルドのトリガーを送信 |
| **Google Cloud Run** | **アプリケーション実行基盤** | ・Streamlit コンテナを実行し、e-Gov API v2 や Gemini API と通信<br>・リクエストがない時は自動スリープ（Scale-to-Zero）し、無駄なサーバー費用を完全カット |
| **Cloudflare** | **エッジ・DNS・セキュリティ・ドメイン管理** | ・独自ドメインのルーティングおよび自動 SSL/TLS 暗号化通信の提供<br>・WAF / DDoS 防御および Cloudflare Workers による AdSense / 解析タグの動的挿入 |

---

## 3. システムフロー

### 3.1 開発・デプロイフロー（コード変更時）
```
[ 開発者 (Colab / ローカル) ]
         │
         ▼ Git Push
[ GitHub リポジトリ ]
         │
         ▼ Webhook 通知
[ Google Cloud Build ] ──(コンテナビルド)──► [ Artifact Registry ]
                                                    │
                                                    ▼ 自動デプロイ
                                          [ Google Cloud Run ]
```
1. 開発者が更新したコード（`app.py` 等）を **GitHub** の main ブランチへ Commit / Push する。
2. GitHub の更新をフックして **Google Cloud Build** が起動し、自動的に Docker コンテナイメージをビルドする。
3. ビルドされたイメージが **Google Cloud Run** にデプロイされ、本番環境が最新化される。

---

### 3.2 ユーザーアクセスフロー（Webサイト利用時）
```
[ ユーザー (ブラウザ) ]
         │
         ▼ 独自ドメインアクセス (https://...)
[ Cloudflare (エッジ / DNS / セキュリティ) ]
   ├─ SSL暗号化 & WAF防御
   └─ Workers (AdSense / 解析タグを動的挿入)
         │
         ▼ 暗号化プロキシ転送
[ Google Cloud Run (Streamlit アプリエンジン) ]
   ├─ e-Gov 法令 API v2 (公式条文XML取得)
   └─ Google Gemini 3.8 Flash (法的監査・添削生成)
```
1. ユーザーが独自ドメインへアクセスすると、まず最前線の **Cloudflare** がリクエストを受信。
2. Cloudflare が SSL 通信を処理し、セキュリティチェックおよび Workers によるHTMLタグ（AdSense等）の動的挿入を実施。
3. リクエストが **Cloud Run** 上の Streamlit アプリへ転送され、e-Gov API および Gemini API と連携して迅速に法令監査結果を返す。

---

## 4. この3連携構成を採用する理由とメリット

1. **完全スリープ（Scale-to-Zero）によるコスト最小化**  
   アクセスがない時間帯は Cloud Run のインスタンスが0になり、課金が発生しません。個人開発やスタートアップに最適な「実質0円運用」が可能です。
2. **アプリケーションコードの純粋性の維持**  
   AdSense 審査用タグや Google Analytics などの計測コードを Streamlit の Python コード内に埋め込む必要がなく、Cloudflare Workers 側で動的に挿入できるため、`app.py` のメンテナンス性が高く保たれます。
3. **高次元のセキュリティと信頼性**  
   Cloud Run の標準URLを外部に直接露出させず、Cloudflare の強力な DDoS 防御と SSL 通信を前面に立てることで、B2B 利用にも耐えうる堅牢な法的Webツールとして運用可能です。
