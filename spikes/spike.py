import redis, threading, collections, time, uuid
r = redis.Redis(decode_responses=True)
script = r.register_script(open('limiter.lua').read())
def run(name, burst, rate, quota, n_clients, n_req, replay=False):
    r.flushall()
    res = collections.Counter(); lock = threading.Lock()
    def worker(i):
        c = redis.Redis(decode_responses=True)
        s = c.register_script(open('limiter.lua').read())
        for j in range(n_req):
            rid = "fixed" if replay else str(uuid.uuid4())
            out = s(keys=['{org1}:bucket','{org1}:q:202611',f'{{org1}}:idem:{rid}','usage:events'],
                    args=[rate,burst,quota,3000000,600,rid,'org1','key1','1' if replay else '0'])
            with lock: res[out[1]] += 1
    ts=[threading.Thread(target=worker,args=(i,)) for i in range(n_clients)]
    t0=time.time(); [t.start() for t in ts]; [t.join() for t in ts]; dt=time.time()-t0
    print(f"{name}: total={sum(res.values())} {dict(res)} counter={r.get('{org1}:q:202611')} stream={r.xlen('usage:events')} {dt:.2f}s")
    return res
# 1) burst-limited: burst 100, quota big, refill ~0 -> exactly ~100 admitted
a=run("burst_cap", burst=100, rate=0.0001, quota=10**6, n_clients=50, n_req=100)
assert a['ok']==100, a
# 2) quota-limited: burst huge, quota 300 -> exactly 300
b=run("quota_cap", burst=10**6, rate=1000, quota=300, n_clients=50, n_req=100)
assert b['ok']==300, b
# 3) both: burst 500, quota 250 -> 250
c=run("quota_before_burst", burst=500, rate=0.0001, quota=250, n_clients=50, n_req=100)
assert c['ok']==250, c
# 4) replay: same request id from many clients -> 1 consumed
d=run("idempotent_replay", burst=10, rate=0.0001, quota=100, n_clients=1, n_req=50, replay=True)
assert d['ok']==1 and d['replay']==49, d

# 5) void: decrement once, idempotent, and a retry after void is a fresh attempt
r.flushall()
adm=r.register_script(open('limiter.lua').read()); vd=r.register_script(open('void.lua').read())
K=lambda rid:['{org1}:bucket','{org1}:q:202611','{org1}:idem:'+rid,'usage:events']
o=adm(keys=K('R1'),args=[1,10,100,3000000,600,'R1','org1','key1','1']); assert o[1]=='ok'
assert vd(keys=['{org1}:q:202611','{org1}:void:'+o[4],'{org1}:idem:R1','usage:events'],args=[o[4],'org1','R1'])==1
assert vd(keys=['{org1}:q:202611','{org1}:void:'+o[4],'{org1}:idem:R1','usage:events'],args=[o[4],'org1','R1'])==0
assert r.get('{org1}:q:202611')=='0'
o2=adm(keys=K('R1'),args=[1,10,100,3000000,600,'R1','org1','key1','1']); assert o2[1]=='ok' and r.get('{org1}:q:202611')=='1'
o3=adm(keys=K('R1'),args=[1,10,100,3000000,600,'R1','org1','key1','1']); assert o3[1]=='replay'
print("void_and_retry: ok")
print("ALL PASS")