import streamlit as st
import requests
import re

# Backend API URL
BACKEND_URL = "https://browserext-lookup.onrender.com"

# Function to validate extension ID format
def is_valid_extension_id(extension_id):
    return bool(re.fullmatch(r"[a-z]{32}", extension_id))

def main():
    st.title("BrowserExt Lookup")
    st.write("Analyze browser extensions using their ID or CRX file.")

    # Input fields
    extension_id = st.text_input("Enter Extension ID:")
    store_name = st.selectbox("Select Store:", ["Chrome", "Edge"])
    crx_file = st.file_uploader("Upload CRX File (Optional)", type=["crx"])

    if st.button("Analyze"):
        if not extension_id and not crx_file:
            st.error("Please enter an Extension ID or upload a CRX file.")
            return
        
        if extension_id and not is_valid_extension_id(extension_id):
            st.error("Invalid Extension ID. It should be a 32-character lowercase alphanumeric string.")
            return

        # Prepare payload
        payload = {}
        if extension_id:
            payload["extension_id"] = extension_id.strip()
            payload["store_name"] = store_name.lower().strip()
        if crx_file:
            payload["crx_filename"] = crx_file.name
        
        headers = {"Content-Type": "application/json"}

        try:
            # Make the POST request to the backend
            response = requests.post(f"{BACKEND_URL}/analyze", json=payload, headers=headers)
            if response.status_code == 400:
                st.error("Bad request: Please check the provided extension ID or CRX file.")
                return
            elif response.status_code == 404:
                st.error("Extension not found. Please check the Extension ID or store selection.")
                return
            elif response.status_code >= 500:
                st.error("Server error: Please try again later.")
                return
            
            response.raise_for_status()
            result = response.json()

            st.success("Analysis complete!")

            # Display extension details
            st.write("**Extension Details:**")
            st.write("**Name:**", result["extension_details"].get("name", "N/A"))
            st.write("**Description:**", result["extension_details"].get("description", "N/A"))
            st.write("**Version:**", result["extension_details"].get("version", "N/A"))
            st.write("**Total Reviews:**", result["extension_details"].get("total_reviews", "N/A"))
            st.write("**Stars:**", result["extension_details"].get("stars", "N/A"))

            # Display analysis results
            st.write("**Analysis Results:**")
            st.write("**Permissions Score:**", result["analysis_results"].get("permissions_score", "N/A"))
            st.write("**Scripts:**")
            st.json(result["analysis_results"].get("scripts", []))
            st.write("**Third-Party Dependencies:**")
            st.json(result["analysis_results"].get("third_party_dependencies", []))

            # Display summary
            st.write("**DeepSeek Summary:**", result.get("summary", "No summary provided."))

        except requests.exceptions.RequestException as e:
            st.error(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
