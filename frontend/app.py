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
    st.markdown("""
        <div style='background-color: rgba(255, 255, 255, 0.1); 
                    padding: 20px; 
                    border-radius: 10px; 
                    margin-bottom: 20px;'>
    """, unsafe_allow_html=True)
    
    ext_details = result['extension_details']
    
    st.markdown(f"### {ext_details.get('name', 'Unknown Extension')}")
    st.markdown(f"*by {ext_details.get('developer', 'Unknown Developer')}*")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.markdown("**Version**")
        st.markdown(f"`{ext_details.get('version', 'N/A')}`")
    
    with col2:
        st.markdown("**Rating**")
        stars = ext_details.get('stars', 0.0)
        st.markdown(f"{'⭐' * int(stars)} ({stars:.1f})")
    
    with col3:
        st.markdown("**Reviews**")
        st.markdown(f"{ext_details.get('total_reviews', 0):,}")
    
    with col4:
        st.markdown("**Last Updated**")
        st.markdown(f"{ext_details.get('last_updated', 'N/A')}")

def display_security_analysis(result: Dict[str, Any]):
    st.markdown("## 🛡️ Security Analysis")
    
    risk_score = result['analysis_results'].get('permissions_score', 0)
    risk_color = get_risk_color(risk_score)
    
    st.markdown(f"""
        <div style='background-color: rgba(255, 255, 255, 0.1); 
                    padding: 20px; 
                    border-radius: 10px; 
                    margin-bottom: 20px;'>
            <h3 style='color: {risk_color}'>Risk Score: {risk_score}/5.0</h3>
        </div>
    """, unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 🔑 Required Permissions")
        permissions = result['analysis_results'].get('permissions', [])
        if permissions:
            for perm in permissions:
                st.markdown(f"""
                    <div style='background-color: rgba(255, 255, 255, 0.05); 
                              padding: 10px; 
                              border-radius: 5px; 
                              margin-bottom: 5px;'>
                        🔐 {perm}
                    </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No special permissions required")
    
    with col2:
        st.markdown("### 🌐 Third-Party Dependencies")
        deps = result['analysis_results'].get('third_party_dependencies', [])
        if deps:
            for dep in deps:
                st.markdown(f"""
                    <div style='background-color: rgba(255, 255, 255, 0.05); 
                              padding: 10px; 
                              border-radius: 5px; 
                              margin-bottom: 5px;'>
                        🔗 {dep}
                    </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No third-party domains detected")
    
    with st.expander("📜 View Manifest", expanded=False):
        if manifest := result['analysis_results'].get('manifest'):
            st.json(manifest)
        else:
            st.info("No manifest content available")

def display_ai_summary(summary: str):
    st.markdown("""
        <div style='background-color: rgba(0, 100, 255, 0.1); 
                    padding: 20px; 
                    border-radius: 10px; 
                    margin-top: 20px;'>
            <h3>🤖 AI Security Analysis</h3>
    """, unsafe_allow_html=True)
    
    st.markdown(f"""
        <div style='background-color: rgba(255, 255, 255, 0.05); 
                    padding: 15px; 
                    border-radius: 5px;'>
            {summary}
        </div>
    """, unsafe_allow_html=True)
    
    st.markdown("</div>", unsafe_allow_html=True)

def main():
    st.set_page_config(
        page_title="BrowserExt Lookup",
        page_icon="🔍",
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    
    # Modern UI styling
    st.markdown("""
        <style>
            .stApp {
                background-color: #2C3E50;  /* Changed to metallic gray */
                color: #FFFFFF;
            }
            /* Input fields styling */
            .stTextInput>div>div>input {
                background-color: rgba(0, 255, 0, 0.1) !important;
                color: black !important;
                border-radius: 10px;
                border: 1px solid rgba(0, 255, 0, 0.2) !important;
            }
            .stTextInput>div>div>input::placeholder {
                color: rgba(255, 255, 255, 0.5) !important;
            }
            .stTextInput>div>div>input:focus {
                border-color: rgba(0, 255, 0, 0.3) !important;
                box-shadow: 0 0 0 1px rgba(0, 255, 0, 0.2) !important;
            }
            /* Store selector styling */
            .stSelectbox>div>div>select {
                background-color: rgba(0, 255, 0, 0.1) !important;
                color: white !important;
                border-radius: 10px;
                border: 1px solid rgba(0, 255, 0, 0.2) !important;
            }
            /* Analysis button styling */
            .stButton>button {
                background-color: #2196F3 !important;
                color: black !important;
                border-radius: 20px;
                padding: 10px 25px;
                border: none !important;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                transition: all 0.3s ease;
            }
            .stButton>button:hover {
                transform: translateY(-2px);
                box-shadow: 0 6px 8px rgba(0, 0, 0, 0.2);
            }
            .stJson {
                background-color: rgba(255, 255, 255, 0.05) !important;
                border-radius: 10px;
                padding: 10px;
            }
            .stExpander {
                background-color: rgba(255, 255, 255, 0.05);
                border-radius: 10px;
            }
            @keyframes glow {
                from { text-shadow: 0 0 10px #2196F3; }
                to { text-shadow: 0 0 20px #2196F3; }
            }
            /* Extension ID label styling */
            .stTextInput>label {
                color: white !important;  /* Changed to white */
            }
        </style>
    """, unsafe_allow_html=True)
    
    st.markdown("""
        <h1 style='text-align: center; 
                   color: #2196F3; 
                   margin-bottom: 30px;
                   animation: glow 2s ease-in-out infinite alternate;'>
            🔍 BrowserExt Lookup
        </h1>
    """, unsafe_allow_html=True)
    
    st.markdown("""
        <p style='text-align: center; 
                  color: #BBBBBB; 
                  margin-bottom: 30px;'>
            Analyze browser extensions for security risks and permissions using AI-powered insights
        </p>
    """, unsafe_allow_html=True)
    
    if 'api_client' not in st.session_state:
        st.session_state.api_client = APIClient()
    
    with st.form("extension_analysis_form"):
        col1, col2 = st.columns([3, 1])
        
        with col1:
            extension_id = st.text_input(
                "Extension ID",
                placeholder="Enter 32-character extension ID..."
            ).strip()
        
        with col2:
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
        
        with st.spinner("🔍 Analyzing extension security..."):
            try:
                payload = {
                    "extension_id": extension_id.lower(),
                    "store_name": store_name.lower()
                }
                
                logger.info(f"Frontend request payload: {payload}")
                result = st.session_state.api_client.analyze_extension(payload)
                logger.info(f"Frontend response: {result}")
                
                st.success("✅ Analysis complete!")
                
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
