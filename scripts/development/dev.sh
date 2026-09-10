#!/usr/bin/env bash
# ==============================================================================
# LinguaFlow — chạy song song Backend (FastAPI) và Frontend (Next.js).
#
#   ./scripts/development/dev.sh              # chạy cả hai
#   ./scripts/development/dev.sh backend      # chỉ backend
#   ./scripts/development/dev.sh frontend     # chỉ frontend
#   ./scripts/development/dev.sh stop         # dừng mọi thứ đang giữ cổng 8000/3000
#
# `stop` có mặt vì Ctrl+C không đáng tin trong Git Bash trên Windows: bash ở đó
# không phải lúc nào cũng chuyển được tín hiệu tới tiến trình Windows thật, nên
# cần một cách dừng không dựa vào tín hiệu nào cả.
#
#   SKIP_MIGRATE=1 ./scripts/development/dev.sh    # bỏ qua alembic upgrade head
#   NO_DB_AUTOSTART=1 ./scripts/development/dev.sh # không tự bật Postgres trong WSL
#   BACKEND_PORT=8001 ./scripts/development/dev.sh # đổi cổng
#
# Frontend nằm trong `frontend/`.
# ==============================================================================

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/logs/dev"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
mkdir -p "$LOG_DIR"

# Mọi log và mọi lệnh Python đều phải in được tiếng Việt / CJK / Thái / Ả Rập.
# Console Windows mặc định là cp1252 nên thiếu dòng này là UnicodeEncodeError.
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

BE_PID=""
FE_PID=""

# ─── Quản lý tiến trình ────────────────────────────────────────────────────
# Trên Windows, `$!` của Git Bash không phải PID mà Task Manager nhìn thấy, và
# `kill` nó thường không hạ được tiến trình Windows thật — lần chạy trước để lại
# uvicorn giữ cổng 8000, khiến lần chạy sau chết vì "only one usage of each
# socket address". Vì vậy PID luôn được tra lại từ chính cổng đang lắng nghe,
# rồi hạ bằng `taskkill /T` để diệt cả cây con (Next.js sinh nhiều tiến trình
# con và chỉ giết tiến trình cha là bỏ sót).

is_windows() { case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) return 0 ;; *) return 1 ;; esac; }

port_pids() {
    # MỌI PID đang LISTENING trên một cổng, mỗi dòng một PID.
    #
    # Số nhiều là cần thiết: một cổng có thể có nhiều mục (0.0.0.0 và 127.0.0.1,
    # hoặc một tiến trình cũ chưa chết hẳn), và bản trước chỉ lấy `head -1` nên
    # nó giết một cái rồi báo "không giải phóng được" vì cái thứ hai vẫn còn.
    if is_windows; then
        netstat -ano 2>/dev/null \
            | grep -E "[:.]$1[[:space:]]" \
            | grep -i "LISTENING" \
            | awk '{print $NF}' | tr -d '\r' | sort -u
    else
        lsof -ti ":$1" -sTCP:LISTEN 2>/dev/null | sort -u
    fi
}

port_pid() { port_pids "$1" | head -1; }

kill_tree() {
    local pid="$1"
    [ -n "$pid" ] || return 0
    if is_windows; then
        taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
    else
        kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    fi
}

free_port() {
    # Dọn tiến trình cũ còn giữ cổng. Không làm việc này thì lần chạy thứ hai
    # trong vòng vài giây sẽ hỏng, và triệu chứng nằm trong log chứ không hiện
    # ra màn hình.
    local port="$1" label="$2" pids pid
    pids="$(port_pids "$port")"
    [ -n "$pids" ] || return 0
    echo "♻️  Cổng $port ($label) đang bị PID $(echo "$pids" | tr '\n' ' ')giữ — đang dọn."
    for _ in $(seq 1 20); do
        pids="$(port_pids "$port")"
        [ -z "$pids" ] && return 0
        for pid in $pids; do kill_tree "$pid"; done
        sleep 0.5
    done
    echo "❌ Không giải phóng được cổng $port. Hãy đóng tiến trình đó rồi chạy lại." >&2
    exit 1
}

cleanup() {
    trap - INT TERM EXIT
    echo ""
    echo "=================================================="
    echo " 🛑 Đang dừng Backend và Frontend..."
    echo "=================================================="
    # Theo cổng chứ không theo `$!`, vì lý do đã nói ở trên.
    [ -n "$FE_PID" ] && kill_tree "$(port_pid "$FRONTEND_PORT")"
    [ -n "$BE_PID" ] && kill_tree "$(port_pid "$BACKEND_PORT")"
    kill_tree "$FE_PID"
    kill_tree "$BE_PID"

    # Đợi cổng thật sự nhả. Một tiến trình đã nhận lệnh giết vẫn giữ socket
    # thêm vài giây — backend nạp torch thì lâu hơn nữa — và trả prompt về sớm
    # khiến lần chạy ngay sau đó gặp đúng cổng chưa nhả.
    local waited=0 port
    for port in "$FRONTEND_PORT" "$BACKEND_PORT"; do
        while [ -n "$(port_pid "$port")" ] && [ "$waited" -lt 30 ]; do
            sleep 1
            waited=$((waited + 1))
        done
    done
    echo " ✅ Đã dừng xong."
}
trap cleanup INT TERM EXIT

# Đợi tới khi dịch vụ thật sự trả lời. Trước đây script in URL ngay lập tức,
# trong khi backend cần ~15–20s để khởi động (nạp tracing, kiểm tra CSDL), nên
# ai mở link ngay cũng gặp connection refused và tưởng là hỏng.
wait_until_up() {
    local label="$1" port="$2" url="${3:-}" limit="${4:-180}" i=0
    printf "   ⏳ Đợi %s" "$label"
    while [ "$i" -lt "$limit" ]; do
        if [ -n "$url" ]; then
            curl -s -m 3 -o /dev/null "$url" && { printf " — sẵn sàng sau %ss\n" "$i"; return 0; }
        elif [ -n "$(port_pid "$port")" ]; then
            printf " — sẵn sàng sau %ss\n" "$i"; return 0
        fi
        # Đếm giây thay vì rắc dấu chấm: backend mất khoảng một phút để lên và
        # một hàng chấm im lặng nhìn giống treo máy hơn là đang chạy.
        [ $((i % 10)) -eq 0 ] && printf " %ss" "$i" || printf "."
        sleep 1
        i=$((i + 1))
    done
    printf " — quá %ss mà chưa lên.\n" "$limit"
    return 1
}

if [ "${1:-all}" = "stop" ]; then
    # Không dựa vào tín hiệu, không cần biết lần chạy trước bắt đầu thế nào:
    # tra PID từ chính cổng đang lắng nghe rồi hạ cả cây tiến trình. Chạy được
    # cả khi terminal cũ đã đóng, hoặc backend được khởi động bằng `make run`.
    trap - INT TERM EXIT
    stopped=0
    for target in "$FRONTEND_PORT:frontend" "$BACKEND_PORT:backend"; do
        target_port="${target%%:*}"
        target_name="${target##*:}"
        if [ -n "$(port_pids "$target_port")" ]; then
            free_port "$target_port" "$target_name"
            stopped=1
        fi
    done
    [ "$stopped" -eq 1 ] && echo " ✅ Đã dừng xong." || echo " ℹ️  Không có gì đang chạy trên $BACKEND_PORT/$FRONTEND_PORT."
    exit 0
fi

echo "=================================================="
echo " 🚀 LinguaFlow — môi trường phát triển"
echo "=================================================="

# ─── 1. Tìm trình thông dịch Python ────────────────────────────────────────
# Ưu tiên môi trường ảo trong dự án, sau đó dùng VIRTUAL_ENV hoặc Python hệ thống.
pick_python() {
    local candidates=(
        "$ROOT_DIR/.venv/bin/python"
        "$ROOT_DIR/.venv/Scripts/python.exe"
        "${VIRTUAL_ENV:-}/bin/python"
        "${VIRTUAL_ENV:-}/Scripts/python.exe"
    )
    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ] || [ -f "$candidate" ]; then echo "$candidate"; return 0; fi
    done
    command -v python3 || command -v python || return 1
}

PYTHON="$(pick_python)" || {
    echo "❌ Không tìm thấy Python. Hãy tạo .venv hoặc đặt VIRTUAL_ENV." >&2
    exit 1
}
echo "🐍 Python: $PYTHON"

if ! "$PYTHON" -c "import uvicorn, langgraph" 2>/dev/null; then
    echo "❌ Môi trường Python thiếu dependency. Chạy: $PYTHON -m pip install -r requirements.txt" >&2
    exit 1
fi

# ─── 2. PostgreSQL ─────────────────────────────────────────────────────────
# Docker Engine chạy trong WSL2 trên máy này, và VM tự ngủ sau ~60s idle — kéo
# theo Postgres. Đây là sự cố thường gặp nhất khi chạy dự án, nên script tự bật
# lại thay vì chỉ báo lỗi rồi thoát.
db_reachable() {
    "$PYTHON" - "$1" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.settimeout(3)
s.connect(("127.0.0.1", int(sys.argv[1])))
s.close()
PY
}

# Chỉ lấy DATABASE_URL đang có hiệu lực; các dòng ví dụ bị comment trong .env
# cũng khớp mẫu cổng và sẽ cho số sai.
DB_PORT="$(grep -E '^DATABASE_URL=' .env 2>/dev/null | head -1 \
    | grep -oE '@[^:/]+:[0-9]+' | grep -oE '[0-9]+$' | tr -d '\r')"
DB_PORT="${DB_PORT:-5432}"

if ! db_reachable "$DB_PORT"; then
    if [ "${NO_DB_AUTOSTART:-0}" != "1" ] && command -v wsl >/dev/null 2>&1; then
        echo "🐘 PostgreSQL chưa chạy — thử bật trong WSL..."
        wsl -e bash -lc "cd '$(wsl wslpath -a "$ROOT_DIR" 2>/dev/null || echo .)' && docker compose up -d postgres" >/dev/null 2>&1 || true
        for _ in $(seq 1 20); do
            db_reachable "$DB_PORT" && break
            sleep 1
        done
    fi
fi

if ! db_reachable "$DB_PORT"; then
    echo "⚠️  Không kết nối được PostgreSQL ở localhost:$DB_PORT." >&2
    echo "    Từ WSL chạy: docker compose up -d postgres" >&2
    echo "    Kiểm tra Docker Desktop/WSL rồi chạy lại." >&2
    exit 1
fi
echo "🐘 PostgreSQL: localhost:$DB_PORT — OK"

# ─── 3. Backend ────────────────────────────────────────────────────────────
run_backend() {
    free_port "$BACKEND_PORT" "backend"

    # Alembic sở hữu schema (ADR-06); app không tự tạo bảng lúc khởi động.
    if [ "${SKIP_MIGRATE:-0}" != "1" ]; then
        echo "📦 alembic upgrade head ..."
        "$PYTHON" -m alembic upgrade head || {
            echo "❌ Migration thất bại — dừng lại." >&2
            exit 1
        }
    fi

    # Khoảng một phút là bình thường, không phải treo: tiến trình nạp
    # sentence-transformers (keo theo torch) truoc moi import khac — xem đầu
    # `src/main.py` — rồi mới đến kiểm tra tracing và CSDL. Đo trên máy dev:
    # 74s khi có preload, 58s khi không. Đặt ASSISTANT_RERANK_ENABLED=false *và*
    # EMBEDDING_FALLBACK_PROVIDER= (rỗng) nếu muốn bỏ hẳn phần nạp đó.
    echo "⚡ Backend: http://localhost:$BACKEND_PORT (docs: /docs) — mất ~1 phút"
    # Một tiến trình duy nhất, không --workers: ConnectionManager giữ socket
    # trong bộ nhớ tiến trình (ADR-18).
    "$PYTHON" -m uvicorn src.main:app --host 0.0.0.0 --port "$BACKEND_PORT" \
        > "$LOG_DIR/backend.log" 2>&1 &
    BE_PID=$!

    if ! wait_until_up "backend" "$BACKEND_PORT" "http://127.0.0.1:$BACKEND_PORT/health" 180; then
        if [ -n "$(port_pid "$BACKEND_PORT")" ]; then
            # Phân biệt hai thứ rất khác nhau: tiến trình chết, và tiến trình
            # còn sống nhưng khởi động lâu hơn hạn chờ. Bản trước gộp cả hai
            # thành "không khởi động được", nên một máy chậm bị báo là hỏng.
            echo "⚠️  Backend vẫn đang chạy nhưng chưa trả lời sau 180s." >&2
            echo "    Nó có thể lên muộn — theo dõi bằng: tail -f logs/dev/backend.log" >&2
        else
            echo "❌ Backend không khởi động được." >&2
        fi
        echo "    20 dòng cuối của logs/dev/backend.log:" >&2
        tail -20 "$LOG_DIR/backend.log" >&2
        exit 1
    fi
}

# ─── 4. Frontend ───────────────────────────────────────────────────────────
run_frontend() {
    [ -d "$FRONTEND_DIR" ] || { echo "❌ Không thấy $FRONTEND_DIR" >&2; exit 1; }
    command -v npm >/dev/null 2>&1 || { echo "❌ Không thấy npm trong PATH." >&2; exit 1; }

    free_port "$FRONTEND_PORT" "frontend"

    if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
        echo "📥 Cài dependency frontend (npm install) ..."
        (cd "$FRONTEND_DIR" && npm install) || exit 1
    fi

    # Không có .env.local thì NEXT_PUBLIC_API_URL rỗng và mọi lời gọi API
    # trỏ về chính origin của Next — lỗi 404 rất khó đoán.
    if [ ! -f "$FRONTEND_DIR/.env.local" ]; then
        echo "⚠️  Thiếu frontend/.env.local — tạo từ .env.example."
        cp "$FRONTEND_DIR/.env.example" "$FRONTEND_DIR/.env.local"
        echo "    (Điền NEXT_PUBLIC_GOOGLE_OAUTH_CLIENT_ID nếu cần Google Sign-In.)"
    fi

    echo "🎨 Frontend: http://localhost:$FRONTEND_PORT"
    # Không bọc trong subshell chạy nền: `( ... & )` khiến `$!` trỏ vào subshell
    # chứ không phải npm, nên FE_PID trước đây luôn sai và Next không bao giờ bị
    # dừng đúng cách.
    cd "$FRONTEND_DIR"
    npm run dev -- --port "$FRONTEND_PORT" > "$LOG_DIR/frontend.log" 2>&1 &
    FE_PID=$!
    cd "$ROOT_DIR"

    if ! wait_until_up "frontend" "$FRONTEND_PORT" "" 120; then
        echo "❌ Frontend không khởi động được. 20 dòng cuối của logs/dev/frontend.log:" >&2
        tail -20 "$LOG_DIR/frontend.log" >&2
        exit 1
    fi
}

# ─── 5. Điều phối ──────────────────────────────────────────────────────────
case "${1:-all}" in
    backend|be)  run_backend ;;
    frontend|fe) run_frontend ;;
    all|"")      run_backend; run_frontend ;;
    *)           echo "Dùng: ./scripts/development/dev.sh [all|backend|frontend|stop]" >&2; exit 2 ;;
esac

echo ""
echo "=================================================="
[ -n "$BE_PID" ] && echo "    Backend  : http://localhost:$BACKEND_PORT/docs"
[ -n "$FE_PID" ] && echo "    Frontend : http://localhost:$FRONTEND_PORT"
echo "    Log      : logs/dev/backend.log / logs/dev/frontend.log"
echo " 💡 Ctrl+C để dừng. Nếu Ctrl+C không ăn (hay gặp trong Git Bash trên"
echo "    Windows), mở terminal khác và chạy: ./scripts/development/dev.sh stop"
echo "=================================================="
echo ""

wait
