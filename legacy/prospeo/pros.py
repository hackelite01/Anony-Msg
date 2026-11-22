import requests
import csv
import time

# List of API keys
API_KEYS = ["921e8e22a569b42df660c51626b29eb7", "your_api_key_2", "your_api_key_3", "your_api_key_4", "your_api_key_5", "your_api_key_6", "your_api_key_7"]

# API URL
API_URL = "https://api.prospeo.io/domain-search"

# Function to fetch emails from Prospeo's Domain Search API using different API keys
def get_emails_from_domain(domain, api_key, search_id=None):
    headers = {
        'Content-Type': 'application/json',
        'X-KEY': api_key
    }

    if search_id:
        data = {
            'search_id': search_id
        }
    else:
        data = {
            'company': domain,
            'limit': 50,  # Set the limit of emails to fetch
            'email_type': 'all',  # You can change this to 'generic' or 'professional' if needed
            'company_enrichment': False  # Set to True if you want company details as well
        }

    try:
        response = requests.post(API_URL, json=data, headers=headers)
        response.raise_for_status()  # Ensure the request was successful
        data = response.json()
        
        # Check if there are any errors in the response
        if data.get('error', False):
            print(f"Error: {data.get('message')}")
            return [], None

        emails = []
        for item in data.get('response', {}).get('email_list', []):
            email = item.get('email', '')
            first_name = item.get('first_name', '')
            last_name = item.get('last_name', '')
            emails.append((first_name, last_name, email))

        search_id = data.get('response', {}).get('meta', {}).get('search_id', None)
        more_results = data.get('response', {}).get('meta', {}).get('more_results', False)

        return emails, search_id if more_results else None
    
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from API with key {api_key}: {e}")
        return [], None

# Function to save results to a CSV file
def save_to_csv(domain, emails):
    with open(f'{domain}_emails.csv', 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['First Name', 'Last Name', 'Email'])  # CSV header
        writer.writerows(emails)
    print(f"Results saved to {domain}_emails.csv")

# Main function to run the email search and save the results
def search_emails(domain):
    print(f"Searching emails for domain: {domain}")
    all_emails = []
    api_key_index = 0  # Start with the first API key
    search_id = None  # No search_id at first

    # Loop to use all API keys and collect emails
    while api_key_index < len(API_KEYS):
        api_key = API_KEYS[api_key_index]
        print(f"Using API key {api_key}")
        
        # Fetch emails from the current API
        emails, new_search_id = get_emails_from_domain(domain, api_key, search_id)

        if emails:
            all_emails.extend(emails)  # Add the found emails to the list

        if new_search_id:
            search_id = new_search_id  # Set the new search_id for pagination

        # If no more results are available, move to the next API key
        if not new_search_id or len(all_emails) >= 50:  # Adjust 50 based on how many emails you want
            api_key_index += 1
            time.sleep(1)  # Wait for a short time before switching to the next API key

        # If we've exhausted all API keys or collected enough emails, break
        if api_key_index >= len(API_KEYS) or len(all_emails) >= 50:
            break

    # Save all found emails to a CSV file
    if all_emails:
        save_to_csv(domain, all_emails)
    else:
        print("No emails found.")

# User input for the domain name
if __name__ == "__main__":
    domain = input("Enter the domain name: ")
    search_emails(domain)
