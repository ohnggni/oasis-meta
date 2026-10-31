import datetime, hashlib, html, os, re, threading, time
from functools import wraps
import pymysql
from flask import Flask, Response, g, jsonify, request
from werkzeug.security import check_password_hash

DB = dict(host=os.environ.get("DB_HOST", "bookoasis_mariadb"), port=int(os.environ.get("DB_PORT", "3306")),
          user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], charset="utf8mb4",
          cursorclass=pymysql.cursors.DictCursor, connect_timeout=5, read_timeout=30)
PREFIX = os.environ.get("DB_PREFIX", "media_")
app = Flask(__name__)
app.json.ensure_ascii = False

def q(db, sql, params=()):
    c = pymysql.connect(database=PREFIX + db, **DB)
    try:
        with c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        c.close()

_auth, _lock, AUTH_TTL = {}, threading.Lock(), 300
def check_user(username, password):
    key = hashlib.sha256(f"{username}:{password}".encode()).hexdigest()
    hit = _auth.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    rows = q("general", "SELECT username, password_hash, role FROM users WHERE username=%s", (username,))
    if not rows or not check_password_hash(rows[0]["password_hash"], password):
        return None
    with _lock:
        _auth[key] = (time.time() + AUTH_TTL, rows[0]["role"])
    return rows[0]["role"]

def auth(f):
    @wraps(f)
    def wrapper(*a, **kw):
        au = request.authorization
        role = check_user(au.username, au.password) if au and au.username and au.password else None
        if not role:
            return Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="oasis-meta"'})
        db = request.args.get("db", "general")
        if db not in ("general", "adult"):
            return jsonify(error="db must be general or adult"), 400
        if db == "adult" and role != "admin":
            return jsonify(error="forbidden"), 403
        g.db = db
        return f(*a, **kw)
    return wrapper

@app.errorhandler(ValueError)
def bad_value(e):
    return jsonify(error=f"bad parameter: {e}"), 400

def iso(ts):
    try:
        return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None
    except (TypeError, ValueError, OSError):
        return None

def cover_url(v):
    # 북오아시스 웹(static/js/cover_fallback.js getBookCoverSrc)과 같은 규칙:
    # DB books.cover_image = "{library_id}/book_<md5>.webp" -> "/covers/{library_id}/book_<md5>.webp"
    s = str(v or "").strip()
    if not s:
        return None
    if s.startswith(("http://", "https://", "/api/")):
        return s
    s = re.sub(r"^[/\\]+", "", s)
    if s.lower().startswith("covers/"):
        s = re.sub(r"^[/\\]+", "", s[7:])
    name = re.split(r"[/\\]", s)[-1]
    if not s or s.endswith(("/", "\\")) or "." not in name:
        return None
    return "/covers/" + s

# ── 줄거리(summary) — /books/{id} 전용 ──
# 출처: books.summary (권 단위 컬럼이지만 대부분 폴더 사이드카에서 채워져 폴더 내 권들이 같은 값).
# 그 권의 값만 쓰고, 비어 있으면 같은 시리즈 값으로 채우지 않는다(null).
#
# summary_trust 판정 규칙 (2026-10-29 표본 조사 기준):
#  R2 (정보 없음) 아래 NO_INFO 문구와 정확히 같거나 정리 후 빈 문자열이면 summary=null, summary_trust=null.
#  R1 (suspect) books.link에 'search.shopping.naver.com'이 들어 있으면 'suspect'.
#     북오아시스 kavita_yaml 파서는 meta/Summary가 없으면 search: 후보 리스트의 [0]번 항목(네이버 책검색 결과)의
#     description/link를 그대로 쓰는데, 이 경로로 들어온 값만 link가 네이버 쇼핑 책 카탈로그 주소다.
#     표본: 이 그룹 일반 30개 중 17개 오염(약 60%), 성인 12개 중 11개 오염(약 92%).
#  그 외 (ridibooks, kakao, naver series/comic, novelpia, yes24, link 없음 등) → 'ok'.
#     표본: 무작위 일반 30 + 성인 30 = 60개 중 오염 0건.
#  중복 summary(여러 시리즈가 같은 값)는 판정에 쓰지 않음: 표본상 대부분 정상 세트/전집 공통 소개였음.
NO_INFO = {"-", "책 정보가 없습니다", "책 정보가 없습니다.", "본 도서는 책소개가 없습니다", "본 도서는 책소개가 없습니다.",
           "등록된 설명이 없습니다", "등록된 설명이 없습니다.", "상세 설명 없음"}
SUSPECT_LINK_HOSTS = ("search.shopping.naver.com",)
_HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")  # 영문 HTML 태그만 (<책소개> 같은 한글 꺾쇠는 보존)

def clean_summary(v):
    t = str(v or "")
    if not t.strip():
        return None
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\\n", "\n")  # 글자 그대로 저장된 "\n"도 줄바꿈으로
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</?(?:p|div)(?:\s[^<>]*)?>", "\n", t, flags=re.I)
    t = html.unescape(_HTML_TAG.sub("", t))
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return None if (not t or t in NO_INFO) else t

def summary_trust(summary, link):
    if summary is None:
        return None
    link = str(link or "")
    return "suspect" if any(h in link for h in SUSPECT_LINK_HOSTS) else "ok"

def paging(default=100, maximum=500):
    page = max(int(request.args.get("page", 1)), 1)
    limit = min(max(int(request.args.get("limit", default)), 1), maximum)
    return page, limit, (page - 1) * limit

def like(t):
    return "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

def common_where(series_search=False):
    where, params = ["COALESCE(is_deleted, 0) = 0"], []
    if request.args.get("library_id"):
        where.append("library_id = %s"); params.append(int(request.args["library_id"]))
    fmts = [f.strip().lower() for f in request.args.get("format", "").split(",") if f.strip()]
    if fmts:
        where.append("LOWER(file_format) IN (" + ",".join(["%s"] * len(fmts)) + ")"); params += fmts
    for t in request.args.get("q", "").split()[:10]:
        s = like(t)
        if series_search:
            where.append("COALESCE(series_name, '') LIKE %s"); params.append(s)
        else:
            where.append("(COALESCE(title,'') LIKE %s OR COALESCE(series_name,'') LIKE %s OR COALESCE(author,'') LIKE %s)"); params += [s, s, s]
    return where, params

BOOK_COLS = "id, title, series_name, author, library_id, file_path, file_format, file_size, file_mtime, created_at, total_pages, cover_image"
BOOK_SORTS = {"mtime_desc": "file_mtime DESC, id DESC", "mtime_asc": "file_mtime ASC, id ASC",
              "added_desc": "created_at DESC, id DESC", "added_asc": "created_at ASC, id ASC",
              "title": "series_name ASC, title ASC, id ASC"}

def book_out(r):
    return {"id": r["id"], "title": r["title"] or "",
            "display_title": re.sub(r"#\d+$", "", os.path.splitext(os.path.basename(r["file_path"] or ""))[0]).strip(),
            "series_name": r["series_name"] or "", "author": r["author"] or "", "library_id": r["library_id"],
            "file_name": os.path.basename(r["file_path"] or ""), "file_format": (r["file_format"] or "").lower(),
            "file_size": r["file_size"], "total_pages": r["total_pages"], "file_mtime": iso(r["file_mtime"]),
            "created_at": str(r["created_at"]) if r["created_at"] else None,
            "cover_url": cover_url(r.get("cover_image")),
            "download_path": f"/opds/download/{g.db}/{r['id']}"}

@app.get("/health")
def health():
    q("general", "SELECT 1")
    return jsonify(ok=True)

@app.get("/libraries")
@auth
def libraries():
    return jsonify(items=q(g.db, "SELECT id, name FROM libraries WHERE COALESCE(pending_delete, 0) = 0 ORDER BY id"))

@app.get("/books")
@auth
def books():
    page, limit, offset = paging()
    sort = BOOK_SORTS.get(request.args.get("sort", "mtime_desc"), BOOK_SORTS["mtime_desc"])
    where, params = common_where()
    if "series" in request.args:
        where.append("COALESCE(series_name, '') = %s"); params.append(request.args["series"])
    rows = q(g.db, f"SELECT {BOOK_COLS} FROM books WHERE {' AND '.join(where)} ORDER BY {sort} LIMIT %s OFFSET %s",
             (*params, limit + 1, offset))
    return jsonify(items=[book_out(r) for r in rows[:limit]], page=page, limit=limit, has_more=len(rows) > limit)

@app.get("/books/<int:book_id>")
@auth
def book(book_id):
    rows = q(g.db, f"SELECT {BOOK_COLS}, summary, link FROM books WHERE id = %s", (book_id,))
    if not rows:
        return jsonify(error="not found"), 404
    out = book_out(rows[0])
    out["summary"] = clean_summary(rows[0].get("summary"))
    out["summary_trust"] = summary_trust(out["summary"], rows[0].get("link"))
    return jsonify(out)

SERIES_SORTS = {"mtime_desc": "latest_mtime DESC", "mtime_asc": "latest_mtime ASC",
                "added_desc": "latest_added DESC", "added_asc": "latest_added ASC", "name": "series_name ASC"}
_series_cache, SERIES_TTL = {}, 60

@app.get("/series")
@auth
def series():
    page, limit, offset = paging()
    sort = SERIES_SORTS.get(request.args.get("sort", "mtime_desc"), SERIES_SORTS["mtime_desc"])
    key = (g.db, request.query_string)
    hit = _series_cache.get(key)
    if hit and hit[0] > time.time():
        return jsonify(hit[1])
    where, params = common_where(series_search=True)
    rows = q(g.db, f"""SELECT library_id, COALESCE(series_name, '') AS series_name, COUNT(*) AS book_count,
               MAX(file_mtime) AS latest_mtime, MIN(file_mtime) AS first_mtime,
               MAX(created_at) AS latest_added, MIN(id) AS first_book_id
        FROM books WHERE {' AND '.join(where)}
        GROUP BY library_id, COALESCE(series_name, '')
        ORDER BY {sort}, series_name ASC LIMIT %s OFFSET %s""", (*params, limit + 1, offset))
    has_more = len(rows) > limit
    rows = rows[:limit]
    ids = [r["first_book_id"] for r in rows]
    covers = {}
    if ids:
        covers = {c["id"]: c["cover_image"] for c in q(
            g.db, "SELECT id, cover_image FROM books WHERE id IN (" + ",".join(["%s"] * len(ids)) + ")", tuple(ids))}
    items = [{"library_id": r["library_id"], "series_name": r["series_name"], "book_count": r["book_count"],
              "latest_mtime": iso(r["latest_mtime"]), "first_mtime": iso(r["first_mtime"]),
              "latest_added": str(r["latest_added"]) if r["latest_added"] else None,
              "first_book_id": r["first_book_id"],
              "cover_url": cover_url(covers.get(r["first_book_id"]))} for r in rows]
    body = dict(items=items, page=page, limit=limit, has_more=has_more)
    _series_cache[key] = (time.time() + SERIES_TTL, body)
    return jsonify(body)
