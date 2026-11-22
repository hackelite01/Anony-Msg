import requests

API_KEY = 'QhmnAa8X8vQb8J5XZzY1tA'

# # def get_cxo_contacts(org_name_or_url):
# #     url = "https://api.apollo.io/api/v1/mixed_people/search"
# #     headers = {
# #         "accept": "application/json",
# #         "Cache-Control": "no-cache",
# #         "Content-Type": "application/json"
# #     }
# #     payload = {
# #         "api_key": API_KEY,
# #         "q_organization_domains": [org_name_or_url],
# #         "title_keywords": ["CFO", "CEO", "CXO"]
# #     }
    
# #     response = requests.post(url, json=payload, headers=headers)
# #     if response.status_code == 200:
# #         data = response.json()
# #         contacts = []
# #         for person in data.get('people', []):
# #             contact_info = {
# #                 "name": person.get("name"),
# #                 "designation": person.get("title"),
# #                 "email": person.get("email"),
# #                 "phone": person.get("phone"),
# #                 "company_name": person.get("organization", {}).get("name")
# #             }
# #             contacts.append(contact_info)
# #         return contacts
# #     else:
# #         print(f"Error: {response.status_code}, {response.text}")
# #         return []

# # def main():
# #     input_data = input("Enter organization name/URL or path to file: ")
    
# #     if input_data.endswith('.txt'):
# #         with open(input_data, 'r') as file:
# #             inputs = [line.strip() for line in file.readlines()]
# #     else:
# #         inputs = [input_data]
    
# #     all_contacts = []
# #     for input_item in inputs:
# #         contacts = get_cxo_contacts(input_item)
# #         all_contacts.extend(contacts)
    
# #     for contact in all_contacts:
# #         print(contact)

# # if __name__ == "__main__":
# #     main()

# def get_cxo_contacts(org_name_or_url):
#     url = "https://api.apollo.io/api/v1/mixed_people/search"
#     headers = {
#         "accept": "application/json",
#         "Cache-Control": "no-cache",
#         "Content-Type": "application/json"
#     }
#     payload = {
#         "api_key": API_KEY,
#         "q_organization_domains": [org_name_or_url],
#         "title_keywords": ["CFO", "CEO", "CXO"]
#     }
    
#     response = requests.post(url, json=payload, headers=headers)
#     if response.status_code == 200:
#         data = response.json()
#         contacts = []
#         for person in data.get('people', []):
#             contact_info = {
#                 "name": person.get("name"),
#                 "designation": person.get("title"),
#                 "email": person.get("email"),
#                 "phone": person.get("phone"),
#                 "company_name": person.get("organization", {}).get("name")
#             }
#             contacts.append(contact_info)
#         return contacts
#     else:
#         print(f"Error: {response.status_code}, {response.text}")
#         return []

# def main():
#     input_data = input("Enter organization name/URL or path to file: ")
    
#     if input_data.endswith('.txt'):
#         with open(input_data, 'r') as file:
#             inputs = [line.strip() for line in file.readlines()]
#     else:
#         inputs = [input_data]
    
#     all_contacts = []
#     for input_item in inputs:
#         contacts = get_cxo_contacts(input_item)
#         all_contacts.extend(contacts)
    
#     for contact in all_contacts:
#         print(f"Name: {contact['name']}")
#         print(f"Designation: {contact['designation']}")
#         print(f"Email: {contact['email']}")
#         print(f"Phone: {contact['phone']}")
#         print(f"Company: {contact['company_name']}")
#         print("-" * 40)

# if __name__ == "__main__":
#     main()

def get_all_contacts(org_domain):
    """ Fetch all available contacts from the given company domain """
    if not org_domain:
        print("Error: Organization domain is missing.")
        return []

    url = "https://api.apollo.io/api/v1/mixed_people/search"
    headers = {"accept": "application/json", "Content-Type": "application/json"}
    payload = {
        "api_key": API_KEY,
        "q_organization_domains": [org_domain],  # Ensure this is a valid domain like 'lincolnpharma.com'
    }

    response = requests.post(url, json=payload, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        print(f"API Response: {data}")  # Debugging

        contacts = []
        for person in data.get('people', []):
            contact_info = {
                "name": person.get("name"),
                "designation": person.get("title"),
                "email": person.get("email"),
                "phone": person.get("phone"),
                "company_name": person.get("organization", {}).get("name")
            }
            contacts.append(contact_info)

        return contacts
    else:
        print(f"Error fetching contacts: {response.status_code}, {response.text}")
        return []

def main():
    org_domain = input("Enter organization domain (e.g., lincolnpharma.com): ").strip()

    # Fetch all available contacts
    contacts = get_all_contacts(org_domain)

    # Display results
    if contacts:
        for contact in contacts:
            print(f"Name: {contact['name']}")
            print(f"Designation: {contact['designation']}")
            print(f"Email: {contact['email']}")
            print(f"Phone: {contact['phone']}")
            print(f"Company: {contact['company_name']}")
            print("-" * 40)
    else:
        print("No contacts found.")

if __name__ == "__main__":
    main()
