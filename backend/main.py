import os
import logging
import json
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import sqlite3
import requests
from contextlib import contextmanager
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/summarize"
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
    description="API for analyzing browser extensions with DeepSeek AI integration",
    version="2.0.0"
)

class ExtensionAnalyzer:
    def __init__(self, extension_id: str, store_name: str):
        self.extension_id = extension_id
        self.store_name = store_name.lower()
        self.db = DatabaseManager()
    
    def _extract_last_updated(self, soup: BeautifulSoup) -> str:
        """Extract last updated date from the store page"""
        # Try multiple selectors to find the last updated date
        selectors = [
            {"meta": {"itemprop": "dateModified"}},
            {"span": {"class": "last-updated"}},
            {"div": {"class": "last-updated-date"}}
        ]
        
        for selector in selectors:
            element = soup.find(**selector)
            if element:
                return element.get("content", element.text.strip())
        
        return "N/A"  # Return "N/A" if not found

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
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Extract detailed store information
            details = {
                "name": self._extract_meta(soup, "og:title"),
                "description": self._extract_meta(soup, "og:description"),
                "version": self._extract_version(soup),
                "total_reviews": self._extract_reviews(soup),
                "stars": self._extract_rating(soup),
                "last_updated": self._extract_last_updated(soup),
                "developer": self._extract_developer(soup),
                "size": self._extract_size(soup),
                "category": self._extract_category(soup)
            }
            
            return details
            
        except Exception as e:
            logger.error(f"Failed to fetch store details: {str(e)}")
            raise HTTPException(status_code=404, detail="Extension not found in store")

    async def analyze_extension(self) -> Dict[str, Any]:
        """Complete extension analysis workflow"""
        try:
            # Check cache first
            cached = await self._get_cached_analysis()
            if cached:
                return cached

            # Fetch store details
            store_details = await self.fetch_store_details()
            
            # Download and analyze CRX
            crx_path = await self._download_crx()
            analysis_results = await self._analyze_crx(crx_path)
            
            # Get AI summary from DeepSeek
            ai_summary = await self._get_deepseek_summary({
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
            
            # Cache results
            await self._cache_results(result)
            
            # Cleanup
            if os.path.exists(crx_path):
                os.remove(crx_path)
                
            return result
            
        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    async def _get_deepseek_summary(self, data: Dict[str, Any]) -> str:
        """Get AI summary from DeepSeek API"""
        try:
            # Prepare the input text for DeepSeek
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
            
            # Call DeepSeek API
            response = requests.post(
                DEEPSEEK_API_URL,
                json={"text": analysis_text},
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            
            # Ensure the response contains the expected data
            summary = response.json().get("summary")
            if not summary:
                logger.warning("DeepSeek API returned no summary.")
                return "No summary available."
            
            return summary
            
        except requests.exceptions.RequestException as e:
            logger.error(f"DeepSeek API call failed: {str(e)}")
            return "Failed to generate AI summary."
        except ValueError as e:
            logger.error(f"Invalid JSON response from DeepSeek API: {str(e)}")
            return "Failed to generate AI summary."

    def _extract_meta(self, soup: BeautifulSoup, property_name: str) -> str:
        """Extract metadata from store page"""
        meta = soup.find("meta", {"property": property_name})
        return meta["content"] if meta else "N/A"

    def _extract_version(self, soup: BeautifulSoup) -> str:
        """Extract version from store page"""
        version_elem = soup.find("meta", {"itemprop": "version"})
        return version_elem["content"] if version_elem else "N/A"

    def _extract_reviews(self, soup: BeautifulSoup) -> int:
        """Extract review count from store page"""
        reviews_elem = soup.find("meta", {"itemprop": "ratingCount"})
        return int(reviews_elem["content"]) if reviews_elem else 0

    def _extract_rating(self, soup: BeautifulSoup) -> float:
        """Extract rating from store page"""
        rating_elem = soup.find("meta", {"itemprop": "ratingValue"})
        return float(rating_elem["content"]) if rating_elem else 0.0

    def _extract_developer(self, soup: BeautifulSoup) -> str:
        """Extract developer information from store page"""
        developer_elem = soup.find("meta", {"itemprop": "developer"})
        return developer_elem["content"] if developer_elem else "N/A"

    def _extract_size(self, soup: BeautifulSoup) -> str:
        """Extract size information from store page"""
        size_elem = soup.find("meta", {"itemprop": "fileSize"})
        return size_elem["content"] if size_elem else "N/A"

    def _extract_category(self, soup: BeautifulSoup) -> str:
        """Extract category information from store page"""
        category_elem = soup.find("meta", {"itemprop": "category"})
        return category_elem["content"] if category_elem else "N/A"

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
async def analyze_extension(
    body: dict = Body(...),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
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
    background_tasks.add_task(analyzer.analyze_extension)
    return {"message": "Analysis started in the background."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
