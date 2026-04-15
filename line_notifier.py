"""LINE Messaging API 通知ヘルパー（非同期送信）"""
import threading
import requests
import config

PUSH_URL = "https://api.line.me/v2/bot/message/push"
MULTICAST_URL = "https://api.line.me/v2/bot/message/multicast"


def _headers():
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}",
    }


def _send_push(user_id, text):
    """1人にpush送信"""
    try:
        requests.post(
            PUSH_URL,
            headers=_headers(),
            json={
                "to": user_id,
                "messages": [{"type": "text", "text": text}],
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[LINE] push送信エラー ({user_id[:8]}...): {e}")


def _send_multicast(user_ids, text):
    """複数人にmulticast送信"""
    try:
        requests.post(
            MULTICAST_URL,
            headers=_headers(),
            json={
                "to": user_ids,
                "messages": [{"type": "text", "text": text}],
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[LINE] multicast送信エラー: {e}")


def send_line_notification(notification_type, message, user_ids=None):
    """
    LINE通知を非同期で送信。
    notification_type: LINE_NOTIFY_SETTINGSのキー
    message: 送信テキスト
    user_ids: 送信先（Noneなら管理者全員）
    """
    if not config.LINE_MESSAGING_ENABLED:
        print(f"[LINE] 無効 (type={notification_type}): {message[:50]}...")
        return

    if not config.LINE_CHANNEL_ACCESS_TOKEN:
        print(f"[LINE] トークン未設定 (type={notification_type})")
        return

    # 通知タイプのON/OFFチェック
    if not config.LINE_NOTIFY_SETTINGS.get(notification_type, False):
        print(f"[LINE] 通知OFF (type={notification_type})")
        return

    targets = user_ids or config.LINE_ADMIN_USER_IDS
    if not targets:
        print(f"[LINE] 送信先なし (type={notification_type})")
        return

    # 非同期で送信（打刻レスポンスを遅らせない）
    def _do_send():
        if len(targets) == 1:
            _send_push(targets[0], message)
        else:
            _send_multicast(targets, message)
        print(f"[LINE] 送信完了 (type={notification_type}, to={len(targets)}人)")

    t = threading.Thread(target=_do_send, daemon=True)
    t.start()


# ━━━━━ 便利関数 ━━━━━

def notify_forgot_clockout(entries):
    """退勤忘れアラート"""
    if not entries:
        return
    lines = ["⚠️ 退勤忘れの可能性"]
    for e in entries:
        lines.append(f"・{e['name']}（{e['store']}）/ 出勤{e['clock_in']} / 経過{e['hours']}時間")
    send_line_notification("forgot_clock_out", "\n".join(lines))


def notify_late_night_punch(emp_name, store_name, time_str, punch_type):
    """深夜異常打刻（AM2-5時）"""
    msg = f"🌙 深夜の打刻を検知\n{emp_name}（{store_name}）\n{time_str} / 種別：{punch_type}"
    send_line_notification("late_night_punch", msg)


def notify_pin_failure(emp_name, count):
    """PIN連続ミス"""
    msg = f"🔒 PIN連続ミス検知\n{emp_name} / {count}回失敗 / {config.PIN_LOCK_MINUTES}分ロック"
    send_line_notification("pin_failure", msg)


def notify_monthly_reminder():
    """月次締め日リマインド"""
    msg = "📅 月次締めリマインド\n□ 打刻漏れチェック\n□ MF CSV出力\n□ バックアップ確認"
    send_line_notification("monthly_reminder", msg)


def notify_backup_failure(error_msg):
    """バックアップ失敗"""
    msg = f"🔴 バックアップ失敗\nエラー：{error_msg}\n手動実施してください"
    send_line_notification("backup_failure", msg)


def notify_new_employee_first_punch(emp_name, store_name, time_str):
    """新規従業員初打刻"""
    msg = f"🎉 新メンバー初出勤\n{emp_name}（{store_name}）{time_str}"
    send_line_notification("new_employee_first", msg)
