import streamlit as st
import requests

# Backend API URL (replace with your Render backend URL)
BACKEND_URL = "https://your-render-backend-url.onrender.com"

# Streamlit app
def main():
    st.title("BrowserExt Lookup")
    st.write("Upload a CRX file to analyze its risk and get a summary.")
    
    # Input fields
    extension_id = st.text_input("Enter Extension ID:")
    uploaded_file = st.file_uploader("Upload CRX File", type=["crx"])
    
    if st.button("Analyze"):
        if not extension_id or not uploaded_file:
            st.error("Please enter an Extension ID and upload a CRX file.")
        else:
            # Send file to backend
            files = {"file": uploaded_file.getvalue()}
            response = requests.post(f"{BACKEND_URL}/analyze", params={"extension_id": extension_id}, files=files)
            
            if response.status_code == 200:
                result = response.json()
                st.success("Analysis complete!")
                st.json(result["analysis_results"])
                st.write("**Summary:**", result["summary"])
            else:
                st.error("An error occurred during analysis.")

if __name__ == "__main__":
    main()
