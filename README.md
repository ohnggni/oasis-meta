# oasis-meta

**북오아시스(BookOasis) 전용 · 비공식 보조 도구**입니다. 북오아시스 공식 프로젝트가 아니며 북오아시스 개발자와 관계가 없습니다.

북오아시스 OPDS에는 시리즈명, 실제 파일 날짜, 정리된 제목, 표지 경로, 줄거리 같은 정보가 없습니다.
oasis-meta는 북오아시스 DB(MariaDB)를 **읽기 전용**으로 조회해 이 정보를 JSON으로 알려 주는 작은 API입니다.
[OG Reader](#og-reader)의 "시리즈별 보기"가 이 API를 씁니다.

- 책 파일·구글 드라이브에는 접근하지 않습니다 (DB만 읽음).
- DB에 쓰지 않습니다. 조회 전용 계정(`oasis_ro`)으로만 접속합니다.
- 로그인은 북오아시스 계정 그대로입니다 (HTTP Basic).

> ⚠️ **북오아시스의 라이브러리별 사용자 권한은 적용되지 않습니다** (성인 DB만 관리자로 제한).
> 여러 사람이 쓰는 서버라면 볼 수 없어야 할 목록이 보일 수 있으니 사용하지 마십시오.

## 설치

준비: 북오아시스가 Docker(MariaDB)로 실행 중인 서버, `docker compose`

```bash
git clone https://github.com/ohnggni/oasis-meta
cd oasis-meta
bash install.sh
```

스크립트가 북오아시스 DB 컨테이너를 찾고, 할 일을 요약해 보여준 뒤 확인을 받고 진행합니다.
읽기 전용 계정 생성 → 컨테이너 실행 → 상태 확인(`/health`)까지 자동입니다.

그다음
1. 외부에서 쓰려면 리버스 프록시로 `https://meta.<내 도메인>` → `<서버>:5931` 을 연결합니다 (집 안에서만 쓴다면 `http://<서버 IP>:5931`).
2. OG Reader › 서버 설정 › **메타 API 주소**에 그 주소를 넣고 연결 확인을 누릅니다.

| | |
|---|---|
| 업데이트 | `git pull && bash install.sh` (`.env`의 비밀번호는 그대로) |
| 포트 바꾸기 | `OASIS_META_PORT=5932 bash install.sh` (처음 설치할 때) |
| 로그 | `docker logs --tail 50 oasis_meta` |
| 제거 | `docker compose down` → 필요하면 DB에서 `DROP USER 'oasis_ro'@'%';` |

## API

모든 요청에 북오아시스 계정으로 Basic 인증, `db=general`(기본) 또는 `db=adult`(관리자만).

| 경로 | 내용 |
|---|---|
| `GET /health` | DB 연결 확인 (인증 없음) |
| `GET /libraries` | 라이브러리(카테고리) 목록 |
| `GET /series` | 시리즈(부모 폴더) 목록: 권 수, 최근 파일 날짜, 대표 표지 · `library_id`, `q`, `format`, `sort`(`mtime_desc`·`mtime_asc`·`added_desc`·`added_asc`·`name`), `page`, `limit` |
| `GET /books` | 권 목록: 정리된 제목(`display_title`), 형식, 크기, 쪽수, 파일 날짜, 표지, 다운로드 경로 · `series`, `library_id`, `q`, `format`, `sort`(… `title`), `page`, `limit` |
| `GET /books/{id}` | 권 하나 + `summary`(줄거리), `summary_trust`(`ok`·`suspect`·null) |

- 표지는 `cover_url`(북오아시스 기준 경로)을 북오아시스 주소에 붙여 받습니다. 책 파일도 `download_path`로 북오아시스에서 받습니다.
- `summary_trust`가 `suspect`인 줄거리는 북오아시스가 네이버 쇼핑 검색 결과 첫 항목을 그대로 가져온 것으로, 절반 이상이 다른 책 소개였습니다(표본 조사). 그 외 출처는 표본 60개 중 오염 0건이었습니다.

## 구조

```
OG Reader ──② 시리즈·제목·줄거리 묻기──▶ oasis-meta ──읽기 전용 SQL──▶ 북오아시스 MariaDB
    │                                                                      ▲
    └──① 책 파일·표지 받기─────────────────▶ 북오아시스 ────────────────────┘
```

## OG Reader

북오아시스 전용 안드로이드 전자책·만화 리더입니다. 앱의 설정 › 메타 API 안내에서 이 문서와 같은 내용을 볼 수 있습니다.

## 라이선스

MIT — `LICENSE` 참고
