import requests

API_KEY = "qwXauZAR1rpDiQguuyGSQqk3Mqbo5NeE"

response = requests.post(
    "https://rest.smsmode.com/sms/v1/messages",
    headers={
        "X-Api-Key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json"
    },
    json={
        "recipient": {
            "to": "918128119019"  # India: no + sign
        },
        "body": {
            "text": "Hello from smsmode!"
        }
    }
)

print(response.status_code)
print(response.text)