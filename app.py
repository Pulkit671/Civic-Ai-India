import os
import csv
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from google import genai
from google.genai import types
from google.cloud import firestore

app = Flask(__name__)

# Initialize the Gemini Client
client = genai.Client(api_key="YOUR_API_KEY")

# Initialize the Firestore Client
db = firestore.Client()

system_instruction = """
You are an expert civic navigator and legal AI assistant for India. 
Your job is to help citizens solve home, street, and city problems.

CRITICAL MULTILINGUAL RULE:
Detect the language used by the citizen and output your entire response DIRECTLY into that exact same language.

You MUST structure your response into exactly three sections using the markers below:

---COMMUNITY_ACTION---
Provide clear, actionable steps for what citizens can do directly "with their own hands".

---GOVERNMENT_COMPLAINT---
Provide the official government escalation route, deadlines, and a copy-pasteable text template of a formal grievance letter.

---QUICK_SUMMARY---
Provide a short, 3-bullet takeaway under 60 words total.
"""

def log_to_firestore(category, lang, has_image):
    """
    Saves a civic telemetry event permanently to Google Cloud Firestore.
    """
    try:
        doc_ref = db.collection("civic_analytics").document()
        doc_ref.set({
            "timestamp": firestore.SERVER_TIMESTAMP,
            "category": category,
            "detected_language": lang,
            "has_image": bool(has_image)
        })
        print(f"🔥 [Firestore Ingestion] Logged event to civic_analytics: {category}, {lang}")
    except Exception as e:
        print(f"Firestore telemetry logging error: {e}")

def log_to_analytics_stream(user_text, has_image):
    """
    Uses a lightweight Gemini call to categorize incoming civic data for analytics
    and streams it directly to our Firestore NoSQL database.
    """
    try:
        categorize_prompt = f"""
        Analyze this civic issue description and return exactly two words separated by a comma.
        Word 1: The category (choose only from: Roads, Sanitation, Water, Electricity, Public_Safety, Others).
        Word 2: The language it is written in (e.g., Hindi, English, Tamil, Marathi).
        
        Issue: {user_text[:200]}
        """
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=categorize_prompt
        )
        
        # Clean the output (e.g., "Sanitation, Hindi")
        result = response.text.strip().split(",")
        category = result[0].strip()
        lang = result[1].strip() if len(result) > 1 else "Unknown"
        
        # Log to Firestore instead of local CSV
        log_to_firestore(category, lang, has_image)
    except Exception as e:
        print(f"Telemetry logging error: {e}")

@app.route("/", methods=["GET", "POST"])
def home():
    community_data = ""
    government_data = ""
    summary_data = ""
    
    if request.method == "POST":
        user_problem = request.form.get("problem")
        image_file = request.files.get("image_file")
        
        contents_payload = [user_problem]
        has_image = False
        
        if image_file and image_file.filename != '':
            image_bytes = image_file.read()
            mime_type = image_file.content_type
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            contents_payload.append(image_part)
            has_image = True
        
        # Trigger our analytics pipeline running in the background
        log_to_analytics_stream(user_problem, has_image)
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=contents_payload,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.3,
            )
        )
        
        full_text = response.text
        
        if "---COMMUNITY_ACTION---" in full_text and "---GOVERNMENT_COMPLAINT---" in full_text and "---QUICK_SUMMARY---" in full_text:
            try:
                parts = full_text.split("---COMMUNITY_ACTION---")[1].split("---GOVERNMENT_COMPLAINT---")
                community_data = parts[0].strip()
                sub_parts = parts[1].split("---QUICK_SUMMARY---")
                government_data = sub_parts[0].strip()
                summary_data = sub_parts[1].strip()
            except Exception:
                community_data = full_text
        else:
            community_data = full_text

    return render_template("index.html", 
                           community_data=community_data, 
                           government_data=government_data, 
                           summary_data=summary_data)

@app.route('/debug-gate', methods=["GET"])
def debug_gate():
    """
    Diagnostic endpoint verifying backend connectivity to Gemini API.
    """
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            # Fallback to hardcoded key from your script if env var is missing
            api_key = "AIzaSyBrB94A7ogFGbC9upygKS3FH_jpTAhuL-Y"
        
        # Create lightweight test client
        debug_client = genai.Client(api_key=api_key)
        test_response = debug_client.models.generate_content(
            model='gemini-2.5-flash',
            contents="ping"
        )
        
        if test_response.text:
            return jsonify({
                "status": "healthy",
                "gemini_connectivity": "success",
                "api_model": "gemini-2.5-flash",
                "message": "Successfully reached Gemini 2.5 API."
            }), 200
        else:
            return jsonify({
                "status": "unhealthy",
                "gemini_connectivity": "empty_response",
                "message": "Gemini API returned an empty response."
            }), 502
            
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "gemini_connectivity": "error",
            "error_detail": str(e)
        }), 500

if __name__ == "__main__":
    # Cloud Run passes the port via an environment variable
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
