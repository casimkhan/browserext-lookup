from fastapi import FastAPI, File, UploadFile, HTTPException
import sqlite3
import json
import requests
import os
import zipfile
import re
import shutil  # Added for directory cleanup
from typing import Optional

app = FastAPI()

# DeepSeek API URL (replace with actual URL)
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/summarize"

# Database setup
def setup_database():
    conn = sqlite3.connect("crx_analysis.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS extensions (
            id TEXT PRIMARY KEY,
            analysis_results TEXT,
            summary TEXT
        )
    """)
    conn.commit()
    return conn

# Analyze CRX file
def analyze_crx_file(file_path: str):
    # Extract the CRX file
    extract_dir = "extracted_crx"
    os.makedirs(extract_dir, exist_ok=True)  # Ensure the directory exists
    with zipfile.ZipFile(file_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)

    # Parse manifest.json
    manifest_path = os.path.join(extract_dir, "manifest.json")
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    # Analyze permissions
    permissions = manifest.get("permissions", [])
    high_risk_permissions = ["storage", "tabs", "webRequest", "webRequestBlocking"]
    risk_score = 0
    for perm in permissions:
        if perm in high_risk_permissions:
            risk_score += 1

    # Analyze JavaScript files
    scripts = []
    third_party_dependencies = []
    for root, _, files in os.walk(extract_dir):
        for file in files:
            if file.endswith(".js"):
                file_path = os.path.join(root, file)
                with open(file_path, 'r') as f:
                    code = f.read()
                    # Detect obfuscation
                    is_obfuscated = detect_obfuscation(code)
                    # Detect third-party dependencies
                    dependencies = detect_third_party_dependencies(code)
                    scripts.append({
                        "file": file,
                        "obfuscated": is_obfuscated
                    })
                    third_party_dependencies.extend(dependencies)

    # Clean up extracted files
    shutil.rmtree(extract_dir)  # Safely remove the non-empty directory

    return {
        "permissions_score": risk_score,
        "scripts": scripts,
        "third_party_dependencies": list(set(third_party_dependencies))  # Remove duplicates
    }

def detect_obfuscation(code: str) -> bool:
    """Detect obfuscated or minified code."""
    if re.search(r'\b(eval|function\([^)]*\)\{.*\})\b', code):
        return True
    return False

def detect_third_party_dependencies(code: str) -> list:
    """Detect third-party dependencies in JavaScript code."""
    third_party_domains = []
    # Look for URLs in the code
    urls = re.findall(r'https?://[^\s"\']+', code)
    for url in urls:
        domain = url.split("//")[1].split("/")[0]
        third_party_domains.append(domain)
    return third_party_domains

# Summarize with DeepSeek
def summarize_with_deepseek(analysis_results: dict):
    response = requests.post(DEEPSEEK_API_URL, json={"text": json.dumps(analysis_results)})
    return response.json().get("summary", "")

# API endpoint to analyze CRX file
@app.post("/analyze")
async def analyze_crx(extension_id: str, file: UploadFile = File(...)):
    # Save the uploaded file
    file_path = f"temp_{extension_id}.crx"
    with open(file_path, "wb") as f:
        f.write(await file.read())
    
    # Check if analysis already exists in the database
    conn = setup_database()
    cursor = conn.cursor()
    cursor.execute("SELECT analysis_results, summary FROM extensions WHERE id = ?", (extension_id,))
    result = cursor.fetchone()
    
    if result:
        return {
            "status": "success",
            "analysis_results": json.loads(result[0]),
            "summary": result[1]
        }
    
    # Analyze the CRX file
    analysis_results = analyze_crx_file(file_path)
    summary = summarize_with_deepseek(analysis_results)
    
    # Save results to the database
    cursor.execute("INSERT INTO extensions (id, analysis_results, summary) VALUES (?, ?, ?)",
                   (extension_id, json.dumps(analysis_results), summary))
    conn.commit()
    conn.close()
    
    # Clean up the temporary file
    os.remove(file_path)
    
    return {
        "status": "success",
        "analysis_results": analysis_results,
        "summary": summary
    }

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
