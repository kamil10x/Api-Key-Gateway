-- admit.lua: atomic quota check + token bucket + replay marker + usage event (D-05, D-06, D-10)
-- KEYS[1]=bucket  KEYS[2]=monthly counter  KEYS[3]=request marker  KEYS[4]=usage stream
-- ARGV: 1 rate/s  2 burst  3 monthly quota  4 counter TTL s  5 marker TTL s
--       6 request_id  7 org_id  8 key_id  9 use_marker ('1' or '0')
-- Returns {allowed, reason, remaining, retry_after_ms, stream_id}. A rejection changes nothing.
local t = redis.call('TIME')
local now_ms = t[1] * 1000 + math.floor(t[2] / 1000)
local rate  = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local quota = tonumber(ARGV[3])
local use_idem = ARGV[9] == '1'

local used = tonumber(redis.call('GET', KEYS[2]) or '0')

if use_idem then
  local m = redis.call('GET', KEYS[3])
  if m == 'admit' then return {1, 'replay', quota - used, 0, ''} end
  if m == 'void'  then redis.call('DEL', KEYS[3]) end
end

if used >= quota then
  return {0, 'quota_exceeded', 0, -1, ''}
end

local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1])
local ts = tonumber(b[2])
if tokens == nil then tokens = burst; ts = now_ms end
tokens = math.min(burst, tokens + math.max(0, now_ms - ts) * rate / 1000)

if tokens < 1 then
  local wait_ms = math.ceil((1 - tokens) * 1000 / rate)
  return {0, 'rate_limited', 0, wait_ms, ''}
end

redis.call('HSET', KEYS[1], 'tokens', tokens - 1, 'ts', now_ms)
redis.call('PEXPIRE', KEYS[1], math.ceil(burst / rate * 1000) * 2 + 1000)
used = redis.call('INCR', KEYS[2])
if used == 1 then redis.call('EXPIRE', KEYS[2], ARGV[4]) end
if use_idem then redis.call('SET', KEYS[3], 'admit', 'EX', ARGV[5]) end
local sid = redis.call('XADD', KEYS[4], '*', 'kind', 'admit', 'request_id', ARGV[6],
                      'org_id', ARGV[7], 'key_id', ARGV[8], 'ts_ms', now_ms)
return {1, 'ok', quota - used, 0, sid}