"""Швидка діагностика свіжості preview барів у Redis."""
import redis
import time

r = redis.Redis(host='127.0.0.1', port=6379, db=1, decode_responses=True)
symbols = [
    'XAU_USD', 'EUSTX50', 'GBP_CAD', 'GER30', 'HKG33',
    'NAS100', 'NGAS', 'NZD_CAD', 'SPX500', 'US30',
    'USD_CAD', 'USD_JPY', 'XAG_USD',
]

fresh_count = 0
for sym in symbols:
    key = f'v3_local:preview:curr:{sym}:60'
    ttl = r.ttl(key)
    if ttl > 0:
        age = 1800 - ttl
        status = 'FRESH' if age < 30 else 'stale'
        if age < 30:
            fresh_count += 1
        print(f'{sym:16s} tf=60  ttl={ttl:5d}  age={age:5d}s  {status}')
    else:
        print(f'{sym:16s} tf=60  MISSING/EXPIRED')

print(f'\nFresh (<30s): {fresh_count}/{len(symbols)}')
