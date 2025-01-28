from fastapi import FastAPI, HTTPException
import sqlite3
import requests
import json
import os
import zipfile
import re
from crx_analyzer.download import download_crx  # Importing the CRX download function

app = FastAPI()

# Database setup
def setup_database():
    conn = sqlite3.connect("crx_analysis.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS extensions (
            id TEXT PRIMARY KEY,
            store_name TEXT,
            analysis_results TEXT,
            extension_details TEXT,
            summary TEXT
        )
    """)
    conn.commit()
    return conn

# Analyze CRX file
def analyze_crx_file(file_path: str):
    extract_dir = "extracted_crx"
    with zipfile.ZipFile(file_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)

    manifest_path = os.path.join(extract_dir, "manifest.json")
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    # Analyze permissions
    permissions = manifest.get("permissions", [])
    high_risk_permissions = ["storage", "tabs", "webRequest", "webRequestBlocking"]
    risk_score = sum(1 for perm in permissions if perm in high_risk_permissions)

    # Analyze JavaScript files
    scripts = []
    third_party_dependencies = []
    for root, _, files in os.walk(extract_dir):
        for file in files:
            if file.endswith(".js"):
                file_path = os.path.join(root, file)
                with open(file_path, 'r') as f:
                    code = f.read()
                    is_obfuscated = detect_obfuscation(code)
                    dependencies = detect_third_party_dependencies(code)
                    scripts.append({"file": file, "obfuscated": is_obfuscated})
                    third_party_dependencies.extend(dependencies)

    # Clean up extracted files
    for root, _, files in os.walk(extract_dir):
        for file in files:
            os.remove(os.path.join(root, file))
    os.rmdir(extract_dir)

    return {
        "permissions_score": risk_score,
        "scripts": scripts,
        "third_party_dependencies": list(set(third_party_dependencies)),  # Remove duplicates
    }

def detect_obfuscation(code: str) -> bool:
    if re.search(r'\b(eval|function\([^)]*\)\{.*\})\b', code):
        return True
    return False

def detect_third_party_dependencies(code: str) -> list:
    urls = re.findall(r'https?://[^\s"\']+', code)
    return [url.split("//")[1].split("/")[0] for url in urls]

# Summarize with DeepSeek
def summarize_with_deepseek(analysis_results: dict):
    # Retrieve DeepSeek API URL and API Key from environment variables
    DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/summarize")  # Default URL if not set
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")  # Retrieve API key from environment
    
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="DeepSeek API key is missing from environment variables.")
    
    # Prepare headers with the API key for authentication
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    # Make the request to DeepSeek API
    response = requests.post(
        DEEPSEEK_API_URL,
        json={"text": json.dumps(analysis_results)},
        headers=headers
    )
    
    if response.status_code == 200:
        return response.json().get("summary", "No summary provided.")
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch summary from DeepSeek.")

# URL templates for CRX downloads
CHROME_CRX_URL = "https://clients2.google.com/service/update2/crx?response=redirect&os=mac&arch=arm64&os_arch=arm64&nacl_arch=arm&prod=chromecrx&prodchannel=&prodversion=115.0.5790.171&lang=en-US&acceptformat=crx3,puff&x=id%3D{extension_id}%26installsource%3Dondemand%26uc&authuser=0"
EDGE_CRX_URL = "https://edge.microsoft.com/extensionwebstorebase/v1/crx?response=redirect&os=linux&arch=x64&os_arch=x86_64&nacl_arch=x86-64&prod=chromiumcrx&prodchannel=dev&prodversion=115.0.5790.171&lang=en-US&acceptformat=crx3&x=id%3D{extension_id}%26installsource%3Dondemand%26uc"

# Fetch CRX file from the appropriate store using download function
def fetch_crx(extension_id: str, store_name: str) -> str:
    crx_file_path = download_crx(extension_id, store_name)
    if not crx_file_path:
        raise HTTPException(status_code=404, detail="Extension not found in the specified store.")
    return crx_file_path

# Extract extension details from CRX file
def extract_extension_details(file_path: str):
    extract_dir = "extracted_crx"
    with zipfile.ZipFile(file_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)

    manifest_path = os.path.join(extract_dir, "manifest.json")
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    details = {
        "name": manifest.get("name", "Unknown"),
        "description": manifest.get("description", "No description available."),
        "version": manifest.get("version", "Unknown"),
    }

    # Clean up extracted files
    for root, _, files in os.walk(extract_dir):
        for file in files:
            os.remove(os.path.join(root, file))
    os.rmdir(extract_dir)

    return details

# API endpoint
@app.get("/analyze")
def analyze_extension(extension_id: str, store_name: str):
    conn = setup_database()
    cursor = conn.cursor()

    # Check if already in database
    cursor.execute("SELECT analysis_results, extension_details, summary FROM extensions WHERE id = ? AND store_name = ?", (extension_id, store_name))
    result = cursor.fetchone()
    if result:
        return {
            "analysis_results": json.loads(result[0]),
            "extension_details": json.loads(result[1]),
            "summary": result[2],
        }

    # Fetch CRX file
    crx_file_path = fetch_crx(extension_id, store_name)

    # Analyze and summarize
    analysis_results = analyze_crx_file(crx_file_path)
    extension_details = extract_extension_details(crx_file_path)
    summary = summarize_with_deepseek(analysis_results)

    # Save to database
    cursor.execute(
        "INSERT INTO extensions (id, store_name, analysis_results, extension_details, summary) VALUES (?, ?, ?, ?, ?)",
        (extension_id, store_name, json.dumps(analysis_results), json.dumps(extension_details), summary),
    )
    conn.commit()
    conn.close()

    # Clean up CRX file
    os.remove(crx_file_path)

    return {
        "analysis_results": analysis_results,
        "extension_details": extension_details,
        "summary": summary,
    }
