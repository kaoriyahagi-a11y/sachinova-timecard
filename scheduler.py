"""APScheduler ジョブ：退勤忘れアラート + 自動バックアップ + 月次リマインド"""
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import config


def _check_forgot_clockout():
    """22:00 に退勤忘れをチェック"""
    try:
        import sheets_manager
        import line_notifier
        flagged = sheets_manager.flag_forgot_clockout()
        if flagged:
            line_notifier.notify_forgot_clockout(flagged)
            print(f"[scheduler] 退勤忘れアラート送信: {len(flagged)}件")
        else:
            print("[scheduler] 退勤忘れなし")
    except Exception as e:
        print(f"[scheduler] 退勤忘れチェックエラー: {e}")


def _run_backup():
    """深夜2:00 にバックアップ作成"""
    if not config.BACKUP_ENABLED:
        return
    try:
        import sheets_manager
        name = sheets_manager.create_backup()
        print(f"[scheduler] バックアップ作成: {name}")
        sheets_manager.cleanup_old_backups()
        print("[scheduler] 古いバックアップ削除完了")
    except Exception as e:
        print(f"[scheduler] バックアップエラー: {e}")
        try:
            import line_notifier
            line_notifier.notify_backup_failure(str(e))
        except Exception:
            pass


def _monthly_reminder():
    """毎月25日と月末の10:00に月次締めリマインド"""
    try:
        import line_notifier
        line_notifier.notify_monthly_reminder()
        print("[scheduler] 月次締めリマインド送信")
    except Exception as e:
        print(f"[scheduler] 月次リマインドエラー: {e}")


def init_scheduler():
    tz = pytz.timezone(config.TIMEZONE)
    scheduler = BackgroundScheduler(timezone=tz)

    # 毎日22:00 退勤忘れチェック
    scheduler.add_job(
        _check_forgot_clockout,
        "cron", hour=22, minute=0,
        id="forgot_clockout", replace_existing=True,
    )

    # 毎日2:00 バックアップ
    scheduler.add_job(
        _run_backup,
        "cron", hour=2, minute=0,
        id="daily_backup", replace_existing=True,
    )

    # 毎月25日 10:00 月次リマインド
    scheduler.add_job(
        _monthly_reminder,
        "cron", day=25, hour=10, minute=0,
        id="monthly_reminder_25", replace_existing=True,
    )

    # 毎月末日 10:00 月次リマインド
    scheduler.add_job(
        _monthly_reminder,
        "cron", day="last", hour=10, minute=0,
        id="monthly_reminder_last", replace_existing=True,
    )

    scheduler.start()
    print("[scheduler] APScheduler起動完了")
    print("  - 退勤忘れチェック: 毎日 22:00")
    print("  - 自動バックアップ: 毎日 02:00")
    print("  - 月次リマインド: 毎月25日・月末 10:00")
    return scheduler
