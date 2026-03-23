import requests
import json

# IMPORTANT: Replace with your actual API key
API_KEY = "d62cd6bf4f1021c024ccdbd605bfa72c884a0c339c77c825aac11bc671c010e3"

# List of people to enrich
people_to_enrich = [
    {
        "first_name": "GILBERT",
        "last_name": "RIVERA",
        "street_address": "10308 N 97TH DR APARTMENT B",
        "locality": "Peoria",
    },
    {
        "first_name": "SAMANTHA",
        "last_name": "STEFFENS",
        "street_address": "525 N MAY ST 22",
        "locality": "Mesa",
    },
    {
        "first_name": "Antonia",
        "last_name": "Langston",
        "street_address": "7846 North 47th Avenue",
        "locality": "Glendale",
    },
    {
        "first_name": "Dai",
        "last_name": "Jinn",
        "street_address": "5316 West Sunnyside Drive",
        "locality": "Glendale",
    },
    {
        "first_name": "Andrea",
        "last_name": "Ruiz",
        "street_address": "7312 S 22nd Ln",
        "locality": "Phoenix",
    },
    {
        "first_name": "Anh",
        "middle_name": "N.",
        "last_name": "Truong",
        "street_address": "8403 North 83rd Drive",
        "locality": "Peoria",
    }
]

def enrich_person(person_data):
    """
    Enriches a person's data using the People Data Labs API.
    """
    if not API_KEY or API_KEY == "YOUR_API_KEY":
        print("Please replace 'YOUR_API_KEY' with your actual People Data Labs API key.")
        return None

    headers = {
        'Content-Type': 'application/json',
        'X-Api-Key': API_KEY
    }
    
    # The Person Enrichment API endpoint
    url = 'https://api.peopledatalabs.com/v5/person/enrich'

    # Construct the query parameters
    params = {
        'first_name': person_data.get('first_name'),
        'last_name': person_data.get('last_name'),
        'street_address': person_data.get('street_address'),
        'locality': person_data.get('locality'),
    }
    
    # Remove any None values from the params
    params = {k: v for k, v in params.items() if v is not None}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)
        
        data = response.json()
        
        if data['status'] == 200:
            return data['data']
        else:
            print(f"Error enriching data for {person_data.get('first_name')} {person_data.get('last_name')}: {data.get('message')}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return None

if __name__ == "__main__":
    for person in people_to_enrich:
        enriched_data = enrich_person(person)
        if enriched_data:
            print(f"--- Enriched Data for {person.get('first_name')} {person.get('last_name')} ---")
            
            # Extract and print phone numbers
            phone_numbers = enriched_data.get('phone_numbers')
            if isinstance(phone_numbers, list) and phone_numbers:
                print("Phone Numbers:")
                for phone in phone_numbers:
                    print(f"- {phone}")
            else:
                print("No phone numbers found.")

            print("\\n")
        else:
            print(f"Could not retrieve data for {person.get('first_name')} {person.get('last_name')}.\\n")

