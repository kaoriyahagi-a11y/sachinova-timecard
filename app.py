import os
import json
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, Response, jsonify,
)
from functools import wraps
from datetime import datetime
import csv
import io
from urllib.parse import quote
import config
import sheets_manager
import line_notifier

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# 写真フォルダ確保
os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)

# Webhook受信User IDの一時保管（メモリ内）
_webhook_received_ids = []  # [{"user_id": "Uxxx", "timestamp": "2026-04-13 10:00:00", "display_name": ""}]


# ━━━━━ ヘルパー ━━━━━

def _is_late_night():
    """現在がAM2-5時かチェック"""
    h = datetime.now().hour
    return 2 <= h < 5


# ━━━━━ デコレータ ━━━━━

def _store_session_valid():
    auth_date = session.get("store_auth_date")
    if not auth_date:
        return False
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if now.hour < config.SESSION_RESET_HOUR:
        return auth_date == today
    return auth_date == today


def store_auth_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _store_session_valid():
            session.pop("store_id", None)
            session.pop("store_auth_date", None)
            return redirect(url_for("store_select"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  従業員フロー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route("/")
def index():
    if _store_session_valid():
        return redirect(url_for("employee_select"))
    return redirect(url_for("store_select"))


# --- 店舗選択 ---

@app.route("/store", methods=["GET"])
def store_select():
    if _store_session_valid():
        return redirect(url_for("employee_select"))
    return render_template("store_select.html", stores=config.STORE_NAMES)


@app.route("/store/<store_id>/password", methods=["GET", "POST"])
def store_password(store_id):
    if store_id not in config.STORE_PASSWORDS:
        flash("無効な店舗です", "error")
        return redirect(url_for("store_select"))

    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == config.STORE_PASSWORDS[store_id]:
            session["store_id"] = store_id
            session["store_auth_date"] = datetime.now().strftime("%Y-%m-%d")
            return redirect(url_for("employee_select"))
        flash("パスワードが正しくありません", "error")

    store_name = config.STORE_NAMES.get(store_id, store_id)
    return render_template("store_password.html", store_id=store_id, store_name=store_name)


@app.route("/store/change")
def store_change():
    """店舗切り替え — セッションをリセットして店舗選択に戻る"""
    session.pop("store_id", None)
    session.pop("store_auth_date", None)
    session.pop("punch_eid", None)
    session.pop("punch_name", None)
    session.pop("punch_store", None)
    return redirect(url_for("store_select"))


# --- 従業員タイル ---

@app.route("/select")
@store_auth_required
def employee_select():
    store_id = session["store_id"]
    employees = sheets_manager.get_employees_by_store(store_id)

    for emp in employees:
        emp["_status"] = sheets_manager.get_employee_status(emp["ID"])

    order = {"working": 0, "break": 1, "none": 2, "shift_done": 3}
    employees.sort(key=lambda e: order.get(e["_status"], 9))

    store_name = config.STORE_NAMES.get(store_id, store_id)
    return render_template(
        "employee_select.html",
        employees=employees,
        store_name=store_name,
        store_id=store_id,
    )


# --- PIN ---

@app.route("/pin/<employee_id>")
@store_auth_required
def pin_entry(employee_id):
    emp = sheets_manager.get_employee_by_id(employee_id)
    if not emp:
        flash("従業員が見つかりません", "error")
        return redirect(url_for("employee_select"))

    locked = sheets_manager.is_pin_locked(employee_id)
    return render_template("pin_entry.html", employee=emp, locked=locked)


@app.route("/pin/<employee_id>/verify", methods=["POST"])
@store_auth_required
def pin_verify(employee_id):
    # ロックチェック
    if sheets_manager.is_pin_locked(employee_id):
        return jsonify({
            "success": False,
            "message": f"PINがロックされています。{config.PIN_LOCK_MINUTES}分後に再試行してください",
        }), 403

    pin = request.form.get("pin", "")
    emp = sheets_manager.authenticate_pin(employee_id, pin)
    if emp:
        sheets_manager.clear_pin_failures(employee_id)
        session["punch_eid"] = str(emp["ID"])
        session["punch_name"] = emp["名前"]
        session["punch_store"] = emp.get("店舗ID", session.get("store_id", ""))
        return jsonify({"success": True})

    # PIN失敗記録
    reached_limit = sheets_manager.record_pin_failure(employee_id)
    count = sheets_manager.get_pin_failure_count(employee_id)
    if reached_limit:
        emp_info = sheets_manager.get_employee_by_id(employee_id)
        emp_name = emp_info["名前"] if emp_info else employee_id
        line_notifier.notify_pin_failure(emp_name, count)
        return jsonify({
            "success": False,
            "message": f"PIN {count}回連続ミス。{config.PIN_LOCK_MINUTES}分間ロックされました",
        }), 403

    remaining = config.PIN_MAX_FAILURES - count
    return jsonify({
        "success": False,
        "message": f"PINが正しくありません（残り{remaining}回）",
    }), 401


# --- 打刻 ---

@app.route("/punch")
@store_auth_required
def punch():
    eid = session.get("punch_eid")
    name = session.get("punch_name")
    if not eid:
        return redirect(url_for("employee_select"))
    # HTMLのみ返す。状態は /api/punch/status から取得
    return render_template("punch.html", employee_id=eid, employee_name=name)


@app.route("/api/punch/status")
@store_auth_required
def api_punch_status():
    """打刻状態API — クライアントからfetchで呼び出される"""
    eid = request.args.get("employee_id", session.get("punch_eid", ""))
    if not eid:
        return jsonify({"error": "employee_id required"}), 400

    sheets_manager.clear_today_cache()  # 最新データを取得
    allowed, status_label, shift_num = sheets_manager.get_allowed_actions(eid)
    shifts = sheets_manager.get_today_shifts_for(eid)

    # シフトをJSON安全な形に変換
    today_shifts = []
    for i, s in enumerate(shifts, 1):
        today_shifts.append({
            "shift_id": i,
            "clock_in": s.get("出勤", "") or None,
            "clock_out": s.get("退勤", "") or None,
            "break_start": s.get("休憩開始", "") or None,
            "break_end": s.get("休憩終了", "") or None,
            "work_hours": s.get("勤務時間", "") or None,
        })

    # 最後の打刻情報
    last_punch = None
    if shifts:
        last = shifts[-1]
        if last.get("退勤"):
            last_punch = {"type": "退勤", "time": last["退勤"]}
        elif last.get("休憩終了"):
            last_punch = {"type": "休憩終了", "time": last["休憩終了"]}
        elif last.get("休憩開始"):
            last_punch = {"type": "休憩開始", "time": last["休憩開始"]}
        elif last.get("出勤"):
            last_punch = {"type": "出勤", "time": last["出勤"]}

    # ステータスを英語キーにマッピング
    status_map = {
        "none": "not_working",
        "working": "working",
        "break": "on_break",
        "shift_done": "finished",
    }
    raw_status = sheets_manager.get_employee_status(eid)

    result = {
        "status": status_map.get(raw_status, "not_working"),
        "status_label": status_label,
        "current_shift": shift_num,
        "last_punch": last_punch,
        "today_shifts": today_shifts,
        "allowed_actions": allowed,
    }
    print(f"[API] /api/punch/status eid={eid} → status={result['status']} allowed={allowed}")
    return jsonify(result)


@app.route("/punch/action", methods=["POST"])
@store_auth_required
def punch_action():
    eid = session.get("punch_eid")
    name = session.get("punch_name")
    store_id = session.get("punch_store", session.get("store_id", ""))
    if not eid:
        return jsonify({"success": False, "message": "認証が必要です"}), 401

    action = request.form.get("action", "")

    # サーバー側バリデーション: allowed_actions チェック
    allowed, _, _ = sheets_manager.get_allowed_actions(eid)
    if action not in allowed:
        return jsonify({"success": False, "message": "現在この操作はできません"}), 400

    time_str = None
    error = None
    work_hours_str = None

    if action == "clock_in":
        is_first = sheets_manager.is_first_punch(eid)
        time_str, error = sheets_manager.clock_in(eid, name, store_id)
        label = "出勤"
        if time_str:
            if is_first:
                store_name = config.STORE_NAMES.get(store_id, store_id)
                line_notifier.notify_new_employee_first_punch(name, store_name, time_str)
            if _is_late_night():
                store_name = config.STORE_NAMES.get(store_id, store_id)
                line_notifier.notify_late_night_punch(name, store_name, time_str, "出勤")
    elif action == "clock_out":
        time_str, error, work_hours_str = sheets_manager.clock_out(eid)
        label = "退勤"
        if time_str and _is_late_night():
            store_name = config.STORE_NAMES.get(store_id, store_id)
            line_notifier.notify_late_night_punch(name, store_name, time_str, "退勤")
    elif action == "break_start":
        time_str, error = sheets_manager.break_start(eid)
        label = "休憩開始"
    elif action == "break_end":
        time_str, error = sheets_manager.break_end(eid)
        label = "休憩終了"
    else:
        return jsonify({"success": False, "message": "不正な操作です"}), 400

    if error:
        return jsonify({"success": False, "message": error})

    # 打刻成功 — セッションクリアはしない（状態再取得のため）
    return jsonify({
        "success": True,
        "message": f"{name}さん {label}を記録しました",
        "time": time_str,
    })


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  管理者フロー
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route("/admin")
def admin_index():
    if session.get("admin_id"):
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("admin_login"))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_id"):
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = sheets_manager.authenticate_admin(email, password)
        if user:
            session["admin_id"] = str(user["ID"])
            session["admin_name"] = user["名前"]
            return redirect(url_for("admin_dashboard"))
        flash("メールアドレスまたはパスワードが間違っています", "error")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_id", None)
    session.pop("admin_name", None)
    return redirect(url_for("admin_login"))


# --- ダッシュボード ---

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    dashboard = sheets_manager.get_store_dashboard()
    return render_template("admin_dashboard.html", dashboard=dashboard)


@app.route("/api/admin/dashboard")
@admin_required
def api_admin_dashboard():
    sheets_manager.clear_today_cache()
    dashboard = sheets_manager.get_store_dashboard()
    return jsonify(dashboard)


# --- 従業員管理 ---

@app.route("/admin/employees")
@admin_required
def admin_employees():
    employees = sheets_manager.get_all_employees()
    return render_template("admin_employees.html", employees=employees, stores=config.STORE_NAMES)


@app.route("/admin/employees/add", methods=["POST"])
@admin_required
def admin_add_employee():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "employee")
    store_id = request.form.get("store_id", "kiyosumi")
    pin = request.form.get("pin", "0000").strip()

    if not name or not email or not password:
        flash("名前・メール・パスワードは必須です", "error")
        return redirect(url_for("admin_employees"))
    if len(pin) != 4 or not pin.isdigit():
        flash("PINは4桁の数字で入力してください", "error")
        return redirect(url_for("admin_employees"))
    if sheets_manager.get_employee_by_email(email):
        flash("このメールアドレスは既に登録されています", "error")
        return redirect(url_for("admin_employees"))

    sheets_manager.add_employee(name, email, password, role, store_id, pin)
    flash(f"従業員「{name}」を追加しました", "success")
    return redirect(url_for("admin_employees"))


@app.route("/admin/employees/<eid>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_employee(eid):
    emp = sheets_manager.get_employee_by_id(eid)
    if not emp:
        flash("従業員が見つかりません", "error")
        return redirect(url_for("admin_employees"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        store_id = request.form.get("store_id", "kiyosumi")
        role = request.form.get("role", "employee")
        pin = request.form.get("pin", "").strip()
        active = "1" if request.form.get("active") else "0"

        updates = {"name": name, "store_id": store_id, "role": role, "active": active}
        if pin:
            if len(pin) != 4 or not pin.isdigit():
                flash("PINは4桁の数字で入力してください", "error")
                return redirect(url_for("admin_edit_employee", eid=eid))
            updates["pin"] = pin

        photo = request.files.get("photo")
        if photo and photo.filename:
            from PIL import Image
            try:
                img = Image.open(photo.stream)
                img.thumbnail((config.PHOTO_MAX_DIMENSION, config.PHOTO_MAX_DIMENSION))
                filename = f"emp_{eid}.jpg"
                filepath = os.path.join(config.UPLOAD_FOLDER, filename)
                img = img.convert("RGB")
                img.save(filepath, "JPEG", quality=85)
                updates["photo"] = filename
            except Exception as e:
                flash(f"写真のアップロードに失敗しました: {e}", "error")

        sheets_manager.update_employee(eid, **updates)
        flash(f"従業員「{name}」を更新しました", "success")
        return redirect(url_for("admin_employees"))

    return render_template("admin_edit_employee.html", employee=emp, stores=config.STORE_NAMES)


@app.route("/admin/employees/<eid>/delete", methods=["POST"])
@admin_required
def admin_delete_employee(eid):
    if str(eid) == session.get("admin_id"):
        flash("自分自身は削除できません", "error")
        return redirect(url_for("admin_employees"))
    sheets_manager.delete_employee(eid)
    flash("従業員を削除しました", "success")
    return redirect(url_for("admin_employees"))


# --- 打刻履歴編集 ---

@app.route("/admin/records/<eid>")
@admin_required
def admin_records(eid):
    emp = sheets_manager.get_employee_by_id(eid)
    if not emp:
        flash("従業員が見つかりません", "error")
        return redirect(url_for("admin_employees"))
    records = sheets_manager.get_all_records_for_employee(eid)
    records.reverse()
    return render_template("admin_records.html", employee=emp, records=records, stores=config.STORE_NAMES)


@app.route("/admin/records/<eid>/edit/<int:row_num>", methods=["POST"])
@admin_required
def admin_edit_record(eid, row_num):
    error = sheets_manager.update_record(
        row_num,
        clock_in=request.form.get("clock_in", "").strip() or None,
        clock_out=request.form.get("clock_out", "").strip() or None,
        break_start_t=request.form.get("break_start", "").strip() or None,
        break_end_t=request.form.get("break_end", "").strip() or None,
    )
    if error:
        flash(error, "error")
    else:
        flash("打刻を修正しました", "success")
    return redirect(url_for("admin_records", eid=eid))


@app.route("/admin/records/<eid>/add", methods=["POST"])
@admin_required
def admin_add_record(eid):
    emp = sheets_manager.get_employee_by_id(eid)
    if not emp:
        flash("従業員が見つかりません", "error")
        return redirect(url_for("admin_employees"))
    sheets_manager.add_manual_record(
        eid, emp["名前"], emp.get("店舗ID", ""),
        request.form.get("date", ""),
        request.form.get("clock_in", ""),
        request.form.get("clock_out", ""),
        request.form.get("break_start", ""),
        request.form.get("break_end", ""),
    )
    flash("打刻を追加しました", "success")
    return redirect(url_for("admin_records", eid=eid))


@app.route("/admin/records/<eid>/delete/<int:row_num>", methods=["POST"])
@admin_required
def admin_delete_record(eid, row_num):
    sheets_manager.delete_record_by_row(row_num)
    flash("打刻を削除しました", "success")
    return redirect(url_for("admin_records", eid=eid))


# --- 要確認 ---

@app.route("/admin/alerts")
@admin_required
def admin_alerts():
    records = sheets_manager.get_flagged_records()
    return render_template("admin_alerts.html", records=records, stores=config.STORE_NAMES)


@app.route("/admin/alerts/resolve/<int:row_num>", methods=["POST"])
@admin_required
def admin_resolve_alert(row_num):
    sheets_manager.resolve_flag(row_num)
    flash("フラグを解除しました", "success")
    return redirect(url_for("admin_alerts"))


# --- 月次レポート ---

@app.route("/admin/report")
@admin_required
def admin_report():
    now = datetime.now()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    store_filter = request.args.get("store", "all")

    records = sheets_manager.get_monthly_records(year, month, store_id=store_filter if store_filter != "all" else None)

    total_work = sum(float(r["勤務時間"]) for r in records if r.get("勤務時間"))
    total_break = sum(float(r["休憩時間"]) for r in records if r.get("休憩時間"))
    work_days = len([r for r in records if r.get("出勤")])

    return render_template(
        "admin_report.html",
        records=records, year=year, month=month,
        total_work=f"{total_work:.2f}",
        total_break=f"{total_break:.2f}",
        work_days=work_days,
        store_filter=store_filter,
        stores=config.STORE_NAMES,
    )


# --- CSV出力 ---

@app.route("/admin/report/csv")
@admin_required
def admin_report_csv():
    now = datetime.now()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    store_filter = request.args.get("store", "all")

    records = sheets_manager.get_monthly_records(year, month, store_id=store_filter if store_filter != "all" else None)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["従業員名", "店舗", "日付", "出勤", "退勤", "休憩開始", "休憩終了", "勤務時間", "休憩時間"])
    for r in records:
        store_name = config.STORE_NAMES.get(r.get("店舗ID", ""), r.get("店舗ID", ""))
        writer.writerow([
            r.get("従業員名", ""), store_name, r.get("日付", ""),
            r.get("出勤", ""), r.get("退勤", ""),
            r.get("休憩開始", ""), r.get("休憩終了", ""),
            r.get("勤務時間", ""), r.get("休憩時間", ""),
        ])

    output.seek(0)
    filename = f"タイムカード_{year}年{month:02d}月.csv"
    encoded = quote(filename)
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv; charset=utf-8-sig",
        headers={
            "Content-Disposition": f"attachment; filename=\"download.csv\"; filename*=UTF-8''{encoded}",
        },
    )


# --- MF給与CSV ---

@app.route("/admin/report/mf-csv")
@admin_required
def admin_mf_csv():
    now = datetime.now()
    year = int(request.args.get("year", now.year))
    month = int(request.args.get("month", now.month))
    store_filter = request.args.get("store", "all")

    summary = sheets_manager.get_mf_summary(year, month, store_id=store_filter if store_filter != "all" else None)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "従業員コード", "従業員名", "出勤日数",
        "総労働時間", "普通残業時間", "深夜労働時間",
        "休日労働時間", "休憩時間合計",
    ])
    for s in summary:
        writer.writerow([
            s["code"], s["name"], s["days"],
            f"{s['total_work']:.2f}",
            f"{s['overtime']:.2f}",
            f"{s['night_work']:.2f}",
            "0.00",
            f"{s['break_total']:.2f}",
        ])

    output.seek(0)
    filename = f"MF給与_{year}年{month:02d}月.csv"
    encoded = quote(filename)
    return Response(
        output.getvalue().encode("utf-8-sig"),
        mimetype="text/csv; charset=utf-8-sig",
        headers={
            "Content-Disposition": f"attachment; filename=\"download.csv\"; filename*=UTF-8''{encoded}",
        },
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LINE Webhook + テスト通知
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route("/webhook/line", methods=["POST"])
def line_webhook():
    """LINE Webhook：User ID取得用"""
    try:
        body = request.get_json(silent=True) or {}
        events = body.get("events", [])
        for ev in events:
            uid = ev.get("source", {}).get("userId")
            if not uid:
                continue

            # 重複チェック
            already = any(r["user_id"] == uid for r in _webhook_received_ids)
            if already:
                continue

            # プロフィール取得（display_name）
            display_name = ""
            try:
                import requests as _req
                headers = {"Authorization": f"Bearer {config.LINE_CHANNEL_ACCESS_TOKEN}"}
                resp = _req.get(
                    f"https://api.line.me/v2/bot/profile/{uid}",
                    headers=headers, timeout=5,
                )
                if resp.status_code == 200:
                    display_name = resp.json().get("displayName", "")
            except Exception:
                pass

            _webhook_received_ids.append({
                "user_id": uid,
                "display_name": display_name,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            print(f"[LINE Webhook] User ID取得: {uid} ({display_name})")

        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"[LINE Webhook] エラー: {e}")
        return jsonify({"status": "error"}), 500


# --- LINE設定ページ ---

@app.route("/admin/line-setup")
@admin_required
def admin_line_setup():
    """LINE User ID取得支援ページ"""
    return render_template(
        "admin_line_setup.html",
        received_ids=_webhook_received_ids,
        current_ids=config.LINE_ADMIN_USER_IDS,
        token_set=bool(config.LINE_CHANNEL_ACCESS_TOKEN),
        messaging_enabled=config.LINE_MESSAGING_ENABLED,
    )


@app.route("/api/admin/line-ids")
@admin_required
def api_line_ids():
    """Webhook受信済みUser IDをポーリングで取得"""
    return jsonify(_webhook_received_ids)


@app.route("/admin/line-ids/clear", methods=["POST"])
@admin_required
def admin_clear_line_ids():
    """受信済みUser IDリストをクリア"""
    _webhook_received_ids.clear()
    flash("受信済みUser IDリストをクリアしました", "success")
    return redirect(url_for("admin_line_setup"))


@app.route("/admin/line-test", methods=["POST"])
@admin_required
def admin_line_test():
    """テスト通知送信"""
    line_notifier.send_line_notification(
        "forgot_clock_out",
        "🔔 テスト通知\nLINE Messaging APIの接続テストです。\nこのメッセージが届いていれば正常に動作しています。",
    )
    flash("テスト通知を送信しました（LINE設定を確認してください）", "success")
    return redirect(url_for("admin_line_setup"))


# ━━━━━ 起動 ━━━━━

if __name__ == "__main__":
    from scheduler import init_scheduler
    init_scheduler()

    print("=" * 55)
    print("  Sachinova株式会社 タイムカードシステム")
    print("  従業員用: http://localhost:5000")
    print("  管理者用: http://localhost:5000/admin")
    print("=" * 55)
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
