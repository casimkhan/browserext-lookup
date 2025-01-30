import os
import requests
import zipfile
import json
from fastapi import FastAPI, HTTPException, Body
import sqlite3
from bs4 import BeautifulSoup

app = FastAPI()

# DeepSeek API URL (replace with actual URL)
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/summarize"

# User-Agent and browser versions
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

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

# Analyze CRX file directly
def analyze_crx_file(file_path: str):
    # Check if the file is a valid CRX file
    try:
        with open(file_path, 'rb') as f:
            header = f.read(4)
            if header != b'Cr24':  # For CRX v3+ format
                raise HTTPException(status_code=400, detail="Not a valid CRX file.")
        
        # Validate zip structure (CRX is essentially a zip file)
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.testzip()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"File validation failed: {str(e)}")

    # Extract the manifest and analyze
    extract_dir = "extracted_crx"
    try:
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="The downloaded file is not a valid ZIP (CRX) file.")

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
    store_url = f"https://chrome.google.com/webstore/detail/{extension_id}" if store_name.lower() == "chrome" else f"https://microsoftedge.microsoft.com/addons/detail/{extension_id}"

    try:
        response = requests.get(store_url, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        name = soup.find("meta", {"property": "og:title"})["content"] if soup.find("meta", {"property": "og:title"}) else "N/A"
        description = soup.find("meta", {"property": "og:description"})["content"] if soup.find("meta", {"property": "og:description"}) else "N/A"
        
        return {
            "name": name,
            "description": description,
            "version": "N/A",  # Can be scraped if needed
            "total_reviews": "N/A",  # Can be scraped if needed
            "stars": "N/A"  # Can be scraped if needed
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
        raise HTTPException(status_code=400, detail="Both 'extension_id', 'store_name' are required.")
        
    # Download CRX file
    try:
        response = requests.get(crx_url, allow_redirects=True, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        file_path = f"temp_{extension_id}.crx"
        with open(file_path, "wb") as f:
            f.write(response.content)
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
