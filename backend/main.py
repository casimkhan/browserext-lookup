import os
import requests
import zipfile
import json
from fastapi import FastAPI, File, UploadFile, HTTPException
import sqlite3
from typing import Optional
import re

app = FastAPI()

# DeepSeek API URL (replace with actual URL)
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/summarize"

# User-Agent and Chrome/Edge version (for downloading CRX)
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

# Download Chrome extension CRX
def get_chrome_extension_url(extension_id: str, chrome_version: str = CHROME_VERSION) -> str:
    return f"https://clients2.google.com/service/update2/crx?response=redirect&os=mac&arch=arm64&os_arch=arm64&nacl_arch=arm&prod=chromecrx&prodversion={chrome_version}&lang=en-US&x=id%3D{extension_id}%26installsource%3Dondemand%26uc"

# Download Edge extension CRX
def get_edge_extension_url(extension_id: str, edge_version: str = EDGE_VERSION) -> str:
    return f"https://edge.microsoft.com/extensionwebstorebase/v1/crx?response=redirect&os=linux&arch=x64&os_arch=x86_64&nacl_arch=x86-64&prod=chromiumcrx&prodchannel=dev&prodversion={edge_version}&lang=en-US&acceptformat=crx3&x=id%3D{extension_id}%26installsource%3Dondemand%26uc"

# Download extension
def download_extension(url: str, output_path: str) -> None:
    response = requests.get(url, allow_redirects=True, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    
    with open(output_path, "wb") as f:
        f.write(response.content)

# Analyze CRX file
def analyze_crx_file(file_path: str):
    extract_dir = "extracted_crx"
    with zipfile.ZipFile(file_path, 'r') as zip_ref:
        zip_ref.extractall(extract_dir)

    manifest_path = os.path.join(extract_dir, "manifest.json")
    with open(manifest_path, 'r') as f:
        manifest = json.load(f)

    permissions = manifest.get("permissions", [])
    high_risk_permissions = ["storage", "tabs", "webRequest", "webRequestBlocking"]
    risk_score = 0
    for perm in permissions:
        if perm in high_risk_permissions:
            risk_score += 1

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
                    scripts.append({
                        "file": file,
                        "obfuscated": is_obfuscated
                    })
                    third_party_dependencies.extend(dependencies)

    for root, _, files in os.walk(extract_dir):
        for file in files:
            os.remove(os.path.join(root, file))
    os.rmdir(extract_dir)

    return {
        "permissions_score": risk_score,
        "scripts": scripts,
        "third_party_dependencies": list(set(third_party_dependencies))
    }

def detect_obfuscation(code: str) -> bool:
    if re.search(r'\b(eval|function\([^)]*\)\{.*\})\b', code):
        return True
    return False

def detect_third_party_dependencies(code: str) -> list:
    third_party_domains = []
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
async def analyze_crx(extension_id: str, store_name: str):
    # Determine whether to get Chrome or Edge extension URL
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

    # Setup database and check if the extension is already analyzed
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

    # Analyze the CRX file and save the results
    analysis_results = analyze_crx_file(file_path)
    summary = summarize_with_deepseek(analysis_results)

    cursor.execute("INSERT INTO extensions (id, analysis_results, summary) VALUES (?, ?, ?)",
                   (extension_id, json.dumps(analysis_results), summary))
    conn.commit()
    conn.close()

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
