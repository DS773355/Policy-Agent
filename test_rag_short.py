import requests

url = "http://localhost:8080/v1/chat/completions"

system_prompt = (
    "You are an expert policy assistant. "
    "Answer the user's question using ONLY the policy excerpts provided below. "
    "Your response MUST follow this exact structure:\n"
    "1. Explanation: A clear, direct explanation answering the user's query.\n"
    "2. Source Details: Specify in detail exactly WHERE (document title, page, paragraph/section) "
    "and WHY you retrieved and used this information. Under this section, you MUST quote the relevant "
    "paragraph(s) or sentences directly from the excerpts so the user can see the exact text used.\n\n"
    "If the provided excerpts do not contain enough information to answer fully, say so — "
    "do NOT invent or assume any policy rules."
)

excerpt = (
    "The authorised representatives, who intend to attend the tender opening, "
    "shall be required to bring with them letters of authority from the tenderers concerned. "
    "The tender opening official/ committee shall announce the salient features of the tenders "
    "like description and specification of the goods/services, quoted price, terms of delivery, "
    "delivery period, discount, if any, whether EMD furnished or not and any other special "
    "feature of the tender for the information of the representatives."
)

user_prompt = f"""Policy Excerpts:
--- Policy Excerpt 1 ---
Source: DPM-2025 (Page 12, Section 5.3)
{excerpt}

Question: what is tender
Answer:"""

payload = {
    "model": "phi3",
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    "temperature": 0.0,
    "max_tokens": 500
}

try:
    print("Sending request with short context...")
    response = requests.post(url, json=payload)
    print("Status:", response.status_code)
    if response.status_code == 200:
        print("Response:")
        print(response.json()['choices'][0]['message']['content'])
    else:
        print("Error response:", response.text)
except Exception as e:
    print("Error:", e)
