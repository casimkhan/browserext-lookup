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
import zipfile
import io
import re

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
        """Extract details from Chrome Web Store using structured data"""
        try:
            store_data = {
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

            # Try to extract from JSON-LD structured data
            script_data = soup.find('script', type='application/ld+json')
            if script_data:
                try:
                    json_data = json.loads(script_data.string)
                    store_data.update({
                        "name": json_data.get("name", "N/A"),
                        "description": json_data.get("description", "N/A"),
                        "version": json_data.get("softwareVersion", "N/A"),
                        "developer": json_data.get("author", {}).get("name", "N/A")
                    })

                    if "aggregateRating" in json_data:
                        store_data.update({
                            "stars": float(json_data["aggregateRating"].get("ratingValue", 0.0)),
                            "total_reviews": int(json_data["aggregateRating"].get("reviewCount", 0))
                        })
                except json.JSONDecodeError:
                    pass

            # Fallback to HTML parsing if structured data is incomplete
            if store_data["version"] == "N/A":
                additional_info = soup.find_all('div', {'class': 'C-b-p-D-Xe'})
                store_data["version"] = next(
                    (info.find('span').text for info in additional_info if 'Version' in info.text),
                    'N/A'
                )

            return store_data
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
                "category": "N/A"
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

    async def _download_crx(self) -> io.BytesIO:
        """Download the CRX file and return the file object"""
        try:
            if self.store_name == "chrome":
                crx_url = f"https://clients2.google.com/service/update2/crx?response=redirect&prodversion=49.0&x=id%3D{self.extension_id}%26uc"
            else:
                crx_url = f"https://edge.microsoft.com/extensionwebstorebase/v1/crx?response=redirect&prodversion=109.0&x=id%3D{self.extension_id}%26uc"

            response = requests.get(crx_url, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            return io.BytesIO(response.content)
        except Exception as e:
            logger.error(f"CRX download failed: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to download extension package")

    def _calculate_risk_score(self, manifest: dict) -> float:
        """Calculate security risk score based on manifest permissions"""
        high_risk_permissions = {
            'debugger', 'proxy', 'webRequest', 'webRequestBlocking',
            'desktopCapture', 'management', 'privacy', 'sessions'
        }
        
        permissions = set(manifest.get('permissions', []) + manifest.get('optional_permissions', []))
        return min(len(permissions) + len(high_risk_permissions.intersection(permissions)), 10) / 2

    def _extract_domains(self, manifest: dict) -> list:
        """Extract third-party domains from manifest"""
        domains = set()
        patterns = [
            *manifest.get('content_scripts', []),
            manifest.get('externally_connectable', {}).get('matches', []),
            manifest.get('web_accessible_resources', [])
        ]

        for pattern in patterns:
            if isinstance(pattern, dict):
                for match in pattern.get('matches', []):
                    domain = re.findall(r'https?://([^/]+)', match)
                    if domain:
                        domains.update(domain)
            elif isinstance(pattern, str):
                domain = re.findall(r'https?://([^/]+)', pattern)
                if domain:
                    domains.update(domain)

        return list(domains)[:3]

    async def _analyze_crx(self, crx_file: io.BytesIO) -> Dict[str, Any]:
        """Analyze the CRX file and return the results"""
        try:
            with zipfile.ZipFile(crx_file) as zf:
                manifest_data = zf.read('manifest.json').decode('utf-8')
                manifest = json.loads(manifest_data)

            return {
                "permissions": list(set(manifest.get('permissions', []) + manifest.get('optional_permissions', []))),
                "permissions_score": self._calculate_risk_score(manifest),
                "third_party_dependencies": self._extract_domains(manifest),
                "manifest_content": manifest
            }
        except Exception as e:
            logger.error(f"CRX analysis failed: {str(e)}")
            return {
                "permissions": [],
                "permissions_score": 0.0,
                "third_party_dependencies": [],
                "manifest_content": None
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
                    json.dumps(result["analysis_results"]["manifest_content"]),
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
            crx_file = await self._download_crx()
            analysis_results = await self._analyze_crx(crx_file)
            
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
            
            # Cache results
            await self._cache_results(result)
            
            return result
            
        except HTTPException as e:
            logger.error(f"Analysis failed: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

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
    return await analyzer.analyze_extension()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
