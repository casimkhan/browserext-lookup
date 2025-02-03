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
import re
import zipfile
import io
import hashlib  # For calculating file hash

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
        """Fetch extension details from store using web crawling"""
        store_url = (
            f"https://chrome.google.com/webstore/detail/{self.extension_id}" 
            if self.store_name == "chrome" 
            else f"https://microsoftedge.microsoft.com/addons/detail/{self.extension_id}"
        )

        try:
            response = requests.get(store_url, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            html_content = response.text
            return self._crawl_html_details(html_content)
        except Exception as e:
            logger.error(f"Failed to fetch store details: {str(e)}")
            raise HTTPException(status_code=404, detail="Extension not found in store")

    def _crawl_html_details(self, html_content: str) -> Dict[str, Any]:
        """Crawl the HTML to extract specific details."""
        soup = BeautifulSoup(html_content, 'html.parser')
        details = {}

        # Extract details using class names or patterns
        details['name'] = self._extract_text(soup, 'h1', class_='Pa2dE') or 'N/A'
        details['description'] = self._extract_text(soup, 'div', class_='JJ3H1e jVwmLb') or 'N/A'
        details['version'] = self._extract_text(soup, 'div', class_='v7vKf') or 'N/A'
        details['total_reviews'] = self._extract_number(soup, 'div', class_='p9xg1 Yemige') or 0
        details['stars'] = self._extract_rating(soup, 'div', class_='PmmSTd') or 0.0
        details['last_updated'] = self._extract_text(soup, 'div', class_='h-CkGe') or 'N/A'  # Example for Chrome
        details['developer'] = self._extract_text(soup, 'span', class_='e-f-ih') or 'N/A'  # Example for Chrome
        details['size'] = 'N/A'  # Size might not be directly available
        details['category'] = self._extract_text(soup, 'div', class_='Cj b') or 'N/A'  # Example for Chrome

        return details

    def _extract_text(self, soup, tag, **kwargs):
        element = soup.find(tag, **kwargs)
        return element.text.strip() if element else None

    def _extract_number(self, soup, tag, **kwargs):
        text = self._extract_text(soup, tag, **kwargs)
        if text:
            match = re.search(r'\d+', text)
            return int(match.group()) if match else 0
        return 0

    def _extract_rating(self, soup, tag, **kwargs):
        text = self._extract_text(soup, tag, **kwargs)
        if text:
            match = re.search(r'(\d+(\.\d+)?)', text)
            return float(match.group()) if match else 0.0
        return 0.0

    async def analyze_extension(self) -> Dict[str, Any]:
        """Complete extension analysis workflow"""
        try:
            # Check cache first
            cached = await self._get_cached_analysis()
            if cached:
                return cached

            # Fetch store details using web crawler
            store_details = await self.fetch_store_details()

            # Download and analyze CRX
            zip_path, file_size, file_hash = await self._download_crx()
            analysis_results = await self._analyze_crx(zip_path)

            # Get AI summary from OpenAI based on crawled data
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
                    "store": self.store_name,
                    "file_size": file_size,  # Include file size in the response
                    "file_hash": file_hash   # Include file hash in the response
                }
            }

            # Log the response
            logger.info(f"Backend response: {result}")

            # Cache results
            await self._cache_results(result)

            # Cleanup
            if os.path.exists(zip_path):
                os.remove(zip_path)

            return result

        except HTTPException as e:
            logger.error(f"Analysis failed: {str(e)}")
            return {
                "extension_details": None,
                "analysis_results": None,
                "summary": f"Error: {str(e)}",
                "metadata": {
                    "analyzed_at": datetime.utcnow().isoformat(),
                    "store": self.store_name,
                    "file_size": None,
                    "file_hash": None
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
                    "store": self.store_name,
                    "file_size": None,
                    "file_hash": None
                }
            }

    async def _download_crx(self) -> tuple[str, int, str]:
        """Download the CRX file, rename to ZIP, and return the local path, size, and hash"""
        crx_url = (
            f"https://clients2.google.com/service/update2/crx?response=redirect&prodversion=1&acceptformat=crx2,crx3&x=id={self.extension_id}&uc"
            if self.store_name == "chrome"
            else f"https://edge.microsoft.com/extensionwebstorebase/v1/crx?id={self.extension_id}"
        )
        
        try:
            response = requests.get(crx_url, stream=True, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            crx_path = f"/tmp/{self.extension_id}.crx"
            with open(crx_path, 'wb') as out_file:
                for chunk in response.iter_content(chunk_size=8192):
                    out_file.write(chunk)
            
            # Calculate file size and hash
            file_size = os.path.getsize(crx_path)
            file_hash = self._calculate_file_hash(crx_path)

            # Rename .crx to .zip
            zip_path = crx_path.replace('.crx', '.zip')
            os.rename(crx_path, zip_path)
            logger.info(f"CRX file downloaded and renamed to {zip_path}")
            return zip_path, file_size, file_hash
        except requests.RequestException as e:
            logger.error(f"Failed to download CRX file: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to download CRX file")

    def _calculate_file_hash(self, file_path: str) -> str:
        """Calculate SHA-256 hash of a file"""
        sha256_hash = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    async def _analyze_crx(self, zip_path: str) -> Dict[str, Any]:
        """Analyze the ZIP file (formerly CRX) and return the results including manifest.json content"""
        analysis_results = {
            "permissions": [],
            "permissions_score": 0.0,
            "third_party_dependencies": [],
            "manifest": None
        }

        try:
            # Open the renamed .zip file and extract its contents
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                if 'manifest.json' in zip_ref.namelist():
                    with zip_ref.open('manifest.json') as manifest_file:
                        manifest_content = manifest_file.read()
                        try:
                            analysis_results['manifest'] = json.loads(manifest_content)
                            if 'permissions' in analysis_results['manifest']:
                                analysis_results['permissions'] = analysis_results['manifest']['permissions']
                        except json.JSONDecodeError:
                            logger.warning("Failed to parse manifest.json")
                else:
                    logger.warning("No manifest.json found in CRX file")

            # Calculate security scores
            analysis_results['permissions_score'] = self._calculate_permission_score(analysis_results['permissions'])
            
        except zipfile.BadZipFile:
            logger.error("The file is not a valid ZIP archive")
        except Exception as e:
            logger.error(f"Error analyzing CRX file: {str(e)}")

        return analysis_results

    def _calculate_permission_score(self, permissions):
        score = 0.0
        risky_permissions = ['activeTab', 'background', 'bookmarks', 'browsingData', 'clipboardRead', 'clipboardWrite', 'contentSettings', 'cookies', 'debugger', 'downloads', 'geolocation', 'history', 'management', 'nativeMessaging', 'notifications', 'privacy', 'proxy', 'storage', 'tabs', 'unlimitedStorage', 'webNavigation', 'webRequest', 'webRequestBlocking']
        for perm in permissions:
            if perm in risky_permissions:
                score += 0.5
        return score

    async def _get_openai_summary(self, data: Dict[str, Any]) -> str:
        """Get AI summary using OpenAI"""
        try:
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

            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that summarizes browser extension details and security analysis."},
                    {"role": "user", "content": f"Summarize the following browser extension details and security analysis:\n\n{analysis_text}"}
                ],
                max_tokens=300,
                temperature=0.7
            )

            summary = response.choices[0].message.content
            return summary if summary else "No summary available."

        except Exception as e:
            logger.error(f"OpenAI API call failed: {str(e)}")
            return "Failed to generate AI summary."

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
                    json.dumps(result["analysis_results"]["manifest"]) if result["analysis_results"]["manifest"] else "N/A",
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
