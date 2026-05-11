import os
import json
import glob
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# =======================================================================
# DATABASE CONFIGURATION
# Ensure you have DATABASE_URL in your .env file or set it here directly.
# Example: postgresql://user:password@localhost:5432/your_database
# =======================================================================
DB_CONNECTION_STRING = os.getenv("DATABASE_URL")

def parse_budget(budget_str):
    """
    Placeholder to parse string budgets like 'under 1 crore' to numeric values.
    You can implement logic here if your system requires strict integers.
    """
    return 0

def map_call_result_to_preferences(call_data):
    """
    Maps the 'answers' section from the call_results JSON to the preset 
    'preferences' JSON schema.
    """
    answers = call_data.get('answers', {})
    
    # Extract values with fallbacks
    property_type = answers.get('property_type', '').strip()
    budget_str = answers.get('budget', '').strip()
    areas_str = answers.get('areas', '').strip()
    bhk_str = answers.get('bhk', '').strip()
    possession_str = answers.get('possession_timeline', '').strip()
    
    # Transform strings into lists for the array fields
    locations = [a.strip() for a in areas_str.split(',')] if areas_str else []
    configurations = [b.strip() for b in bhk_str.split(',')] if bhk_str else []
    possessions = [possession_str] if possession_str else []
    
    preferences = {
        "budget": budget_str or "Not specified",
        "facing": "Not specified",
        "sizeMax": "Not specified",
        "sizeMin": "Not specified",
        "floorMax": "Not specified",
        "floorMin": "Not specified",
        "location": areas_str or "Not specified",
        "budgetMax": parse_budget(budget_str),
        "budgetMin": parse_budget(budget_str),
        "gatedType": "Not specified",
        "locations": locations,
        "possession": possession_str or "Not decided yet",
        "budgetRange": budget_str or "Not decided yet",
        "possessions": possessions,
        "buildingType": "Not specified",
        "propertyType": property_type or "Not specified",
        "configuration": bhk_str or "Not specified",
        "configurations": configurations,
        "financingOption": "Not specified",
        "possessionTimeline": possession_str or "Not decided yet",
        "matchedPropertyCount": "0",
        "includeGSTRegistration": False
    }
    
    return preferences

def push_single_to_db(call_data):
    if not DB_CONNECTION_STRING:
        print("Error: DATABASE_URL is not set. Please add it to your .env file.")
        return None

    try:
        conn = psycopg2.connect(DB_CONNECTION_STRING)
        cursor = conn.cursor()
        
        name = call_data.get('name') or ''
        phone_number = call_data.get('phone_number') or ''
        lead_id = call_data.get('lead_id') or ''
        
        # The schema requires client_mobile. If unknown, we set a fallback.
        client_mobile = phone_number if phone_number else 'Unknown'
        requirement_name = f"Requirement for {name}" if name else "New Voice Lead Requirement"
        
        preferences = map_call_result_to_preferences(call_data)
        
        # Extract additional notes to store in lr_notes
        additional_notes = call_data.get('answers', {}).get('additional_notes', '')
        
        # We use double quotes for the table name to ensure case sensitivity is respected
        insert_query = """
        INSERT INTO "client_Requirements" (
            client_mobile, requirement_name, preferences, lead_name, lead_mobile, lr_notes, lead_id
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s
        ) RETURNING id;
        """
        
        cursor.execute(insert_query, (
            client_mobile,
            requirement_name,
            Json(preferences),
            name,
            phone_number,
            additional_notes,
            lead_id
        ))
        
        inserted_id = cursor.fetchone()[0]
        conn.commit()
        cursor.close()
        conn.close()
        return inserted_id
        
    except Exception as e:
        print(f"Error inserting data to DB: {e}")
        return None


def process_and_push_to_db():
    # Find all JSON files in the call_results folder
    json_files = glob.glob('call_results/*.json')
    print(f"Found {len(json_files)} call_result files to process.")
    
    for file_path in json_files:
        with open(file_path, 'r') as f:
            try:
                call_data = json.load(f)
            except json.JSONDecodeError:
                print(f"Error parsing JSON in {file_path}. Skipping.")
                continue
            
        inserted_id = push_single_to_db(call_data)
        if inserted_id:
            print(f"Successfully inserted record ID {inserted_id} from {file_path}")
            
    print("Database sync complete.")

if __name__ == "__main__":
    process_and_push_to_db()
