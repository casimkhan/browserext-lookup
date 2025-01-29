import streamlit as st
import requests
import re

# Backend API URL
BACKEND_URL = "https://browserext-lookup.onrender.com"

# Function to validate extension ID format
def is_valid_extension_id(extension_id):
    return bool(re.fullmatch(r"[a-z]{32}", extension_id))

def main():
    st.title("🔍 BrowserExt Lookup")
    st.write("Analyze browser extensions using their ID.")

    # Input fields
    extension_id = st.text_input("Enter Extension ID:")
    store_name = st.selectbox("Select Store:", ["Chrome", "Edge"])

    if st.button("Analyze"):
        if not extension_id:
            st.error("⚠️ Please enter an Extension ID.")
            return
        
        if not is_valid_extension_id(extension_id):
            st.error("⚠️ Invalid Extension ID. It should be a 32-character lowercase alphanumeric string.")
            return

        # Prepare payload
        payload = {
            "extension_id": extension_id.strip(),
            "store_name": store_name.lower().strip()
        }
        
        headers = {"Content-Type": "application/json"}

        try:
            # Make the POST request to the backend
            response = requests.post(f"{BACKEND_URL}/analyze", json=payload, headers=headers)
            
            if response.status_code == 400:
                st.error("❌ Bad request: Check the provided Extension ID.")
                return
            elif response.status_code == 404:
                st.error("❌ Extension not found. Verify the Extension ID or store selection.")
                return
            elif response.status_code >= 500:
                st.error("❌ Server error: Please try again later.")
                return

            response.raise_for_status()
            result = response.json()

            st.success("✅ Analysis complete!")

            # Display extension details
            st.subheader("🛠️ Extension Details")
            st.write(f"**Name:** {result['extension_details'].get('name', 'N/A')}")
            st.write(f"**Description:** {result['extension_details'].get('description', 'N/A')}")
            st.write(f"**Version:** {result['extension_details'].get('version', 'N/A')}")
            st.write(f"**Total Reviews:** {result['extension_details'].get('total_reviews', 'N/A')}")
            st.write(f"**Stars:** {result['extension_details'].get('stars', 'N/A')} ⭐")

            # Display security analysis
            st.subheader("🔍 Security Analysis")
            st.write(f"**Permissions Score:** {result['analysis_results'].get('permissions_score', 'N/A')}")
            st.write("**Detected Scripts:**")
            st.json(result['analysis_results'].get('scripts', []))
            st.write("**Third-Party Dependencies:**")
            st.json(result['analysis_results'].get('third_party_dependencies', []))

            # Display summary
            st.subheader("📢 Security Summary")
            st.write(result.get("summary", "No summary provided."))

        except requests.exceptions.RequestException as e:
            st.error(f"⚠️ An error occurred: {e}")

if __name__ == "__main__":
    main()
