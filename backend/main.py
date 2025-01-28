from fastapi import FastAPI, File, UploadFile, HTTPException
import sqlite3
import json
import requests
import os
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
    with open(file_path, "rb") as f:
        # Add your CRX analysis logic here
        # For now, return a dummy result
        return {
            "permissions_score": 5,
            "scripts": [],
            "third_party_dependencies": []
        }

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
