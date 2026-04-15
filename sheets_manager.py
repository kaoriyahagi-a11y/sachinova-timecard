"""Google Sheets データ管理 + メモリキャッシュ + マルチシフト対応"""
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import time as _time
import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# シフト間最小インターバル（分）
SHIFT_MIN_INTERVAL = 15

# ━━━━━ キャッシュ ━━━━━
_employee_cache = {"data": None, "ts": 0}
_today_records_cache = {"data": None, "ts": 0, "date": ""}
TODAY_CACHE_TTL = 30


def _emp_cache_valid():
    return (
        _employee_cache["data"] is not None
        and (_time.time() - _employee_cache["ts"]) < config.EMPLOYEE_CACHE_TTL
    )


def _today_cache_valid():
    today = datetime.now().strftime("%Y-%m-%d")
    return (
        _today_records_cache["data"] is not None
        and _today_records_cache["date"] == today
        and (_time.time() - _today_records_cache["ts"]) < TODAY_CACHE_TTL
    )


def clear_employee_cache():
    _employee_cache["data"] = None
    _employee_cache["ts"] = 0


def clear_today_cache():
    _today_records_cache["data"] = None
    _today_records_cache["ts"] = 0
    _today_records_cache["date"] = ""


# ━━━━━ Google Sheets 接続 ━━━━━
_spreadsheet_cache = None
_client_cache = {"client": None, "ts": 0}
CLIENT_TTL = 1800


def get_client():
    if _client_cache["client"] and (_time.time() - _client_cache["ts"]) < CLIENT_TTL:
        return _client_cache["client"]
    import os, json as _json
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if creds_json:
        # Render等: 環境変数にJSON文字列を格納
        info = _json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        # ローカル: credentials.jsonファイルを使用
        creds = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
        )
    client = gspread.authorize(creds)
    _client_cache["client"] = client
    _client_cache["ts"] = _time.time()
    return client


def get_spreadsheet():
    global _spreadsheet_cache
    if _spreadsheet_cache is not None:
        return _spreadsheet_cache
    client = get_client()
    try:
        ss = client.open(config.SPREADSHEET_NAME)
    except gspread.SpreadsheetNotFound:
        ss = client.create(config.SPREADSHEET_NAME)
    _ensure_sheets(ss)
    _spreadsheet_cache = ss
    return ss


def reset_spreadsheet_cache():
    global _spreadsheet_cache
    _spreadsheet_cache = None
    _client_cache["client"] = None
    _client_cache["ts"] = 0


def _ensure_sheets(ss):
    names = [ws.title for ws in ss.worksheets()]

    if "従業員" not in names:
        ws = ss.add_worksheet(title="従業員", rows=200, cols=10)
        ws.append_row([
            "ID", "名前", "メール", "パスワード", "役割",
            "店舗ID", "PIN", "写真", "有効", "作成日",
        ])
        ws.append_row([
            "1",
            config.DEFAULT_ADMIN_NAME,
            config.DEFAULT_ADMIN_EMAIL,
            generate_password_hash(config.DEFAULT_ADMIN_PASSWORD),
            "admin",
            "kiyosumi",
            generate_password_hash("0000"),
            "",
            "1",
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        ])

    if "打刻記録" not in names:
        ws = ss.add_worksheet(title="打刻記録", rows=5000, cols=12)
        ws.append_row([
            "ID", "従業員ID", "従業員名", "店舗ID", "日付",
            "出勤", "退勤", "休憩開始", "休憩終了",
            "勤務時間", "休憩時間", "要確認",
        ])

    if "Sheet1" in names:
        try:
            ss.del_worksheet(ss.worksheet("Sheet1"))
        except Exception:
            pass


# ━━━━━ 従業員 CRUD ━━━━━

def get_all_employees():
    if _emp_cache_valid():
        return _employee_cache["data"]
    ss = get_spreadsheet()
    ws = ss.worksheet("従業員")
    records = ws.get_all_records()
    _employee_cache["data"] = records
    _employee_cache["ts"] = _time.time()
    return records


def get_employee_by_id(eid):
    for emp in get_all_employees():
        if str(emp["ID"]) == str(eid):
            return emp
    return None


def get_employee_by_email(email):
    for emp in get_all_employees():
        if emp["メール"] == email:
            return emp
    return None


def get_employees_by_store(store_id):
    return [
        e for e in get_all_employees()
        if str(e.get("有効", "1")) == "1"
        and e.get("役割") != "admin"
        and (e.get("店舗ID", "") == store_id or e.get("店舗ID", "") == "both")
    ]


def authenticate_admin(email, password):
    emp = get_employee_by_email(email)
    if emp and emp["役割"] == "admin" and check_password_hash(emp["パスワード"], password):
        return emp
    return None


def authenticate_pin(eid, pin):
    emp = get_employee_by_id(eid)
    if not emp:
        return None
    stored = str(emp.get("PIN", ""))
    if stored.startswith("pbkdf2:") or stored.startswith("scrypt:"):
        if check_password_hash(stored, pin):
            return emp
    else:
        if stored == pin:
            return emp
    return None


def add_employee(name, email, password, role="employee", store_id="kiyosumi", pin="0000"):
    ss = get_spreadsheet()
    ws = ss.worksheet("従業員")
    all_vals = ws.get_all_values()
    max_id = 0
    for row in all_vals[1:]:
        try:
            max_id = max(max_id, int(row[0]))
        except (ValueError, IndexError):
            pass
    new_id = str(max_id + 1)
    ws.append_row([
        new_id, name, email,
        generate_password_hash(password),
        role, store_id,
        generate_password_hash(pin),
        "", "1",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    ])
    clear_employee_cache()
    return new_id


def update_employee(eid, *, name=None, store_id=None, pin=None, role=None, photo=None, active=None):
    ss = get_spreadsheet()
    ws = ss.worksheet("従業員")
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row[0] == str(eid):
            updates = []
            if name is not None:
                updates.append({"row": i + 1, "col": 2, "val": name})
            if role is not None:
                updates.append({"row": i + 1, "col": 5, "val": role})
            if store_id is not None:
                updates.append({"row": i + 1, "col": 6, "val": store_id})
            if pin is not None:
                updates.append({"row": i + 1, "col": 7, "val": generate_password_hash(pin)})
            if photo is not None:
                updates.append({"row": i + 1, "col": 8, "val": photo})
            if active is not None:
                updates.append({"row": i + 1, "col": 9, "val": str(active)})
            if updates:
                cells = [gspread.Cell(u["row"], u["col"], u["val"]) for u in updates]
                ws.update_cells(cells)
            clear_employee_cache()
            return True
    return False


def delete_employee(eid):
    ss = get_spreadsheet()
    ws = ss.worksheet("従業員")
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if row[0] == str(eid):
            ws.delete_rows(i + 1)
            clear_employee_cache()
            return True
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  打刻 — マルチシフト対応ステートマシン
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_today_records():
    """今日の全打刻レコードを取得（キャッシュ付き）"""
    if _today_cache_valid():
        return _today_records_cache["data"]
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    all_vals = ws.get_all_values()
    today = datetime.now().strftime("%Y-%m-%d")
    headers = all_vals[0] if all_vals else []
    results = []
    for row in all_vals[1:]:
        if len(row) > 4 and row[4] == today:
            rec = {}
            for j, h in enumerate(headers):
                rec[h] = row[j] if j < len(row) else ""
            results.append(rec)
    _today_records_cache["data"] = results
    _today_records_cache["ts"] = _time.time()
    _today_records_cache["date"] = today
    return results


def get_today_shifts_for(eid):
    """特定従業員の今日の全シフト（リスト、時系列順）"""
    shifts = []
    for rec in get_today_records():
        if str(rec.get("従業員ID", "")) == str(eid):
            shifts.append(rec)
    return shifts


def get_current_shift(eid):
    """最新（アクティブ）シフトを返す。なければNone"""
    shifts = get_today_shifts_for(eid)
    if not shifts:
        return None
    return shifts[-1]


def _shift_status(shift):
    """1シフトの状態: none/working/break/done"""
    if not shift or not shift.get("出勤"):
        return "none"
    if shift.get("退勤"):
        return "done"
    if shift.get("休憩開始") and not shift.get("休憩終了"):
        return "break"
    return "working"


def get_employee_status(eid):
    """
    従業員の現在ステータスを返す。
    マルチシフト対応: 最新シフトの状態を返す。
    全シフトが done の場合 → "shift_done"（再出勤可能）
    シフトなし → "none"
    """
    shifts = get_today_shifts_for(eid)
    if not shifts:
        return "none"
    last = shifts[-1]
    st = _shift_status(last)
    if st == "done":
        return "shift_done"
    return st


def get_allowed_actions(eid):
    """
    現在の状態から許可されるアクションリストとステータスラベルを返す。
    Returns: (allowed: list[str], status_label: str, shift_num: int)
    """
    shifts = get_today_shifts_for(eid)
    if not shifts:
        return ["clock_in"], "未出勤", 0

    last = shifts[-1]
    st = _shift_status(last)
    shift_num = len(shifts)

    if st == "none":
        return ["clock_in"], "未出勤", 0
    elif st == "working":
        return ["clock_out", "break_start"], f"第{shift_num}シフト 勤務中", shift_num
    elif st == "break":
        return ["break_end"], f"第{shift_num}シフト 休憩中", shift_num
    elif st == "done":
        # 退勤済み → 再出勤可能（インターバルチェックはclock_inで実施）
        return ["clock_in"], f"第{shift_num}シフト 退勤済み", shift_num

    return [], "不明", shift_num


def _find_active_shift_row(eid):
    """今日のアクティブ（未退勤）シフトの行番号を返す"""
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    all_vals = ws.get_all_values()
    today = datetime.now().strftime("%Y-%m-%d")
    # 逆順走査で最新のものを見つける
    for i in range(len(all_vals) - 1, 0, -1):
        row = all_vals[i]
        if len(row) > 6 and row[1] == str(eid) and row[4] == today and row[5] and not row[6]:
            return i + 1, row, ws
    return None, None, ws


def _find_last_completed_shift(eid):
    """今日の最後に完了したシフトを返す"""
    shifts = get_today_shifts_for(eid)
    for s in reversed(shifts):
        if _shift_status(s) == "done":
            return s
    return None


def clock_in(eid, emp_name, store_id):
    """出勤打刻 — ステートマシンバリデーション付き"""
    shifts = get_today_shifts_for(eid)

    if shifts:
        last = shifts[-1]
        st = _shift_status(last)
        if st == "working":
            return None, "現在勤務中です。先に退勤してください"
        if st == "break":
            return None, "休憩中です。先に休憩終了してください"
        if st == "done":
            # インターバルチェック
            try:
                last_out = datetime.strptime(last["退勤"], "%H:%M")
                now = datetime.now()
                now_hm = now.replace(second=0, microsecond=0)
                last_out_dt = now_hm.replace(hour=last_out.hour, minute=last_out.minute)
                diff_min = (now_hm - last_out_dt).total_seconds() / 60
                if diff_min < SHIFT_MIN_INTERVAL:
                    remaining = int(SHIFT_MIN_INTERVAL - diff_min)
                    return None, f"前のシフトから{SHIFT_MIN_INTERVAL}分経っていません（あと{remaining}分）"
            except Exception:
                pass

    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    now = datetime.now()
    ws.append_row([
        now.strftime("%Y%m%d%H%M%S"),
        str(eid), emp_name, store_id,
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M"),
        "", "", "", "", "", "",
    ])
    clear_today_cache()
    return now.strftime("%H:%M"), None


def clock_out(eid):
    """退勤打刻 — 休憩中は拒否、出勤より前の時刻は拒否"""
    row_num, row, ws = _find_active_shift_row(eid)
    if not row_num:
        return None, "出勤中のシフトがありません", None
    if not row[5]:
        return None, "先に出勤打刻をしてください", None
    if row[7] and not row[8]:
        return None, "退勤する前に休憩終了を押してください", None
    if row[6]:
        return None, "既に退勤打刻済みです", None

    now = datetime.now()
    time_str = now.strftime("%H:%M")

    clock_in_t = datetime.strptime(row[5], "%H:%M")
    clock_out_t = datetime.strptime(time_str, "%H:%M")

    # 退勤が出勤より前になるケースを防止
    if clock_out_t <= clock_in_t:
        return None, f"退勤時刻（{time_str}）が出勤時刻（{row[5]}）より前です", None

    delta = clock_out_t - clock_in_t

    break_hours = 0.0
    if row[7] and row[8]:
        bs = datetime.strptime(row[7], "%H:%M")
        be = datetime.strptime(row[8], "%H:%M")
        if be > bs:
            break_hours = (be - bs).total_seconds() / 3600

    work_hours = max(0, delta.total_seconds() / 3600 - break_hours)

    cells = [
        gspread.Cell(row_num, 7, time_str),
        gspread.Cell(row_num, 10, f"{work_hours:.2f}"),
        gspread.Cell(row_num, 11, f"{break_hours:.2f}"),
    ]
    ws.update_cells(cells)
    clear_today_cache()
    return time_str, None, f"{work_hours:.1f}時間"


def break_start(eid):
    """休憩開始 — 勤務中のみ可、出勤より前の時刻は拒否"""
    row_num, row, ws = _find_active_shift_row(eid)
    if not row_num:
        return None, "出勤中のシフトがありません"
    if not row[5]:
        return None, "先に出勤打刻をしてください"
    if row[6]:
        return None, "既に退勤済みです。休憩開始はできません"
    if row[7]:
        return None, "既に休憩開始済みです"

    now = datetime.now()
    time_str = now.strftime("%H:%M")
    clock_in_t = datetime.strptime(row[5], "%H:%M")
    if datetime.strptime(time_str, "%H:%M") <= clock_in_t:
        return None, f"休憩開始（{time_str}）が出勤時刻（{row[5]}）より前です"

    ws.update_cell(row_num, 8, time_str)
    clear_today_cache()
    return time_str, None


def break_end(eid):
    """休憩終了 — 休憩中のみ可、休憩開始より前の時刻は拒否"""
    row_num, row, ws = _find_active_shift_row(eid)
    if not row_num:
        return None, "出勤中のシフトがありません"
    if not row[7]:
        return None, "先に休憩開始をしてください"
    if row[8]:
        return None, "既に休憩終了済みです"

    now = datetime.now()
    time_str = now.strftime("%H:%M")
    break_start_t = datetime.strptime(row[7], "%H:%M")
    if datetime.strptime(time_str, "%H:%M") <= break_start_t:
        return None, f"休憩終了（{time_str}）が休憩開始（{row[7]}）より前です"

    ws.update_cell(row_num, 9, time_str)
    clear_today_cache()
    return time_str, None


# ━━━━━ 要確認フラグ ━━━━━

def flag_forgot_clockout():
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    all_vals = ws.get_all_values()
    today = datetime.now().strftime("%Y-%m-%d")
    flagged = []

    for i, row in enumerate(all_vals):
        if i == 0:
            continue
        if len(row) > 6 and row[4] == today and row[5] and not row[6]:
            ws.update_cell(i + 1, 12, "要確認")
            store_name = config.STORE_NAMES.get(row[3], row[3])
            try:
                cin = datetime.strptime(row[5], "%H:%M")
                now = datetime.now()
                elapsed = now - now.replace(hour=cin.hour, minute=cin.minute, second=0, microsecond=0)
                hours_str = str(int(elapsed.total_seconds() // 3600))
            except Exception:
                hours_str = "不明"
            flagged.append({
                "name": row[2],
                "store": store_name,
                "clock_in": row[5],
                "hours": hours_str,
            })

    clear_today_cache()
    return flagged


def get_flagged_records():
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    all_vals = ws.get_all_values()
    headers = all_vals[0] if all_vals else []
    results = []
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) > 11 and row[11] == "要確認":
            rec = {"_row": i}
            for j, h in enumerate(headers):
                rec[h] = row[j] if j < len(row) else ""
            results.append(rec)
    return results


def resolve_flag(row_num):
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    ws.update_cell(row_num, 12, "")
    clear_today_cache()


def update_record(row_num, clock_in=None, clock_out=None, break_start_t=None, break_end_t=None):
    """管理者による打刻修正 — 時刻の前後関係をバリデーション"""
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")

    row = ws.row_values(row_num)
    cin = clock_in or (row[5] if len(row) > 5 else "")
    cout = clock_out or (row[6] if len(row) > 6 else "")
    bs = break_start_t or (row[7] if len(row) > 7 else "")
    be = break_end_t or (row[8] if len(row) > 8 else "")

    # 時刻バリデーション
    try:
        if cin and cout:
            t_in = datetime.strptime(cin, "%H:%M")
            t_out = datetime.strptime(cout, "%H:%M")
            if t_out <= t_in:
                return "退勤時刻は出勤時刻より後にしてください"
        if cin and bs:
            if datetime.strptime(bs, "%H:%M") <= datetime.strptime(cin, "%H:%M"):
                return "休憩開始は出勤時刻より後にしてください"
        if bs and be:
            if datetime.strptime(be, "%H:%M") <= datetime.strptime(bs, "%H:%M"):
                return "休憩終了は休憩開始より後にしてください"
        if be and cout:
            if datetime.strptime(cout, "%H:%M") < datetime.strptime(be, "%H:%M"):
                return "退勤時刻は休憩終了より後にしてください"
    except ValueError:
        return "時刻の形式が正しくありません（HH:MM）"

    cells = []
    if clock_in is not None:
        cells.append(gspread.Cell(row_num, 6, clock_in))
    if clock_out is not None:
        cells.append(gspread.Cell(row_num, 7, clock_out))
    if break_start_t is not None:
        cells.append(gspread.Cell(row_num, 8, break_start_t))
    if break_end_t is not None:
        cells.append(gspread.Cell(row_num, 9, break_end_t))

    if cin and cout:
        try:
            t_in = datetime.strptime(cin, "%H:%M")
            t_out = datetime.strptime(cout, "%H:%M")
            work = (t_out - t_in).total_seconds() / 3600
            brk = 0.0
            if bs and be:
                t_bs = datetime.strptime(bs, "%H:%M")
                t_be = datetime.strptime(be, "%H:%M")
                brk = (t_be - t_bs).total_seconds() / 3600
            cells.append(gspread.Cell(row_num, 10, f"{max(0, work - brk):.2f}"))
            cells.append(gspread.Cell(row_num, 11, f"{max(0, brk):.2f}"))
        except Exception:
            pass

    if cells:
        ws.update_cells(cells)
    clear_today_cache()
    return None  # エラーなし


def add_manual_record(eid, emp_name, store_id, date, clock_in, clock_out, break_s, break_e):
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")

    work_h = ""
    break_h = ""
    if clock_in and clock_out:
        try:
            t_in = datetime.strptime(clock_in, "%H:%M")
            t_out = datetime.strptime(clock_out, "%H:%M")
            brk = 0.0
            if break_s and break_e:
                t_bs = datetime.strptime(break_s, "%H:%M")
                t_be = datetime.strptime(break_e, "%H:%M")
                brk = (t_be - t_bs).total_seconds() / 3600
            work = (t_out - t_in).total_seconds() / 3600 - brk
            work_h = f"{work:.2f}"
            break_h = f"{brk:.2f}"
        except Exception:
            pass

    ws.append_row([
        datetime.now().strftime("%Y%m%d%H%M%S"),
        str(eid), emp_name, store_id, date,
        clock_in, clock_out, break_s, break_e,
        work_h, break_h, "",
    ])
    clear_today_cache()


def delete_record_by_row(row_num):
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    ws.delete_rows(row_num)
    clear_today_cache()


# ━━━━━ レポート ━━━━━

def get_monthly_records(year, month, store_id=None, eid=None):
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    all_vals = ws.get_all_values()
    headers = all_vals[0] if all_vals else []
    prefix = f"{year}-{month:02d}"
    results = []
    for row in all_vals[1:]:
        if len(row) > 4 and row[4].startswith(prefix):
            if store_id and store_id != "all" and row[3] != store_id:
                continue
            if eid and row[1] != str(eid):
                continue
            rec = {}
            for j, h in enumerate(headers):
                rec[h] = row[j] if j < len(row) else ""
            results.append(rec)
    return results


def get_all_records_for_employee(eid):
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    all_vals = ws.get_all_values()
    headers = all_vals[0] if all_vals else []
    results = []
    for i, row in enumerate(all_vals[1:], start=2):
        if len(row) > 1 and row[1] == str(eid):
            rec = {"_row": i}
            for j, h in enumerate(headers):
                rec[h] = row[j] if j < len(row) else ""
            results.append(rec)
    return results


# ━━━━━ ダッシュボード集計（マルチシフト対応） ━━━━━

def get_store_dashboard():
    today_recs = get_today_records()
    employees = get_all_employees()

    active_emps = [
        e for e in employees
        if str(e.get("有効", "1")) == "1" and e.get("役割") != "admin"
    ]

    dashboard = {}
    for sid, sname in config.STORE_NAMES.items():
        store_emps = [
            e for e in active_emps
            if e.get("店舗ID") == sid or e.get("店舗ID") == "both"
        ]
        working = []
        on_break = []
        done = []
        not_in = []

        for emp in store_emps:
            eid = str(emp["ID"])
            # 全シフト取得
            emp_shifts = [r for r in today_recs if str(r.get("従業員ID", "")) == eid]
            info = {"name": emp["名前"], "id": eid}

            if not emp_shifts:
                not_in.append(info)
                continue

            last = emp_shifts[-1]
            st = _shift_status(last)
            shift_num = len(emp_shifts)

            if st == "working":
                info["clock_in"] = last["出勤"]
                info["shift_num"] = shift_num
                try:
                    cin = datetime.strptime(last["出勤"], "%H:%M")
                    now = datetime.now()
                    delta = now - now.replace(hour=cin.hour, minute=cin.minute, second=0, microsecond=0)
                    h = int(delta.total_seconds() // 3600)
                    m = int((delta.total_seconds() % 3600) // 60)
                    info["elapsed"] = f"{h}時間{m}分"
                except Exception:
                    info["elapsed"] = ""
                working.append(info)
            elif st == "break":
                info["clock_in"] = last["出勤"]
                info["shift_num"] = shift_num
                on_break.append(info)
            elif st == "done":
                # 全シフトの合計勤務時間
                total_h = 0
                for s in emp_shifts:
                    try:
                        total_h += float(s.get("勤務時間", 0))
                    except (ValueError, TypeError):
                        pass
                info["clock_in"] = emp_shifts[0]["出勤"]
                info["clock_out"] = last["退勤"]
                info["hours"] = f"{total_h:.1f}"
                info["shift_count"] = shift_num
                done.append(info)

        dashboard[sid] = {
            "name": sname,
            "working": working,
            "on_break": on_break,
            "done": done,
            "not_in": not_in,
            "total": len(store_emps),
        }
    return dashboard


# ━━━━━ MF給与CSV用集計（マルチシフト対応） ━━━━━

def _calc_night_hours(cin_str, cout_str):
    """22:00〜翌5:00の深夜時間を分単位で計算"""
    try:
        t_in = datetime.strptime(cin_str, "%H:%M")
        t_out = datetime.strptime(cout_str, "%H:%M")

        night_minutes = 0
        # 22時以降の部分
        if t_out.hour >= 22 or t_out.hour < 5:
            night_start = t_in.replace(hour=22, minute=0) if t_in.hour < 22 else t_in
            if t_out.hour >= 22:
                night_minutes = (t_out - night_start).total_seconds() / 60
            elif t_out.hour < 5:
                # 日をまたぐケース: 22:00〜24:00 + 0:00〜退勤
                if t_in.hour < 22:
                    night_minutes = (24 - 22) * 60 + t_out.hour * 60 + t_out.minute
                else:
                    night_minutes = (t_out - t_in).total_seconds() / 60
                    if night_minutes < 0:
                        night_minutes += 24 * 60
        elif t_in.hour < 5:
            end_hour = min(t_out.hour, 5) if t_out.hour <= 5 else 5
            end = t_in.replace(hour=end_hour, minute=0 if t_out.hour >= 5 else t_out.minute)
            night_minutes = (end - t_in).total_seconds() / 60

        return max(0, night_minutes / 60)
    except Exception:
        return 0


def get_mf_summary(year, month, store_id=None):
    records = get_monthly_records(year, month, store_id=store_id)
    employees = get_all_employees()

    emp_map = {str(e["ID"]): e for e in employees}

    # 日単位の出勤日数を正確にカウントするための集合
    summary = {}
    day_tracker = {}  # {eid: set(dates)}

    for rec in records:
        eid = str(rec.get("従業員ID", ""))
        if eid not in summary:
            emp = emp_map.get(eid, {})
            summary[eid] = {
                "code": eid,
                "name": rec.get("従業員名", ""),
                "store": config.STORE_NAMES.get(emp.get("店舗ID", ""), ""),
                "days": 0,
                "total_work": 0.0,
                "overtime_daily": {},  # {date: total_hours} for overtime calc
                "night_work": 0.0,
                "break_total": 0.0,
            }
            day_tracker[eid] = set()

        s = summary[eid]
        date = rec.get("日付", "")
        if rec.get("出勤") and date:
            day_tracker[eid].add(date)

        try:
            wh = float(rec.get("勤務時間", 0))
            s["total_work"] += wh
            # 日別の合計を蓄積（残業は日単位で8h超過分）
            s["overtime_daily"][date] = s["overtime_daily"].get(date, 0) + wh
        except (ValueError, TypeError):
            pass
        try:
            bh = float(rec.get("休憩時間", 0))
            s["break_total"] += bh
        except (ValueError, TypeError):
            pass

        # 深夜時間（シフトごと）
        cin = rec.get("出勤", "")
        cout = rec.get("退勤", "")
        if cin and cout:
            s["night_work"] += _calc_night_hours(cin, cout)

    # 最終集計
    result = []
    for eid, s in summary.items():
        s["days"] = len(day_tracker.get(eid, set()))
        # 日単位の残業合算
        overtime = sum(max(0, h - 8.0) for h in s["overtime_daily"].values())
        result.append({
            "code": s["code"],
            "name": s["name"],
            "store": s["store"],
            "days": s["days"],
            "total_work": s["total_work"],
            "overtime": overtime,
            "night_work": s["night_work"],
            "break_total": s["break_total"],
        })

    return result


# ━━━━━ PIN失敗追跡（メモリ） ━━━━━
_pin_failures = {}


def record_pin_failure(eid):
    now = _time.time()
    entry = _pin_failures.get(eid, {"count": 0, "locked_until": 0})
    entry["count"] += 1
    _pin_failures[eid] = entry
    if entry["count"] >= config.PIN_MAX_FAILURES:
        entry["locked_until"] = now + config.PIN_LOCK_MINUTES * 60
        return True
    return False


def is_pin_locked(eid):
    entry = _pin_failures.get(eid)
    if not entry:
        return False
    if entry["locked_until"] > _time.time():
        return True
    if entry["count"] >= config.PIN_MAX_FAILURES:
        _pin_failures[eid] = {"count": 0, "locked_until": 0}
    return False


def clear_pin_failures(eid):
    _pin_failures.pop(eid, None)


def get_pin_failure_count(eid):
    return _pin_failures.get(eid, {}).get("count", 0)


# ━━━━━ 初打刻フラグ ━━━━━

def is_first_punch(eid):
    ss = get_spreadsheet()
    ws = ss.worksheet("打刻記録")
    all_vals = ws.get_all_values()
    for row in all_vals[1:]:
        if len(row) > 1 and row[1] == str(eid):
            return False
    return True


# ━━━━━ バックアップ ━━━━━

def create_backup():
    client = get_client()
    ss = get_spreadsheet()
    today = datetime.now().strftime("%Y%m%d")
    backup_name = f"{config.SPREADSHEET_NAME}_backup_{today}"
    try:
        client.list_spreadsheet_files()
    except Exception:
        pass
    client.copy(ss.id, title=backup_name)
    return backup_name


def cleanup_old_backups():
    client = get_client()
    try:
        all_files = client.list_spreadsheet_files()
        prefix = f"{config.SPREADSHEET_NAME}_backup_"
        cutoff = datetime.now()
        for f in all_files:
            if f["name"].startswith(prefix):
                try:
                    date_str = f["name"].replace(prefix, "")
                    file_date = datetime.strptime(date_str, "%Y%m%d")
                    if (cutoff - file_date).days > config.BACKUP_RETENTION_DAYS:
                        client.del_spreadsheet(f["id"])
                except Exception:
                    pass
    except Exception:
        pass
