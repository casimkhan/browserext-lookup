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
import hashlib

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
CHROME_VERSION = "120.0.0.0"  # Match the user agent version
NACL_ARCH = "x86-64"  # Determine based on your target architecture

class DatabaseManager:
    def __init__(self):
        self.db_path = "/var/lib/sqlite/crx_analysis.db"

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
            # Create table with a full JSON blob column to cache the complete analysis result.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS extensions (
                    id TEXT,
                    store_name TEXT,
                    result_blob TEXT,
                    last_updated TIMESTAMP,
                    PRIMARY KEY (id, store_name)
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
        """Retrieve cached full analysis result from the database if available."""
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "SELECT result_blob FROM extensions WHERE id = ? AND store_name = ?",
                (self.extension_id, self.store_name)
            )
            row = cursor.fetchone()
            if row and row["result_blob"]:
                logger.info("Returning cached analysis result")
                return json.loads(row["result_blob"])
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
        details['name'] = self._extract_text(soup, 'h1', class_=['Pa2dE', 'c011070', 'c011075', 'c011080', 'c011085']) or 'N/A'
        details['description'] = self._extract_text(soup, 'div', class_=['JJ3H1e', 'jVwmLb', 'c011136']) or 'N/A'
        details['version'] = self._extract_text(soup, 'div', class_=['N3EXSc', 'c011070', 'c011077', 'c011069']) or 'N/A'
        details['total_reviews'] = self._extract_number(soup, 'span', class_=['PmmSTd', 'xJEoWe', 'c011089', 'c011502']) or 0
        details['stars'] = self._extract_rating(soup, 'span', class_=['Vq0ZA', 'c011088', 'c011685']) or 0.0
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

            # Get AI summary from OpenAI based on crawled data and manifest
            ai_summary = await self._get_openai_summary({
                "store_details": store_details,
                "analysis_results": analysis_results
            })

            # Combine results into one response object
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

            # Cache the full result as a JSON blob in the result_blob column
            await self._cache_results(result)

            # Cleanup downloaded file
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
        """Download the CRX file with proper parameters and return path, size, hash"""
        if self.store_name == "chrome":
            url = (
                f"https://clients2.google.com/service/update2/crx?"
                f"response=redirect&"
                f"prodversion={CHROME_VERSION}&"
                f"x=id%3D{self.extension_id}%26installsource%3Dondemand%26uc&"
                f"nacl_arch={NACL_ARCH}&"
                f"acceptformat=crx2,crx3"
            )
        else:  # Edge
            url = (
                f"https://edge.microsoft.com/extensionwebstorebase/v1/crx?"
                f"response=redirect&"
                f"prod=chromiumcrx&"
                f"prodchannel=&"
                f"x=id%3D{self.extension_id}%26installsource%3Dondemand%26uc"
            )

        try:
            response = requests.get(url, stream=True, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()

            # Save directly as ZIP after processing CRX headers
            zip_path = f"/tmp/{self.extension_id}.zip"
            crx_data = response.content

            # Process CRX headers to get actual ZIP data
            zip_data = self._process_crx_headers(crx_data)

            with open(zip_path, 'wb') as f:
                f.write(zip_data)

            # Calculate verification metrics
            file_size = len(zip_data)
            file_hash = hashlib.sha256(zip_data).hexdigest()

            logger.info(f"CRX processed to ZIP: {zip_path} ({file_size} bytes)")
            return zip_path, file_size, file_hash

        except requests.RequestException as e:
            logger.error(f"Download failed: {str(e)}")
            raise HTTPException(status_code=500, detail="Failed to download CRX file")

    def _process_crx_headers(self, crx_data: bytes) -> bytes:
        """Process CRX file headers to extract actual ZIP content"""
        try:
            # Check for CRX3 format (magic number 'Cr24')
            if crx_data.startswith(b'Cr24'):
                # CRX3 format parsing
                version = int.from_bytes(crx_data[4:8], byteorder='little')
                header_length = int.from_bytes(crx_data[8:12], byteorder='little')
                zip_start = 12 + header_length + 32  # Skip header and SHA256
                return crx_data[zip_start:]
            else:
                # CRX2 format - skip first 16 bytes
                return crx_data[16:]
        except Exception as e:
            logger.error(f"CRX header processing failed: {str(e)}")
            raise HTTPException(status_code=500, detail="Invalid CRX file format")

    async def _analyze_crx(self, zip_path: str) -> Dict[str, Any]:
        """Analyze the processed ZIP file"""
        analysis_results = {
            "permissions": [],
            "permissions_score": 0.0,
            "third_party_dependencies": [],
            "manifest": None
        }

        try:
            logger.info(f"Opening ZIP file: {zip_path}")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Log all files in the ZIP for debugging
                zip_files = zip_ref.namelist()
                logger.info(f"Files in ZIP: {zip_files}")

                if 'manifest.json' in zip_files:
                    logger.info("Found manifest.json in ZIP file")
                    with zip_ref.open('manifest.json') as manifest_file:
                        manifest_content = manifest_file.read()
                        logger.info(f"Raw manifest content (first 100 bytes): {manifest_content[:100]}")

                        try:
                            # Decode the manifest content if it's in bytes
                            if isinstance(manifest_content, bytes):
                                try:
                                    manifest_content = manifest_content.decode('utf-8')
                                except UnicodeDecodeError:
                                    # Fallback to UTF-16 if UTF-8 fails
                                    manifest_content = manifest_content.decode('utf-16')

                            # Parse the manifest content as JSON
                            manifest_json = json.loads(manifest_content)
                            logger.info(f"Parsed manifest.json: {manifest_json}")
                            analysis_results['manifest'] = manifest_json
                            analysis_results['permissions'] = manifest_json.get('permissions', [])
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse manifest.json: {str(e)}")
                            raise HTTPException(status_code=500, detail="Invalid manifest.json format")
                        except UnicodeDecodeError as e:
                            logger.error(f"Failed to decode manifest.json: {str(e)}")
                            raise HTTPException(status_code=500, detail="Invalid manifest.json encoding")
                else:
                    logger.warning("No manifest.json found in extension")
                    raise HTTPException(status_code=404, detail="manifest.json not found in extension")

            # Calculate security scores
            analysis_results['permissions_score'] = self._calculate_permission_score(
                analysis_results['permissions']
            )

        except zipfile.BadZipFile:
            logger.error("Invalid ZIP archive after CRX processing")
            raise HTTPException(status_code=500, detail="Invalid extension package")
        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}", exc_info=True)
            raise HTTPException(status_code=500, detail="Extension analysis failed")

        return analysis_results

    def _calculate_permission_score(self, permissions):
        score = 0.0
        risky_permissions = [
            'activeTab', 'background', 'bookmarks', 'browsingData', 'clipboardRead',
            'clipboardWrite', 'contentSettings', 'cookies', 'debugger', 'downloads',
            'geolocation', 'history', 'management', 'nativeMessaging', 'notifications',
            'privacy', 'proxy', 'storage', 'tabs', 'unlimitedStorage', 'webNavigation',
            'webRequest', 'webRequestBlocking'
        ]
        for perm in permissions:
            if perm in risky_permissions:
                score += 0.5
        return score

    async def _get_openai_summary(self, data: Dict[str, Any]) -> str:
        """Get AI summary using OpenAI with a focus on security analysis of the manifest."""
        try:
            analysis_text = (
                f"Extension Name: {data['store_details']['name']}\n"
                f"Description: {data['store_details']['description']}\n"
                f"Version: {data['store_details']['version']}\n"
                f"Rating: {data['store_details']['stars']} stars from {data['store_details']['total_reviews']} reviews\n\n"
                f"Security Analysis:\n"
                f"- Permissions required: {', '.join(data['analysis_results']['permissions'])}\n"
                f"- Risk score: {data['analysis_results']['permissions_score']}\n"
                f"- Third-party domains: {', '.join(data['analysis_results']['third_party_dependencies'])}\n\n"
                f"Manifest Details:\n"
                f"{json.dumps(data['analysis_results']['manifest'], indent=2)}"
            )

            prompt = (
                "You are an expert in browser extension security. Analyze the following Chrome/Edge extension's manifest.json and store details for potential security risks and privacy concerns. "
                "Review the following details and provide a security-focused summary:\n\n"
                f"{analysis_text}\n\n"
                "Focus on potential security risks, privacy concerns, and any unusual or dangerous permissions. "
                "Provide a concise security-focused summary highlighting risky permissions, potential data access concerns, and overall trustworthiness."
            )

            response = client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[
                    {"role": "system", "content": "You are a security analyst specializing in browser extensions."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.5
            )

            summary = response.choices[0].message.content
            return summary if summary else "No security summary available."

        except Exception as e:
            logger.error(f"OpenAI API call failed: {str(e)}")
            return "Failed to generate security summary."

    async def _cache_results(self, result: Dict[str, Any]):
        """Cache the full analysis result as a JSON blob in the database."""
        result_blob = json.dumps(result)
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO extensions (id, store_name, result_blob, last_updated)
                VALUES (?, ?, ?, ?)
                """,
                (
                    self.extension_id,
                    self.store_name,
                    result_blob,
                    result["metadata"]["analyzed_at"]
                )
            )
            conn.commit()

@app.post("/analyze")
async def analyze_extension(body: dict = Body(...)):
    """
    Analyze a browser extension with AI-powered summary.
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
