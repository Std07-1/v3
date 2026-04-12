#!/bin/bash
echo "=== ARCHI STATUS ==="
sudo supervisorctl status smc_trader_v3

echo "=== LAST LOGS ==="
tail -10 /opt/smc-trader-v3/logs/supervisor.log

echo "=== CIRCUIT BREAKER ==="
grep -c "CIRCUIT_BREAKER" /opt/smc-trader-v3/logs/supervisor.log
grep "breaker" /opt/smc-trader-v3/bot/scheduling/monitor.py | head -5

echo "=== DIRECTIVES consec ==="
python3 -c "
import json
d = json.load(open('/opt/smc-trader-v3/data/v3_agent_directives.json'))
print('consecutive_errors:', d.get('consecutive_errors', 'NOT_FOUND'))
print('budget_exhausted:', d.get('budget_exhausted_notified', 'NOT_FOUND'))
keys = [k for k in d if 'error' in k.lower() or 'circuit' in k.lower() or 'consec' in k.lower()]
print('error-related keys:', keys)
"
echo "=== DONE ==="
