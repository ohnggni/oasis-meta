#!/usr/bin/env bash
# oasis-meta 설치 스크립트 (OG Reader용 · 북오아시스 전용 · 비공식)
#
#   북오아시스 DB를 읽기 전용으로 조회해 시리즈·정리된 제목·실제 날짜·표지·줄거리를 주는 작은 API를
#   북오아시스 옆에 Docker 컨테이너로 띄웁니다. 책 파일·구글 드라이브에는 접근하지 않습니다.
#
#   사용법:  git clone https://github.com/ohnggni/oasis-meta && cd oasis-meta && bash install.sh
#            업데이트: git pull && bash install.sh
#   요구사항: 북오아시스가 Docker(MariaDB)로 실행 중일 것, docker compose 플러그인
#
#   하는 일 (실행 전에 요약을 보여주고 확인을 받습니다)
#     1) 북오아시스 MariaDB 컨테이너와 Docker 네트워크를 찾습니다
#     2) 읽기 전용 DB 계정(oasis_ro)을 만듭니다: books·libraries 조회, users의 아이디·비밀번호 해시·역할 조회만
#     3) 이 폴더에 .env를 쓰고 컨테이너를 띄웁니다
#   이미 설치돼 있으면 .env(비밀번호)는 그대로 둡니다.
#
#   ⚠️ 북오아시스의 라이브러리별 사용자 권한은 적용되지 않습니다(성인 DB만 관리자 제한).
#      여러 사람이 쓰는 서버라면 쓰지 마십시오.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${OASIS_META_PORT:-5931}"
DB_USER="oasis_ro"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || fail "docker가 없습니다."
docker compose version >/dev/null 2>&1 || fail "docker compose 플러그인이 없습니다."

# ── 1) MariaDB 컨테이너 ─────────────────────────────────────────
say "1/4 북오아시스 DB 컨테이너 찾기"
mapfile -t CANDS < <(docker ps --format '{{.Names}}' | grep -iE 'maria|mysql' || true)
if [ "${#CANDS[@]}" -eq 0 ]; then fail "실행 중인 MariaDB 컨테이너가 없습니다. 북오아시스를 먼저 실행하십시오."; fi
DB_C="${OASIS_META_DB_CONTAINER:-}"
if [ -z "$DB_C" ]; then
  if printf '%s\n' "${CANDS[@]}" | grep -qx bookoasis_mariadb; then DB_C=bookoasis_mariadb
  elif [ "${#CANDS[@]}" -eq 1 ]; then DB_C="${CANDS[0]}"
  else
    echo "여러 개가 있습니다:"; printf '  - %s\n' "${CANDS[@]}"
    read -rp "북오아시스 DB 컨테이너 이름: " DB_C
  fi
fi
docker inspect "$DB_C" >/dev/null 2>&1 || fail "컨테이너 '$DB_C'를 찾을 수 없습니다."
NET="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{"\n"}}{{end}}' "$DB_C" | grep -v '^$' | head -1)"
[ -n "$NET" ] || fail "'$DB_C'의 Docker 네트워크를 알 수 없습니다."
echo "  DB 컨테이너: $DB_C   네트워크: $NET"

# root로 SQL 실행 (컨테이너의 MARIADB_ROOT_PASSWORD / MYSQL_ROOT_PASSWORD, 없으면 물어봄)
ROOT_PW=""
sql() {
  if [ -z "$ROOT_PW" ]; then
    docker exec -i "$DB_C" sh -c 'c=$(command -v mariadb || command -v mysql); "$c" -uroot -p"${MARIADB_ROOT_PASSWORD:-$MYSQL_ROOT_PASSWORD}" -N -B' <<<"$1"
  else
    docker exec -i -e P="$ROOT_PW" "$DB_C" sh -c 'c=$(command -v mariadb || command -v mysql); "$c" -uroot -p"$P" -N -B' <<<"$1"
  fi
}
if ! sql "SELECT 1" >/dev/null 2>&1; then
  read -rsp "DB root 비밀번호: " ROOT_PW; echo
  sql "SELECT 1" >/dev/null 2>&1 || fail "DB에 root로 접속하지 못했습니다."
fi

DBS="$(sql "SHOW DATABASES" | grep -xE 'media_(general|adult)' || true)"
echo "$DBS" | grep -qx media_general || fail "media_general 데이터베이스가 없습니다. 북오아시스(MariaDB) 설치가 맞는지 확인하십시오."
echo "  데이터베이스: $(echo $DBS)"

# ── 2) 비밀번호: 기존 설치면 그대로 ─────────────────────────────
say "2/4 확인"
if [ -f "$DIR/.env" ] && grep -q '^DB_PASSWORD=.' "$DIR/.env"; then
  DB_PW="$(grep '^DB_PASSWORD=' "$DIR/.env" | head -1 | cut -d= -f2-)"
  MODE="업데이트 (기존 .env 유지)"
else
  DB_PW="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)"
  MODE="새로 설치"
fi
USER_EXISTS="$(sql "SELECT COUNT(*) FROM mysql.user WHERE user='$DB_USER'")"
cat <<INFO
  설치 폴더   : $DIR
  방식        : $MODE
  DB 계정     : $DB_USER ($([ "$USER_EXISTS" != 0 ] && echo "이미 있음 → 비밀번호를 위 .env 값으로 맞춤" || echo "새로 만듦"))
  권한        : $(echo $DBS | sed 's/ /, /g')의 books·libraries 조회 + media_general.users(아이디·해시·역할) 조회
  포트        : $PORT  →  http://<서버>:$PORT/health
INFO
read -rp "진행할까요? [y/N] " OK
[ "${OK,,}" = "y" ] || fail "취소했습니다. 아무것도 바꾸지 않았습니다."

# ── 3) 읽기 전용 계정 ───────────────────────────────────────────
say "3/4 읽기 전용 DB 계정"
GRANTS="CREATE USER IF NOT EXISTS '$DB_USER'@'%' IDENTIFIED BY '$DB_PW';
ALTER USER '$DB_USER'@'%' IDENTIFIED BY '$DB_PW';"
for D in $DBS; do
  GRANTS+="
GRANT SELECT ON \`$D\`.books TO '$DB_USER'@'%';
GRANT SELECT ON \`$D\`.libraries TO '$DB_USER'@'%';"
done
GRANTS+="
GRANT SELECT (username, password_hash, role) ON media_general.users TO '$DB_USER'@'%';
FLUSH PRIVILEGES;"
sql "$GRANTS" >/dev/null
echo "  완료"

# ── 4) 파일 쓰고 실행 ───────────────────────────────────────────
say "4/4 oasis-meta 실행"
if [ ! -f "$DIR/.env" ] || ! grep -q '^DB_PASSWORD=.' "$DIR/.env"; then
  umask 077
  printf 'DB_PASSWORD=%s\nDB_HOST=%s\nBOOKOASIS_NETWORK=%s\nPORT=%s\n' "$DB_PW" "$DB_C" "$NET" "$PORT" > "$DIR/.env"
  umask 022
fi

PORT="$(grep '^PORT=' "$DIR/.env" | head -1 | cut -d= -f2- || true)"; PORT="${PORT:-5931}"

cd "$DIR"
docker compose up -d --build
printf '  상태 확인 중'
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    printf '\n\n\033[32m✔ oasis-meta가 실행 중입니다:\033[0m http://<이 서버 IP>:%s\n' "$PORT"
    cat <<NEXT

다음 할 일
  1) 외부에서 쓰려면 리버스 프록시(Nginx Proxy Manager 등)로 https 주소를 연결하십시오.
       예) https://meta.<내 도메인>  →  <이 서버>:$PORT
     집 안에서만 쓴다면 http://<서버 IP>:$PORT 도 됩니다(앱이 경고만 표시).
  2) OG Reader › 서버 설정 › "메타 API 주소"에 그 주소를 넣고, 성인 DB 서버라면 "성인"을 고른 뒤 연결 확인.

관리
  로그     : docker logs --tail 50 oasis_meta
  업데이트 : git pull && bash install.sh
  제거     : cd "$DIR" && docker compose down
             그리고 DB 계정 삭제가 필요하면  DROP USER 'oasis_ro'@'%';
NEXT
    exit 0
  fi
  printf '.'; sleep 2
done
fail "60초 안에 응답이 없습니다.  docker logs --tail 50 oasis_meta 로 원인을 확인하십시오."
