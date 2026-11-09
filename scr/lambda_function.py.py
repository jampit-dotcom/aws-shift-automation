import json
import os
import io
import datetime
from pathlib import Path
import pandas as pd
import boto3
from openai import OpenAI

# ---------------------------------------------------------------------------
# Globals & AWS Clients
# ---------------------------------------------------------------------------
s3_client = boto3.client('s3')
WEB_BUCKET_NAME = os.getenv("WEB_BUCKET_NAME", "amzn-s3-mock-web-host")

def get_openai_client(secret_name="ShiftAutomation/OpenAIKey"):
    """
    Fetches the OpenAI API key from Secrets Manager and returns an initialized OpenAI client.
    Handles both JSON key-value secrets and raw plaintext secrets.
    """
    secrets_client = boto3.client('secretsmanager')
    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        secret_string = response.get('SecretString', '')

        # Attempt to parse as JSON first (if stored as key/value pair)
        try:
            secret_dict = json.loads(secret_string)
            api_key = secret_dict.get("OPENAI_API_KEY", secret_dict.get("api_key", secret_string))
        except json.JSONDecodeError:
            # Stored as raw plaintext
            api_key = secret_string

        return OpenAI(api_key=api_key)

    except Exception as e:
        print(f"[!] Critical error initializing OpenAI client from Secrets Manager '{secret_name}': {e}")
        raise e

# ---------------------------------------------------------------------------
# AI Summary Function
# ---------------------------------------------------------------------------
def summarize_ticket_with_ai(client, ticket_title, ticket_description):
    prompt = f"""
    You are an expert Cyber Threat Intelligence Analyst helping summarize shift logs for a SOC handoff report.
    
    Ticket Title: {ticket_title}
    Raw Description: {ticket_description}
    
    Task: Provide a concise, professional 2-sentence summary of this incident for the incoming shift analyst.
    Focus on:
    1. Root cause / trigger event.
    2. Current remediation status or immediate next action required.
    
    Do NOT include filler text. Output only the summary points.
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a precise cybersecurity operational reporting assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[!] AI API call failed for ticket '{ticket_title}': {e}")
        return "Summary unavailable (API error)."

# ---------------------------------------------------------------------------
# HTML Generator
# ---------------------------------------------------------------------------
def generate_and_upload_html_report(dataframe):
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total_tickets = len(dataframe)

    ticket_cards_html = ""
    for _, row in dataframe.iterrows():
        ticket_id = row.get('Ticket_ID', 'N/A')
        title = row.get('Title', 'No Title')
        status = str(row.get('Status', 'OPEN')).upper()
        summary = row.get('AI_Shift_Summary', 'No summary available.')
        raw_desc = row.get('Description', 'No raw description provided.')

        badge_class = "badge-open" if status == "OPEN" else "badge-closed"

        card = f"""
        <div class="ticket-card">
            <div class="ticket-header">
                <span class="ticket-id">{ticket_id}</span>
                <span class="ticket-title">{title}</span>
                <span class="badge {badge_class}">{status}</span>
            </div>
            <div class="summary-section">
                <strong>🤖 AI Handoff Executive Summary:</strong>
                <p>{summary}</p>
            </div>
            <details class="raw-details">
                <summary>View Raw Incident Logs / Full Description</summary>
                <p>{raw_desc}</p>
            </details>
        </div>
        """
        ticket_cards_html += card

    # Path to template file inside Lambda
    script_dir = Path(__file__).resolve().parent
    template_path = script_dir / "templates" / "shift_report_template.html"
    
    if not template_path.exists():
        template_path = script_dir / "shift_report_template.html"
        
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found at {template_path}. Ensure 'templates' folder exists in Lambda.")

    with open(template_path, "r", encoding="utf-8") as f:
        template_content = f.read()

    # Perform standard string replacement
    html_content = (
        template_content
        .replace("{NOW}", now)
        .replace("{TOTAL_TICKETS}", str(total_tickets))
        .replace("{TICKET_CARDS_HTML}", ticket_cards_html)
    )

    # Stream HTML output directly to static S3 Web Bucket
    s3_client.put_object(
        Bucket=WEB_BUCKET_NAME,
        Key="index.html",
        Body=html_content,
        ContentType="text/html"
    )
    print(f"[+] Successfully published index.html to S3 bucket: {WEB_BUCKET_NAME}")

# ---------------------------------------------------------------------------
# Lambda Entrypoint Handler
# ---------------------------------------------------------------------------
def lambda_handler(event, context):
    try:
        # Initialize OpenAI client dynamically
        client = get_openai_client("ShiftAutomation/OpenAIKey")

        # Step A: Parse bucket and key from S3 trigger
        record = event['Records'][0]['s3']
        input_bucket = record['bucket']['name']
        csv_file_key = record['object']['key']

        print(f"[*] Ingesting {csv_file_key} from bucket {input_bucket}...")

        # Step B: Read CSV from S3 into memory
        csv_obj = s3_client.get_object(Bucket=input_bucket, Key=csv_file_key)
        csv_bytes = csv_obj['Body'].read()
        df = pd.read_csv(io.BytesIO(csv_bytes))

        # Step C: Filter active tickets
        if 'Status' in df.columns:
            active_tickets = df[df['Status'].str.upper() == 'OPEN'].copy()
        else:
            active_tickets = df.copy()

        # Step D: Summarize tickets
        ai_summaries = []
        for index, row in active_tickets.iterrows():
            title = row.get('Title', 'No Title')
            description = row.get('Description', 'No Description Provided')
            summary = summarize_ticket_with_ai(client, title, description)
            ai_summaries.append(summary)

        active_tickets['AI_Shift_Summary'] = ai_summaries

        # Step E: Generate & Upload HTML Report
        generate_and_upload_html_report(active_tickets)

        return {
            "statusCode": 200,
            "body": f"Shift handoff report published successfully for {csv_file_key}!"
        }

    except Exception as e:
        print(f"[X] Lambda Execution Error: {str(e)}")
        raise e