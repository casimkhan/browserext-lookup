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
from openai import OpenAI

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
                return json.loads(row["analysis_results"])

        return None

    async def _extract_chrome_store_details(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract details from Chrome Web Store using BeautifulSoup"""
        try:
            # Find the main container
            name = soup.find('h1', {'class': 'e-f-w'}) or soup.find('h1')
            description = soup.find('div', {'class': 'C-b-p-j-Pb'}) or soup.find('pre', {'class': 'C-b-p-j-Oa'})
            
            # Find review details
            reviews_div = soup.find('div', {'class': 'rsw-stars'})
            stars_text = reviews_div.find('span', {'class': 'ba-bc-Xb'}) if reviews_div else None
            reviews_text = reviews_div.find('span', {'class': 'ba-bc-Yb'}) if reviews_div else None
            
            # Find version and last updated
            additional_info = soup.find_all('div', {'class': 'C-b-p-D-Xe'})
            version = next((info.find('span').text for info in additional_info if 'Version' in info.text), 'N/A')
            last_updated = next((info.find('span').text for info in additional_info if 'Updated' in info.text), 'N/A')
            
            # Find developer
            developer = soup.find('div', {'class': 'e-f-Me'})
            
            return {
                "name": name.text.strip() if name else "N/A",
                "description": description.text.strip() if description else "N/A",
                "version": version,
                "total_reviews": int(reviews_text.text.replace(',', '')) if reviews_text else 0,
                "stars": float(stars_text.text.split('/')[0]) if stars_text else 0.0,
                "last_updated": last_updated,
                "developer": developer.text.strip() if developer else "N/A",
                "size": "N/A",
                "category": self._extract_category(soup)
            }
        except Exception as e:
            logger.error(f"Failed to parse Chrome store details: {str(e)}")
            return self._get_default_details()

    async def _extract_edge_store_details(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract details from Edge Add-ons Store using BeautifulSoup"""
        try:
            # Edge store has different HTML structure
            name = soup.find('h1', {'class': 'product-title'})
            description = soup.find('div', {'class': 'product-description'})
            
            # Rating information
            rating_div = soup.find('div', {'class': 'rating-value'})
            reviews_count = soup.find('div', {'class': 'reviews-count'})
            
            # Additional details
            version_div = soup.find('div', {'data-telemetry-name': 'version'})
            last_updated_div = soup.find('div', {'data-telemetry-name': 'last-updated'})
            developer_div = soup.find('div', {'class': 'publisher-name'})
            
            return {
                "name": name.text.strip() if name else "N/A",
                "description": description.text.strip() if description else "N/A",
                "version": version_div.text.strip() if version_div else "N/A",
                "total_reviews": int(reviews_count.text.split()[0].replace(',', '')) if reviews_count else 0,
                "stars": float(rating_div.text.split('/')[0]) if rating_div else 0.0,
                "last_updated": last_updated_div.text.strip() if last_updated_div else "N/A",
                "developer": developer_div.text.strip() if developer_div else "N/A",
                "size": "N/A",
                "category": self._extract_category(soup)
            }
        except Exception as e:
            logger.error(f"Failed to parse Edge store details: {str(e)}")
            return self._get_default_details()

    def _get_default_details(self) -> Dict[str, Any]:
        """Return default details when parsing fails"""
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

    def _extract_category(self, soup: BeautifulSoup) -> str:
        """Extract category information from the store page"""
        try:
            if self.store_name == "chrome":
                category_div = soup.find('div', {'class': 'C-b-p-D-j'})
                return category_div.text.strip() if category_div else "N/A"
            else:  # Edge store
                category_div = soup.find('div', {'class': 'category-name'})
                return category_div.text.strip() if category_div else "N/A"
        except Exception:
            return "N/A"

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
            soup = BeautifulSoup(response.text, 'html.parser')
            
            if self.store_name == "chrome":
                return await self._extract_chrome_store_details(soup)
            else:
                return await self._extract_edge_store_details(soup)
        except Exception as e:
            logger.error(f"Failed to fetch store details: {str(e)}")
            raise HTTPException(status_code=404, detail="Extension not found in store")

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
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that summarizes browser extension details and security analysis."},
                    {"role": "user", "content": f"Summarize the following browser extension details and security analysis:\n\n{analysis_text}"}
                ],
                max_tokens=300,
                temperature=0.7
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"OpenAI API call failed: {str(e)}")
            return "Failed to generate AI summary."

    async def _download_crx(self) -> str:
        """Download the CRX file and return the local path"""
        crx_path = f"/tmp/{self.extension_id}.crx"
        logger.info(f"Downloading CRX file to {crx_path}")
        # Implement actual download logic here
        return crx_path

    async def _analyze_crx(self, crx_path: str) -> Dict[str, Any]:
        """Analyze the CRX file and return the results"""
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
                    json.dumps(result["analysis_results"]),
                    "N/A",  # Placeholder for manifest
                    result["summary"],
                    result["metadata"]["analyzed_at"]
                )
            )
            conn.commit()

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
            
            # Get AI summary
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
    result = await analyzer.analyze_extension()
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
