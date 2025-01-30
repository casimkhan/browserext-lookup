import os
import logging
import json
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Body
from datetime import datetime
import sqlite3
import requests
from contextlib import contextmanager
from bs4 import BeautifulSoup
from openai import OpenAI  # Import OpenAI

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Constants
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

class DatabaseManager:
    def __init__(self):
        self.db_path = "crx_analysis.db"

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self):
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS extensions (
                    id TEXT PRIMARY KEY,
                    store_name TEXT,
                    name TEXT,
                    description TEXT,
                    version TEXT,
                    total_reviews INTEGER,
                    stars FLOAT,
                    analysis_results TEXT,
                    manifest TEXT,
                    ai_summary TEXT,
                    last_updated TIMESTAMP
                )
            """)
            conn.commit()

# Initialize Database
db = DatabaseManager()
db.initialize()

app = FastAPI(
    title="Browser Extension Analyzer",
    description="API for analyzing browser extensions with OpenAI integration",
    version="2.0.0"
)

class ExtensionAnalyzer:
    def __init__(self, extension_id: str, store_name: str):
        self.extension_id = extension_id
        self.store_name = store_name.lower()
        self.db = DatabaseManager()
    
    async def _get_cached_analysis(self):
        """Retrieve cached analysis result from the database if available."""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT analysis_results FROM extensions WHERE id = ? AND store_name = ?",
                (self.extension_id, self.store_name)
            )
            row = cursor.fetchone()

            if row:
                return json.loads(row["analysis_results"])  # Safely convert JSON string to dictionary

        return None
    
    async def fetch_store_details(self) -> Dict[str, Any]:
        """Fetch extension details from store"""
        store_url = (
            f"https://chrome.google.com/webstore/detail/{self.extension_id}" 
            if self.store_name == "chrome" 
            else f"https://microsoftedge.microsoft.com/addons/detail/{self.extension_id}"
        )
        
        try:
            response = requests.get(store_url, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            html_content = response.text  # Get the raw HTML content
            return await self._get_openai_extracted_details(html_content)  # Use OpenAI to extract details
        except Exception as e:
            logger.error(f"Failed to fetch store details: {str(e)}")
            raise HTTPException(status_code=404, detail="Extension not found in store")

    async def _get_openai_extracted_details(self, html_content: str) -> Dict[str, Any]:
        """Extract extension details using OpenAI"""
        try:
            # Prepare the input text for OpenAI
            prompt = (
                f"Extract the following details from the Chrome Web Store page HTML content below:\n"
                f"- Name\n"
                f"- Description\n"
                f"- Version\n"
                f"- Total Reviews\n"
                f"- Stars (rating)\n"
                f"- Last Updated\n"
                f"- Developer\n"
                f"- Size\n"
                f"- Category\n\n"
                f"Return the details in JSON format.\n\n"
                f"HTML Content:\n{html_content}"
            )
            
            # Call OpenAI API
            response = client.chat.completions.create(
                model="gpt-4",  # Use the appropriate model
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that extracts structured data from HTML content."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,  # Adjust based on your needs
                temperature=0.3  # Lower temperature for more precise responses
            )
            
            # Extract the JSON response from OpenAI
            json_response = response.choices[0].message.content
            details = json.loads(json_response)  # Parse the JSON response
            return details
            
        except Exception as e:
            logger.error(f"OpenAI API call failed: {str(e)}")
            return {
                "name": "N/A",
                "description": "N/A",
                "version": "N/A",
                "total_reviews": 0,
                "stars": 0.0,
                "last_updated": "N/A",
                "developer": "N/A",
                "size": "N/A",
                "category": "N/A"
            }

    async def analyze_extension(self) -> Dict[str, Any]:
        """Complete extension analysis workflow"""
        try:
            # Check cache first
            cached = await self._get_cached_analysis()
            if cached:
                return cached

            # Fetch store details using OpenAI
            store_details = await self.fetch_store_details()
            
            # Download and analyze CRX
            crx_path = await self._download_crx()
            analysis_results = await self._analyze_crx(crx_path)
            
            # Get AI summary from OpenAI
            ai_summary = await self._get_openai_summary({
                "store_details": store_details,
                "analysis_results": analysis_results
            })
            
            # Combine results
            result = {
                "extension_details": store_details,
                "analysis_results": analysis_results,
                "summary": ai_summary,
                "metadata": {
                    "analyzed_at": datetime.utcnow().isoformat(),
                    "store": self.store_name
                }
            }
            
            # Log the response
            logger.info(f"Backend response: {result}")
            
            # Cache results
            await self._cache_results(result)
            
            # Cleanup
            if os.path.exists(crx_path):
                os.remove(crx_path)
               
            return result
            
        except HTTPException as e:
            logger.error(f"Analysis failed: {str(e)}")
            return {
                "extension_details": None,
                "analysis_results": None,
                "summary": f"Error: {str(e)}",
                "metadata": {
                    "analyzed_at": datetime.utcnow().isoformat(),
                    "store": self.store_name
                }
            }
        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}")
            return {
                "extension_details": None,
                "analysis_results": None,
                "summary": f"Unexpected error: {str(e)}",
                "metadata": {
                    "analyzed_at": datetime.utcnow().isoformat(),
                    "store": self.store_name
                }
            }

    async def _get_openai_summary(self, data: Dict[str, Any]) -> str:
        """Get AI summary using OpenAI"""
        try:
            # Prepare the input text for OpenAI
            analysis_text = (
                f"Extension Name: {data['store_details']['name']}\n"
                f"Description: {data['store_details']['description']}\n"
                f"Version: {data['store_details']['version']}\n"
                f"Developer: {data['store_details']['developer']}\n"
                f"Rating: {data['store_details']['stars']} stars from {data['store_details']['total_reviews']} reviews\n\n"
                f"Security Analysis:\n"
                f"- Permissions required: {', '.join(data['analysis_results']['permissions'])}\n"
                f"- Risk score: {data['analysis_results']['permissions_score']}\n"
                f"- Third-party domains: {', '.join(data['analysis_results']['third_party_dependencies'])}\n"
            )
            
            # Call OpenAI API
            response = client.chat.completions.create(
                model="gpt-4",  # Use the appropriate model
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that summarizes browser extension details and security analysis."},
                    {"role": "user", "content": f"Summarize the following browser extension details and security analysis:\n\n{analysis_text}"}
                ],
                max_tokens=300,  # Adjust based on your needs
                temperature=0.7  # Adjust for creativity vs. precision
            )
            
            # Extract the summary from the response
            summary = response.choices[0].message.content
            if not summary:
                logger.warning("OpenAI returned no summary.")
                return "No summary available."
            
            return summary
            
        except Exception as e:
            logger.error(f"OpenAI API call failed: {str(e)}")
            return "Failed to generate AI summary."

    async def _download_crx(self) -> str:
        """Download the CRX file and return the local path"""
        # Placeholder implementation
        crx_path = f"/tmp/{self.extension_id}.crx"
        logger.info(f"Downloading CRX file to {crx_path}")
        # Implement actual download logic here
        return crx_path

    async def _analyze_crx(self, crx_path: str) -> Dict[str, Any]:
        """Analyze the CRX file and return the results"""
        # Placeholder implementation
        logger.info(f"Analyzing CRX file at {crx_path}")
        return {
            "permissions": ["storage", "tabs"],
            "permissions_score": 3.5,
            "third_party_dependencies": ["example.com"]
        }

    async def _cache_results(self, result: Dict[str, Any]):
        """Cache the analysis results in the database"""
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO extensions (
                    id, store_name, name, description, version, total_reviews, stars, 
                    analysis_results, manifest, ai_summary, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.extension_id,
                    self.store_name,
                    result["extension_details"]["name"],
                    result["extension_details"]["description"],
                    result["extension_details"]["version"],
                    result["extension_details"]["total_reviews"],
                    result["extension_details"]["stars"],
                    json.dumps(result["analysis_results"]),  # Convert dict to JSON string
                    "N/A",  # Placeholder for manifest
                    result["summary"],
                    result["metadata"]["analyzed_at"]
                )
            )
            conn.commit()

@app.post("/analyze")
async def analyze_extension(body: dict = Body(...)):
    """
    Analyze a browser extension with AI-powered summary
    """
    extension_id = body.get("extension_id")
    store_name = body.get("store_name")

    if not extension_id or not store_name:
        raise HTTPException(
            status_code=400,
            detail="Both extension_id and store_name are required"
        )

    if store_name.lower() not in ["chrome", "edge"]:
        raise HTTPException(
            status_code=400,
            detail="store_name must be either 'chrome' or 'edge'"
        )

    analyzer = ExtensionAnalyzer(extension_id, store_name)
    result = await analyzer.analyze_extension()  # Perform analysis synchronously
    return result  # Return the analysis results directly

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
