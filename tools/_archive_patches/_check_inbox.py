import json

d = json.load(open("/opt/smc-trader-v3/data/v3_audit_inbox.json"))
for m in d["messages"]:
    print(f"  id={m['id']}  read={m.get('read')}  read_ts={m.get('read_ts','n/a')}")
print(
    f"Total: {len(d['messages'])}, Unread: {sum(1 for m in d['messages'] if not m.get('read'))}"
)
