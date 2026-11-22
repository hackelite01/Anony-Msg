import requests
import json
import logging

# Set logging level
logging.basicConfig(level=logging.INFO)

# Apollo API Key (Replace with your actual key)
API_KEY = "QhmnAa8X8vQb8J5XZzY1tA"

# Apollo API Endpoints
ORG_SEARCH_URL = "https://api.apollo.io/api/v1/organizations/search"
PEOPLE_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/search"

# Headers for requests
HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "X-Api-Key": API_KEY
}

def get_organization_id(org_name):
    """
    Step 1: Search for the organization using its name or domain.
    """
    payload = {
        "q_organization_name": org_name,
        "page": 1,
        "per_page": 1
    }

    try:
        response = requests.post(ORG_SEARCH_URL, headers=HEADERS, json=payload)
        response.raise_for_status()
        data = response.json()

        if data.get("organizations"):
            org_id = data["organizations"][0]["id"]
            logging.info(f"✅ Found Organization ID: {org_id} for {org_name}")
            return org_id
        else:
            logging.warning(f"⚠️ No organization found for {org_name}")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error fetching organization ID: {e}")
        return None

def get_people_info(org_id):
    """
    Step 2: Search for employees in the given organization and unlock emails.
    """
    payload = {
        "organization_ids": [org_id],
        "page": 1,
        "per_page": 10,
        "reveal_phone_number": True,
        "contact_email_status": ["verified", "likely to engage"]
    }

    try:
        response = requests.post(PEOPLE_SEARCH_URL, headers=HEADERS, json=payload)
        response.raise_for_status()
        data = response.json()

        employees = []
        for person in data.get("people", []):
            email = person.get("email", "Not Available")
            
            # Check if the email is hidden
            if "email_not_unlocked" in email:
                email = "🔒 Email locked (Upgrade Apollo credits)"
            
            employee_info = {
                "name": person.get("name"),
                "designation": person.get("title"),
                "email": email,
                "phone": person.get("phone_numbers", [None])[0],
                "linkedin_url": person.get("linkedin_url"),
                "company_name": person.get("organization_name")
            }
            employees.append(employee_info)

        logging.info(f"✅ Retrieved {len(employees)} employees from org ID {org_id}")
        return employees
    except requests.exceptions.RequestException as e:
        logging.error(f"❌ Error fetching people info: {e}")
        return []

if __name__ == "__main__":
    # Take user input (Single organization name or domain)
    org_input = input("Enter organization name or website URL: ").strip()

    # Step 1: Get Organization ID
    organization_id = get_organization_id(org_input)

    if organization_id:
        # Step 2: Fetch employee information
        employees = get_people_info(organization_id)
        
        # Print final results
        print(json.dumps(employees, indent=2))
    else:
        print("⚠️ No organization found. Please try another name or domain.")
