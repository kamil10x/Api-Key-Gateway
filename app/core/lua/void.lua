-- void.lua: undo one admitted request that did not end in 2xx (idempotent)
-- KEYS[1]=monthly counter  KEYS[2]=void guard  KEYS[3]=request marker  KEYS[4]=usage stream
-- ARGV: 1 admit_stream_id  2 org_id  3 request_id
if not redis.call('SET', KEYS[2], '1', 'NX', 'EX', 3600) then return 0 end
if tonumber(redis.call('GET', KEYS[1]) or '0') > 0 then redis.call('DECR', KEYS[1]) end
if redis.call('GET', KEYS[3]) == 'admit' then redis.call('SET', KEYS[3], 'void', 'KEEPTTL') end
redis.call('XADD', KEYS[4], '*', 'kind', 'void', 'admit_id', ARGV[1], 'org_id', ARGV[2],
           'request_id', ARGV[3])
return 1