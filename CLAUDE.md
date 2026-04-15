# Sachinova タイムカードシステム

Sachinova株式会社（飲食店2店舗）の従業員向けタイムカードWebアプリ。

## 技術スタック

- **バックエンド**: Python 3.14 + Flask 3.1
- **データストア**: Google Sheets（gspread経由）
- **スケジューラ**: APScheduler（退勤忘れ22:00、バックアップ02:00、月次リマインド25日/月末）
- **通知**: LINE Messaging API（異常時のみ6種、通常出退勤は通知しない）
- **フロントエンド**: バニラJS + CSS（フレームワークなし）
- **デプロイ**: Render（gunicorn）

## ファイル構成

```
app.py              Flaskメインアプリ（全ルート）
config.py           設定（環境変数 or デフォルト値）
sheets_manager.py   Google Sheets CRUD + メモリキャッシュ + 打刻ステートマシン
line_notifier.py    LINE Messaging API通知（非同期送信）
scheduler.py        APSchedulerジョブ定義
templates/          Jinja2テンプレート（14ファイル）
static/             CSS + JS + 従業員写真
credentials.json    Google サービスアカウントキー（※Git管理外）
```

## 画面遷移

### 従業員フロー
```
店舗選択 → 店舗パスワード（1日1回、AM4時リセット）
  → 従業員タイル一覧（該当店舗のみ、ステータス表示付き）
    → 4桁PIN入力（ハッシュ化保存、5回ミスで10分ロック）
      → 打刻画面（状態APIでボタン制御）
        → トースト表示 → 3秒後に一覧へ自動復帰
```

### 管理者フロー
```
/admin → メール+パスワードログイン
  → ダッシュボード（店舗別リアルタイム出勤状況、30秒自動更新）
  → 従業員管理（CRUD + PIN + 写真 + 有効/無効）
  → 打刻履歴編集（修正・追加・削除）
  → 要確認タブ（退勤忘れフラグ）
  → 月次レポート（店舗フィルタ + CSV + MF給与CSV）
  → LINE設定（Webhook User ID取得 + テスト通知）
```

## 打刻ステートマシン（重要）

1日に複数シフト（ダブルシフト）対応。1レコード = 1シフト。

```
未出勤      → 出勤のみ可
勤務中      → 退勤 or 休憩開始のみ可
休憩中      → 休憩終了のみ可（退勤は不可 ← 過去のバグ修正箇所）
退勤済み    → 出勤で新シフト開始可（前シフトから15分以上経過必要）
```

- 状態判定: `sheets_manager.get_allowed_actions(eid)` → `(allowed_actions, status_label, shift_num)`
- API: `GET /api/punch/status?employee_id=xxx` でJSON返却
- UI: 全ボタン初期disabled → API fetchで有効化（サーバーサイドレンダリングに依存しない）

## キャッシュ戦略

- 従業員マスタ: 5分メモリキャッシュ（管理者更新時にクリア）
- 今日の打刻: 30秒キャッシュ（打刻操作時にクリア、status APIでもクリア）
- gspread接続: 30分キャッシュ
- PINミス回数: メモリ保持（再起動でリセット）

## 環境変数

本番では以下を環境変数で設定:
```
SECRET_KEY, GOOGLE_CREDENTIALS_JSON,
STORE_PW_KIYOSUMI, STORE_PW_HIKIFUNE,
LINE_MESSAGING_ENABLED, LINE_CHANNEL_ACCESS_TOKEN, LINE_ADMIN_USER_IDS,
BACKUP_ENABLED
```

`GOOGLE_CREDENTIALS_JSON` はRender用（JSONを文字列で環境変数に格納）。ローカルでは `credentials.json` ファイルを使用。

## 重要な注意事項

- HTTPヘッダーに日本語を含めない（CSVファイル名はRFC 5987 filename*=UTF-8'' を使用）
- `sheets_manager.py` の関数名を変更したら `app.py` の全呼び出し箇所を必ず更新
- 打刻画面のボタン制御はAPI駆動（テンプレート変数埋め込みではなくfetchで取得）
- LINE通知は `threading` で非同期送信（打刻レスポンスを遅らせない）
- credentials.json は絶対にGitにコミットしない
