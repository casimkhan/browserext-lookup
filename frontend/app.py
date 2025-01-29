import streamlit as st
import requests

# Backend API URL
BACKEND_URL = "https://browserext-lookup.onrender.com"

def main():
    st.title("BrowserExt Lookup")
    st.write("Analyze browser extensions using their ID and CRX file.")

    # Input fields
    extension_id = st.text_input("Enter Extension ID:")
    store_name = st.selectbox("Select Store:", ["Chrome", "Edge"])

    if st.button("Analyze"):
        if not extension_id or not store_name:
            st.error("Please enter an Extension ID and select a store.")
        else:
            # ✅ Ensure correct JSON format
            payload = {
                "extension_id": extension_id.strip(),  # Remove whitespace
                "store_name": store_name.lower().strip()  # Convert to lowercase (chrome, edge)
            }

            headers = {"Content-Type": "application/json"}  # ✅ Explicitly set JSON header

            try:
                # Make the POST request to the backend
                response = requests.post(f"{BACKEND_URL}/analyze", json=payload, headers=headers)
                response.raise_for_status()  # Raise an exception for 4xx/5xx responses

                result = response.json()  # Parse the JSON response
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
                st.write("**Permissions Score:**", result["analysis_results"]["permissions_score"])
                st.write("**Scripts:**")
                st.json(result["analysis_results"]["scripts"])
                st.write("**Third-Party Dependencies:**")
                st.json(result["analysis_results"]["third_party_dependencies"])

                # Display summary
                st.write("**DeepSeek Summary:**", result.get("summary", "No summary provided."))

            except requests.exceptions.RequestException as e:
                st.error(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
