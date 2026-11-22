import os
import requests
import csv
import threading
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Function to read API keys from the .env file
def load_api_keys():
    with open(".env", "r") as file:
        keys = [line.strip() for line in file.readlines() if line.strip()]
    return keys

# Load API keys
API_KEYS = load_api_keys()

# API URL
API_URL = "https://api.prospeo.io/domain-search"

# Function to fetch emails and LinkedIn from Prospeo's API
def get_emails_and_linkedin(domain, api_key, page=1):
    headers = {
        'Content-Type': 'application/json',
        'X-KEY': api_key
    }

    data = {
        'company': domain,
        'limit': 50,  # Adjust as per your requirement
        'email_type': 'all',
        'company_enrichment': True,
        'page': page  # For pagination
    }

    try:
        response = requests.post(API_URL, json=data, headers=headers)

        if response.status_code == 400:  # Handle 400 Bad Request error
            print(f"API key {api_key} received a 400 error. Trying the next key...")
            return None, None, 400

        response.raise_for_status()  # Raise HTTPError for other non-200 status codes

        data = response.json()
        if data.get('error', False):
            print(f"Error: {data.get('message')}")
            return None, None, data.get('message')

        emails = []
        linkedin_url = data.get('response', {}).get('company_enrichment', {}).get('linkedin', '')

        for item in data.get('response', {}).get('email_list', []):
            full_name = f"{item.get('first_name', '')} {item.get('last_name', '')}".strip()
            email = item.get('email', '')
            emails.append((full_name, email))

        # Check for more emails in subsequent pages
        if data.get('response', {}).get('has_more', False):
            more_emails, _, _ = get_emails_and_linkedin(domain, api_key, page + 1)
            if more_emails:
                emails.extend(more_emails)

        return emails, linkedin_url, None

    except requests.exceptions.RequestException as e:
        print(f"Error fetching data with key {api_key}: {e}")
        return None, None, e

# Function to save results to a CSV file
def save_to_csv(domain, emails, linkedin_url):
    filename = f'output/{domain}_emails.csv'
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Full Name', 'Email', 'LinkedIn URL'])  # CSV header
        for full_name, email in emails:
            writer.writerow([full_name, email, linkedin_url])
    print(f"Saved emails for {domain} to {filename}")
    return filename

# Function to get domain names from text input or a .txt file
def get_domains_from_input(input_source):
    domains = []
    if input_source.endswith('.txt'):
        try:
            with open(input_source, 'r') as file:
                domains = [line.strip() for line in file.readlines()]
        except FileNotFoundError:
            print(f"File {input_source} not found.")
    else:
        domains = [domain.strip() for domain in input_source.split(',')]
    return domains

# Function to search emails using multiple API keys
def search_emails(domain):
    emails = []
    linkedin_url = ''
    for api_key in API_KEYS:
        print(f"Trying API key: {api_key} for domain: {domain}")
        emails, linkedin_url, error = get_emails_and_linkedin(domain, api_key)

        if emails:  # If emails are fetched successfully
            save_to_csv(domain, emails, linkedin_url)
            break

        if error == 400:  # Switch to the next API key if 400 error occurs
            continue

        if not emails and error:
            print(f"Error occurred with API key {api_key}: {error}")
            continue

    if not emails:
        print(f"Failed to fetch emails for domain: {domain} with all available API keys.")

# Function to run the entire email search process for a list of domains
def run_search(input_source):
    domains = get_domains_from_input(input_source)

    # Using a thread pool to run searches concurrently for each domain
    threads = []
    for domain in domains:
        thread = threading.Thread(target=search_emails, args=(domain,))
        threads.append(thread)
        thread.start()

    # Wait for all threads to finish
    for thread in threads:
        thread.join()

# Main function to manage input and trigger search
def main():
    input_source = input("Enter domain names separated by commas or provide a .txt file: ")
    if not input_source:
        print("No input provided!")
        return

    run_search(input_source)
    print("Search completed!")

if __name__ == '__main__':
    main()
