import requests
import getpass

SMS_API_URL = "https://gateway.seven.io/api/sms"
SMS_API_FROM = "YourSenderName"

def send_sms(api_key, phone_number, sender_name, message):
    headers = {
        "Accept": "application/json",
        "Authorization": f"basic {api_key}"
    }
    data = {
        "to": phone_number,
        "from": sender_name,
        "text": message
    }
    response = requests.post(SMS_API_URL, headers=headers, data=data)
    print(f"SMS API response: {response.status_code}, {response.text}")
    try:
        response_json = response.json()
        success_code = response_json.get("success")
        messages = response_json.get("messages", [])
        if messages:
            message_info = messages[0]
            recipient = message_info.get("recipient")
            text = message_info.get("text")
            success = message_info.get("success")
            error_text = message_info.get("error_text")
            if error_text:
                return (
                    f"📩 **Message Details**:\n"
                    f"👤 **Recipient**: {recipient}\n"
                    f"💬 **Text**: {text}\n"
                    f"✅ **Success**: {success}\n"
                    f"❌ **Error**: {error_text}\n"
                    f"Please contact support to get a new API key."
                )
            else:
                return (
                    f"📩 **Message Details**:\n"
                    f"👤 **Recipient**: {recipient}\n"
                    f"💬 **Text**: {text}\n"
                    f"✅ **Success**: {success}"
                )
        else:
            return (
                f"❌ **Failed to send SMS**.\n"
                f"📄 **Response code**: {success_code}\n"
                f"❌ **Error**: {response_json.get('error_text')}"
            )
    except ValueError:
        return f"❌ **Failed to parse response as JSON**.\n📄 **Response**: {response.text}"

def main():
    api_key = getpass.getpass("Enter your API key: ")
    phone_number = input("Enter the phone number (with country code but without +): ")
    if len(phone_number) == 12 and phone_number.isdigit():
        message = input("Enter the message: ")
        result = send_sms(api_key, phone_number, SMS_API_FROM, message)
        print(result)
    else:
        print("❌ Invalid phone number. Please provide a 12-digit phone number without the +.")

if __name__ == "__main__":
    main()