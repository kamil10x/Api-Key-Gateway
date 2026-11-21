# Runs the whole Day-3 spike inside a Linux container. Usage: see SPIKE_RESULTS.md section 6.
# Run from the spike/ directory. Works with CRLF line endings when piped through `tr -d '\r'`.
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq redis-server >/dev/null
pip install -q redis

echo "== environment =="
echo "cpus: $(nproc)"; python --version; redis-server --version

mkdir -p /tmp/redis_a /tmp/redis_b
(cd /tmp/redis_a && redis-server --port 6379 --daemonize yes --save "" --appendonly no >/dev/null)
(cd /tmp/redis_b && redis-server --port 6380 --daemonize yes --save "" --appendonly yes \
    --appendfsync always --maxmemory-policy noeviction >/dev/null)
sleep 1

echo; echo "== 4.1 exactness with threads (spike.py) =="
python spike.py
echo; echo "== 4.2 / 4.3 config A: no AOF (bench.py 6379) =="
python bench.py 6379
echo; echo "== 4.2 / 4.3 config B: AOF always (bench.py 6380) =="
python bench.py 6380
echo; echo "== 4.4 / 4.5 refill and concurrent replay (extra.py) =="
python extra.py 6379
echo; echo "== 4.6 kill -9 persistence (restart_test.py) =="
python restart_test.py
echo; echo "== done =="