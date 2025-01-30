import os
import requests
import zipfile
import json
from fastapi import FastAPI, HTTPException, Body
import sqlite3
import re
from bs4 import BeautifulSoup
import hashlib

app = FastAPI()

# DeepSeek API URL (replace with actual URL)
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/summarize"

# User-Agent and browser versions
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
CHROME_VERSION = "131.0.6778.265"
EDGE_VERSION = "130.0.2849.142"

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

# Get Chrome extension download URL
def get_chrome_extension_url(extension_id: str) -> str:
    return f"https://clients2.google.com/service/update2/crx?response=redirect&os=mac&arch=arm64&os_arch=arm64&nacl_arch=arm&prod=chromecrx&prodversion={CHROME_VERSION}&lang=en-US&x=id%3D{extension_id}%26installsource%3Dondemand%26uc"

# Get Edge extension download URL
def get_edge_extension_url(extension_id: str) -> str:
    return f"https://edge.microsoft.com/extensionwebstorebase/v1/crx?response=redirect&os=linux&arch=x64&os_arch=x86_64&nacl_arch=x86-64&prod=chromiumcrx&prodchannel=dev&prodversion={EDGE_VERSION}&lang=en-US&acceptformat=crx3&x=id%3D{extension_id}%26installsource%3Dondemand%26uc"

# Download CRX file
def download_extension(url: str, output_path: str) -> None:
    response = requests.get(url, allow_redirects=True, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    
    with open(output_path, "wb") as f:
        f.write(response.content)

# Validate file extension
def validate_crx_extension(file_path: str) -> bool:
    return file_path.lower().endswith(".crx")

# Analyze CRX file
def analyze_crx_file(file_path: str):
    if not validate_crx_extension(file_path):
        raise HTTPException(status_code=400, detail="Invalid file extension. Expected .crx file.")

    extract_dir = "extracted_crx"
    with zipfile.ZipFile(file_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)

    manifest_path = os.path.join(extract_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise HTTPException(status_code=400, detail="Manifest file not found in CRX.")

    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    permissions = manifest.get("permissions", [])
    high_risk_permissions = ["storage", "tabs", "webRequest", "webRequestBlocking"]
    risk_score = sum(1 for perm in permissions if perm in high_risk_permissions)

    scripts = []
    third_party_dependencies = []
    for root, _, files in os.walk(extract_dir):
        for file in files:
            if file.endswith(".js"):
                file_path = os.path.join(root, file)
                with open(file_path, 'r') as f:
                    code = f.read()
                    scripts.append({
                        "file": file,
                        "obfuscated": detect_obfuscation(code)
                    })
                    third_party_dependencies.extend(detect_third_party_dependencies(code))

    for root, _, files in os.walk(extract_dir):
        for file in files:
            os.remove(os.path.join(root, file))
    os.rmdir(extract_dir)

    return {
        "permissions_score": risk_score,
        "scripts": scripts,
        "third_party_dependencies": list(set(third_party_dependencies))
    }

# Detect JavaScript obfuscation
def detect_obfuscation(code: str) -> bool:
    return bool(re.search(r'\b(eval|function\([^)]*\)\{.*\})\b', code))

# Detect third-party dependencies
def detect_third_party_dependencies(code: str) -> list:
    urls = re.findall(r'https?://[^\s"\']+', code)
    return list(set(url.split("//")[1].split("/")[0] for url in urls))

# Fetch extension details from Chrome/Edge Web Store
def fetch_extension_details(extension_id: str, store_name: str):
    if store_name.lower() == "chrome":
        store_url = f"https://chrome.google.com/webstore/detail/{extension_id}"
    elif store_name.lower() == "edge":
        store_url = f"https://microsoftedge.microsoft.com/addons/detail/{extension_id}"
    else:
        raise HTTPException(status_code=400, detail="Invalid store name. Must be 'chrome' or 'edge'.")

    try:
        response = requests.get(store_url, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract metadata
        name = soup.find("meta", {"property": "og:title"})["content"] if soup.find("meta", {"property": "og:title"}) else "N/A"
        description = soup.find("meta", {"property": "og:description"})["content"] if soup.find("meta", {"property": "og:description"}) else "N/A"
        
        # Extract additional details (version, reviews, stars - can improve based on store structure)
        version = "N/A"  
        total_reviews = "N/A"
        stars = "N/A"

        return {
            "name": name,
            "description": description,
            "version": version,
            "total_reviews": total_reviews,
            "stars": stars
        }

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching extension details: {str(e)}")

# Summarize analysis results with DeepSeek
def summarize_with_deepseek(analysis_results: dict):
    response = requests.post(DEEPSEEK_API_URL, json={"text": json.dumps(analysis_results)})
    return response.json().get("summary", "")

# API endpoint to analyze CRX file
@app.post("/analyze")
async def analyze_crx(body: dict = Body(...)):
    extension_id = body.get("extension_id")
    store_name = body.get("store_name")

    if not extension_id or not store_name:
        raise HTTPException(status_code=400, detail="Both 'extension_id' and 'store_name' are required.")
        
    extension_details = fetch_extension_details(extension_id, store_name)

    # Determine CRX URL
    if store_name.lower() == "chrome":
        crx_url = get_chrome_extension_url(extension_id)
    elif store_name.lower() == "edge":
        crx_url = get_edge_extension_url(extension_id)
    else:
        raise HTTPException(status_code=400, detail="Invalid store name. Must be 'chrome' or 'edge'.")

    file_path = f"temp_{extension_id}.crx"
    try:
        download_extension(crx_url, file_path)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error downloading extension: {str(e)}")

    # Check if already analyzed
    conn = setup_database()
    cursor = conn.cursor()
    cursor.execute("SELECT analysis_results, summary FROM extensions WHERE id = ?", (extension_id,))
    result = cursor.fetchone()

    if result:
        return {
            "status": "success",
            "extension_details": extension_details,
            "analysis_results": json.loads(result[0]),
            "summary": result[1]
        }

    # Analyze CRX
    analysis_results = analyze_crx_file(file_path)
    summary = summarize_with_deepseek(analysis_results)

    # Save results in DB
    cursor.execute("INSERT INTO extensions (id, analysis_results, summary) VALUES (?, ?, ?)",
                   (extension_id, json.dumps(analysis_results), summary))
    conn.commit()
    conn.close()

    os.remove(file_path)

    return {
        "status": "success",
        "extension_details": extension_details,
        "analysis_results": analysis_results,
        "summary": summary
    }

# Run FastAPI server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
