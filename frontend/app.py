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
REQUEST_TIMEOUT = 30
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
    return bool(re.fullmatch(r"[a-z]{32}", extension_id.lower()))

def get_risk_color(score: float) -> str:
    if score <= 2:
        return "green"
    elif score <= 3.5:
        return "orange"
    return "red"

def display_extension_details(result: Dict[str, Any]):
    ext_details = result['extension_details']
    
    st.markdown(f"""
        

            
{ext_details.get('name', 'Unknown Extension')}

            
*by {ext_details.get('developer', 'Unknown Developer')}*


            
Version: {ext_details.get('version', 'N/A')}
Rating: {'⭐' * int(ext_details.get('stars', 0.0))} ({ext_details.get('stars', 0.0):.1f})
Reviews: {ext_details.get('total_reviews', 0):,}
Last Updated: {ext_details.get('last_updated', 'N/A')}

        

    """, unsafe_allow_html=True)

def display_security_analysis(result: Dict[str, Any]):
    st.markdown("""
    

        
🛡️ Security Analysis

    

    """, unsafe_allow_html=True)
    
    risk_score = result['analysis_results'].get('permissions_score', 0)
    risk_color = get_risk_color(risk_score)
    
    st.markdown(f"""
        

            
Risk Score: {risk_score}/5.0

        

    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        permissions_content = ''.join([f'
{perm}
' for perm in result['analysis_results'].get('permissions', [])])
        st.markdown(f"""
            

                
🔑 Required Permissions

                {permissions_content if permissions_content else '
No special permissions required.

'}
            

        """, unsafe_allow_html=True)
    
    with col2:
        dependencies_content = ''.join([f'
{dep}
' for dep in result['analysis_results'].get('third_party_dependencies', [])])
        st.markdown(f"""
            

                
🌐 Third-Party Dependencies

                {dependencies_content if dependencies_content else '
No third-party domains detected.

'}
            

        """, unsafe_allow_html=True)
    
    manifest = result['analysis_results'].get('manifest', 'No manifest content available')
    st.markdown(f"""
    
📜 View Manifest

    """, unsafe_allow_html=True)

def display_ai_summary(summary: str):
    st.markdown(f"""
        

            
🤖 AI Security Analysis

            
{summary}


        

    """, unsafe_allow_html=True)

def main():
    st.set_page_config(
        page_title="BrowserExt Lookup - Futuristic Analysis",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="collapsed"
    )

    # Enhanced UI styling with futuristic theme
    st.markdown("""
        
    """, unsafe_allow_html=True)

    st.markdown("""
        
🔍 BrowserExt Lookup

        
Analyze browser extensions for security risks and permissions using AI-powered insights


    """, unsafe_allow_html=True)

    if 'api_client' not in st.session_state:
        st.session_state.api_client = APIClient()

    with st.form("extension_analysis_form", clear_on_submit=False):
        cols = st.columns([3, 1])
        
        with cols[0]:
            extension_id = st.text_input(
                "Extension ID",
                placeholder="Enter 32-character extension ID...",
                help="Must be a 32-character lowercase alphanumeric string"
            ).strip()
        
        with cols[1]:
            store_name = st.selectbox(
                "Store",
                ["Chrome", "Edge"],
                index=0
            )
        
        submitted = st.form_submit_button("🔍 Analyze Extension")

    if submitted:
        if not extension_id:
            st.error("⚠️ Please enter an Extension ID")
            return
        
        if not is_valid_extension_id(extension_id):
            st.error("⚠️ Invalid Extension ID format. Please enter a 32-character lowercase alphanumeric string.")
            return
        
        with st.spinner("🔍 Scanning the digital cosmos for extension data..."):
            try:
                payload = {
                    "extension_id": extension_id.lower(),
                    "store_name": store_name.lower()
                }
                
                logger.info(f"Frontend request payload: {payload}")
                result = st.session_state.api_client.analyze_extension(payload)
                logger.info(f"Frontend response: {result}")
                
                st.success("✅ Analysis complete! Unveiling the results...")
                
                if result.get("extension_details"):
                    display_extension_details(result)
                    display_security_analysis(result)
                    display_ai_summary(result.get("summary", "No AI analysis available."))
                else:
                    st.error("⚠️ Extension details not found in the response.")
                
            except Exception as e:
                st.error(f"⚠️ {str(e)}")
                logger.exception("Analysis failed")

if __name__ == "__main__":
    main()
