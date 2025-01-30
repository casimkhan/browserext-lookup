import streamlit as st
import requests
import re
import logging
from typing import Dict, Any
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
BACKEND_URL = "https://browserext-lookup.onrender.com"
REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3

class APIClient:
    def __init__(self):
        self.session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504]
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
    
    def analyze_extension(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "BrowserExtLookup/1.0"
        }
        
        try:
            response = self.session.post(
                f"{BACKEND_URL}/analyze",
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise Exception("Request timed out. Please try again.")
        except requests.exceptions.HTTPError as e:
            error_mapping = {
                400: "Invalid request: Please check the Extension ID.",
                404: "Extension not found. Please verify the ID and store selection.",
                429: "Too many requests. Please try again later.",
                500: "Server error: Please try again later."
            }
            status_code = e.response.status_code
            raise Exception(error_mapping.get(status_code, f"Error: {str(e)}"))
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            raise

def is_valid_extension_id(extension_id: str) -> bool:
    """Validate extension ID format with improved pattern matching."""
    return bool(re.fullmatch(r"[a-z]{32}", extension_id.lower()))

def display_extension_details(result: Dict[str, Any]):
    """Display extension details in a structured format."""
    with st.container():
        st.subheader("🛠️ Extension Details")
        col1, col2 = st.columns(2)
        
        with col1:
            st.metric("Name", result['extension_details'].get('name', 'N/A'))
            st.metric("Version", result['extension_details'].get('version', 'N/A'))
            st.metric("Total Reviews", result['extension_details'].get('total_reviews', 'N/A'))
        
        with col2:
            st.metric("Stars", f"{result['extension_details'].get('stars', 'N/A')} ⭐")
            st.metric("Permissions Score", result['analysis_results'].get('permissions_score', 'N/A'))

def display_security_analysis(result: Dict[str, Any]):
    """Display security analysis results."""
    with st.expander("🔍 Security Analysis", expanded=True):
        st.write("**Detected Scripts:**")
        if scripts := result['analysis_results'].get('scripts', []):
            st.json(scripts)
        else:
            st.info("No scripts detected")
            
        st.write("**Third-Party Dependencies:**")
        if deps := result['analysis_results'].get('third_party_dependencies', []):
            st.json(deps)
        else:
            st.info("No third-party dependencies detected")

def main():
    st.set_page_config(
        page_title="BrowserExt Lookup",
        page_icon="🔍",
        layout="wide"
    )
    
    st.title("🔍 BrowserExt Lookup")
    st.write("Analyze browser extensions using their ID.")
    
    # Session state initialization
    if 'api_client' not in st.session_state:
        st.session_state.api_client = APIClient()
    
    # Input form
    with st.form("extension_analysis_form"):
        extension_id = st.text_input("Enter Extension ID:").strip()
        store_name = st.selectbox("Select Store:", ["Chrome", "Edge"])
        submitted = st.form_submit_button("Analyze")
        
    if submitted:
        if not extension_id:
            st.error("⚠️ Please enter an Extension ID.")
            return
            
        if not is_valid_extension_id(extension_id):
            st.error("⚠️ Invalid Extension ID. It should be a 32-character lowercase alphanumeric string.")
            return
            
        with st.spinner("Analyzing extension..."):
            try:
                payload = {
                    "extension_id": extension_id.lower(),
                    "store_name": store_name.lower()
                }
                
                # Log the request payload
                logger.info(f"Frontend request payload: {payload}")
                
                result = st.session_state.api_client.analyze_extension(payload)
                
                # Log the response
                logger.info(f"Frontend response: {result}")
                
                st.success("✅ Analysis complete!")
                # Check if extension_details is present
                if result.get("extension_details"):
                    display_extension_details(result)
                    display_security_analysis(result)
                else:
                    st.error("⚠️ Extension details not found in the response.")
                #Display AI Generated Summary
                st.subheader("📢 Security Summary")
                st.info(result.get("summary", "No summary provided."))
                
            except Exception as e:
                st.error(f"⚠️ {str(e)}")
                logger.exception("Analysis failed")

if __name__ == "__main__":
    main()
