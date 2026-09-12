#!/usr/bin/env bash
# ==============================================================================
# LinguaFlow — run the FastAPI backend and Next.js frontend together.
#
#   ./scripts/development/dev.sh              # run both services
#   ./scripts/development/dev.sh backend      # run the backend only
#   ./scripts/development/dev.sh frontend     # run the frontend only
#   ./scripts/development/dev.sh stop         # stop everything listening on ports 8000/3000
#
# `stop` exists because Ctrl+C is unreliable in Git Bash on Windows: Bash does
# not always forward a signal to the real Windows process, so a signal-free stop
# mechanism is required.
#
#   SKIP_MIGRATE=1 ./scripts/development/dev.sh    # skip alembic upgrade head
#   NO_DB_AUTOSTART=1 ./scripts/development/dev.sh # do not auto-start Postgres in WSL
#   BACKEND_PORT=8001 ./scripts/development/dev.sh # use a different port
#
# The frontend lives in `frontend/`.
# ==============================================================================

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/logs/dev"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
mkdir -p "$LOG_DIR"

# Every log and Python command must support Vietnamese, CJK, Thai, and Arabic.
# Windows consoles default to cp1252; without this, UnicodeEncodeError occurs.
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

BE_PID=""
FE_PID=""

# ─── Process management ────────────────────────────────────────────────────
# On Windows, Git Bash's `$!` is not the PID Task Manager sees, and `kill` often
# does not stop the real Windows process. A previous run can leave Uvicorn on
# port 8000, making the next run fail with "only one usage of each socket
# address". Resolve PIDs from the listening port and use `taskkill /T` to stop
# the entire process tree (Next.js creates child processes).

is_windows() { case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) return 0 ;; *) return 1 ;; esac; }

port_pids() {
    # Every PID listening on the port, one PID per line.
    #
    # A port can have multiple entries (0.0.0.0 and 127.0.0.1, or a stale
    # process). The previous implementation used `head -1`, stopped one PID,
    # then reported the port was still occupied by the other.
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
    # Clear stale processes that still hold the port. Without this, a second run
    # started moments later fails with symptoms hidden in the logs.
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
    # Track the listener by port instead of `$!` for the reasons above.
    [ -n "$FE_PID" ] && kill_tree "$(port_pid "$FRONTEND_PORT")"
    [ -n "$BE_PID" ] && kill_tree "$(port_pid "$BACKEND_PORT")"
    kill_tree "$FE_PID"
    kill_tree "$BE_PID"

    # Wait for the port to be released. A terminated process can retain its
    # socket for several seconds, and returning the prompt too early makes an
    # immediate restart hit the same occupied port.
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

# Wait until the service actually responds. The backend needs roughly 15–20s to
# start tracing and check the database, so printing the URL immediately caused
# people opening it right away to see a misleading connection refusal.
wait_until_up() {
    local label="$1" port="$2" url="${3:-}" limit="${4:-180}" i=0
    printf "   ⏳ Đợi %s" "$label"
    while [ "$i" -lt "$limit" ]; do
        if [ -n "$url" ]; then
            curl -s -m 3 -o /dev/null "$url" && { printf " — sẵn sàng sau %ss\n" "$i"; return 0; }
        elif [ -n "$(port_pid "$port")" ]; then
            printf " — sẵn sàng sau %ss\n" "$i"; return 0
        fi
        # Count seconds instead of printing dots: the backend can take a minute
        # to start, and a silent row of dots looks more like a hang than progress.
        [ $((i % 10)) -eq 0 ] && printf " %ss" "$i" || printf "."
        sleep 1
        i=$((i + 1))
    done
    printf " — quá %ss mà chưa lên.\n" "$limit"
    return 1
}

if [ "${1:-all}" = "stop" ]; then
    # Do not depend on signals or how the previous run began. Resolve PIDs from
    # the listening ports and stop their process trees, even after the old
    # terminal closed or the backend was started through `make run`.
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

# ─── 1. Find a Python interpreter ──────────────────────────────────────────
# Prefer the project's virtual environment, then VIRTUAL_ENV, then system Python.
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
# Docker Engine runs in WSL2 on this machine, and its VM auto-sleeps after about
# 60 seconds of inactivity, taking Postgres down with it. This common local
# failure is recovered automatically instead of merely reporting an error.
db_reachable() {
    "$PYTHON" - "$1" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.settimeout(3)
s.connect(("127.0.0.1", int(sys.argv[1])))
s.close()
PY
}

# Read only the effective DATABASE_URL. Commented examples in .env can also
# match the port pattern and would produce the wrong result.
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

    # Alembic owns the schema (ADR-06); the application does not create tables at startup.
    if [ "${SKIP_MIGRATE:-0}" != "1" ]; then
        echo "📦 alembic upgrade head ..."
        "$PYTHON" -m alembic upgrade head || {
            echo "❌ Migration thất bại — dừng lại." >&2
            exit 1
        }
    fi

    # Roughly one minute is normal, not a hang: the process loads
    # sentence-transformers (and Torch) before other imports—see src/main.py—
    # then checks tracing and the database. On a development machine it takes
    # 74s with preload and 58s without. Set ASSISTANT_RERANK_ENABLED=false and
    # EMBEDDING_FALLBACK_PROVIDER= to disable this loading entirely.
    echo "⚡ Backend: http://localhost:$BACKEND_PORT (docs: /docs) — mất ~1 phút"
    # Use a single process without --workers: ConnectionManager holds sockets in
    # process memory (ADR-18).
    "$PYTHON" -m uvicorn src.main:app --host 0.0.0.0 --port "$BACKEND_PORT" \
        > "$LOG_DIR/backend.log" 2>&1 &
    BE_PID=$!

    if ! wait_until_up "backend" "$BACKEND_PORT" "http://127.0.0.1:$BACKEND_PORT/health" 180; then
        if [ -n "$(port_pid "$BACKEND_PORT")" ]; then
            # Distinguish a crashed process from a live process that exceeded
            # the wait limit. The previous version called both "failed to start",
            # which incorrectly reported slower machines as broken.
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

    # Without .env.local, NEXT_PUBLIC_API_URL is empty and all API calls target
    # the Next.js origin, causing hard-to-diagnose 404 errors.
    if [ ! -f "$FRONTEND_DIR/.env.local" ]; then
        echo "⚠️  Thiếu frontend/.env.local — tạo từ .env.example."
        cp "$FRONTEND_DIR/.env.example" "$FRONTEND_DIR/.env.local"
        echo "    (Điền NEXT_PUBLIC_GOOGLE_OAUTH_CLIENT_ID nếu cần Google Sign-In.)"
    fi

    echo "🎨 Frontend: http://localhost:$FRONTEND_PORT"
    # Do not wrap this in a background subshell: `( ... & )` makes `$!` point to
    # the subshell rather than npm, so FE_PID would be wrong and Next.js would
    # never stop correctly.
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

# ─── 5. Coordination ───────────────────────────────────────────────────────
case "${1:-all}" in
    backend|be)  run_backend ;;
    frontend|fe) run_frontend ;;
    all|"")      run_backend; run_frontend ;;
*)           echo "Usage: ./scripts/development/dev.sh [all|backend|frontend|stop]" >&2; exit 2 ;;
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
