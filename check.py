import requests

SOURCE = "nano_1mia3pb64sauxh9mza64kf9h9m4wtfsygyj8eyyfp3ed3q8jagbhrid6emea"
NODE = "https://rpc.nano.to"

print(f"Длина: {len(SOURCE)}")
print(f"repr : {repr(SOURCE)}")

r = requests.post(NODE, json={"action": "validate_account_number", "account": SOURCE}).json()
print(f"Нода : {r}")

info = requests.post(NODE, json={"action": "account_info", "account": SOURCE}).json()
print(f"Инфо : {info.get('error', 'OK, frontier=' + info.get('frontier','?')[:16])}")
