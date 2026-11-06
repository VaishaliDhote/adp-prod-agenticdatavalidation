# ============================================================================
# REUSABLE AS-IS - no rework required. Azure OpenAI / Bedrock LLM client.
# ============================================================================
import json, httpx, requests
from openai import AsyncAzureOpenAI
import boto3
from dotenv import load_dotenv
import os
from aws_lambda_powertools import Logger
import asyncio
from bedrock_client import BedrockClient

# TLS verification is intentionally disabled for the internal token endpoint
# (verify=False below). Silence the resulting urllib3 InsecureRequestWarning.
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

_AIGW_SECRET_ARN        = os.getenv("AIGW_SECRET_ARN")
_AIGW_ENDPOINT          = os.getenv("AIGW_ENDPOINT")
_BEDROCK_BASE_URL       = os.getenv("BEDROCK_BASE_URL")
_SSL_CONTEXT            = os.getenv("SSL_CERT_PATH")
_REGION_NAME            = os.getenv("AIGW_REGION_NAME")
_AIGW_MODEL_GPT         = os.getenv("AIGW_MODEL_GPT")
_AIGW_MODEL_ANTHROPIC   = os.getenv("AIGW_MODEL_ANTHROPIC")
_AIGW_MODEL_ANTHROPIC_OPUS = os.getenv("AIGW_MODEL_ANTHROPIC_OPUS")
_AOAI_API_VERSION       = os.getenv("AOAI_API_VERSION_2024_10_21")

_VENDOR_AZURE   = "Azure OpenAI"
_VENDOR_BEDROCK = "Bedrock"


class LLMClient:
    _logger = Logger()

    def __init__(self, session_id="", user_email="", x_request_id="", x_user_session_id="", temperature=0.1):
        self.session_id         = session_id
        self.user_email         = user_email
        self.x_request_id       = x_request_id
        self.x_user_session_id  = x_user_session_id
        self.temperature        = temperature
        self._token             = self._get_bearer_token()

    # ── Token ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _get_AIGW_secrets(secret_arn, secrets_manager=None):
        if not secrets_manager:
            dynamo_db_verify = os.getenv("DYNAMO_DB_VERIFY", "False").lower() == "true"
            secrets_manager = boto3.client("secretsmanager", region_name=_REGION_NAME, verify=dynamo_db_verify)
        secret_dict = json.loads(secrets_manager.get_secret_value(SecretId=secret_arn)["SecretString"])
        return (
            secret_dict["OAI_TENANT_ID"],
            secret_dict["OAI_CLIENT_ID"],
            secret_dict["OAI_CLIENT_SECRET"],
            secret_dict["SCOPE"],
            secret_dict["WEBEX_CLIENT_ID"],
            secret_dict["WEBEX_CLIENT_SECRET"],
        )

    def _get_bearer_token(self):
        """Retrieve bearer token for Azure authentication."""
        azure_tenant_id, azure_client_id, azure_client_secret, _, _, _ = self._get_AIGW_secrets(secret_arn=_AIGW_SECRET_ARN)
        
        url = f'https://login.microsoftonline.com/{azure_tenant_id}/oauth2/v2.0/token'
        form_data = {
            'client_id': azure_client_id,
            'client_secret': azure_client_secret,
            'grant_type': "client_credentials",
            'scope': "0686f7e6-df73-4037-af26-c03f6fc75de4/.default",
        }
        response = requests.post(url, data=form_data, verify=False)
        response.raise_for_status()
        return response.json()

    # ── Client factory ────────────────────────────────────────────────────────

    def _build_azure_client(self) -> AsyncAzureOpenAI:
        return AsyncAzureOpenAI(
            api_version=_AOAI_API_VERSION,
            azure_endpoint=_AIGW_ENDPOINT,
            api_key=self._token["access_token"],
            http_client=httpx.AsyncClient(verify=_SSL_CONTEXT),
        )

    def _build_bedrock_client(self) -> BedrockClient:
        return BedrockClient(
            endpoint=_BEDROCK_BASE_URL,
            model_name=_AIGW_MODEL_ANTHROPIC,
            access_token=self._token["access_token"],
            ssl_cert_path=_SSL_CONTEXT,
            temperature=self.temperature,
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def generate_response(
        self,
        system_prompt: str,
        context: str,
        vendor: str = _VENDOR_AZURE,
        max_tokens: int = 4000,
        timeout: int = 300,
    ) -> str:
        if vendor == _VENDOR_BEDROCK:
            print(f"[LLMClient] 🚀 Sending request to Bedrock with system prompt length {len(system_prompt)} and context length {len(context)}")
            return await asyncio.wait_for(
                self._build_bedrock_client().generate(system_prompt, context, max_tokens),
                timeout=timeout,
            )

        # Azure OpenAI
        client = self._build_azure_client()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": context},
        ]
        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=_AIGW_MODEL_GPT,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                ),
                timeout=timeout,
            )
            return response.choices[0].message.content.strip()
        except asyncio.TimeoutError:
            print(f"[LLMClient] ❌ LLM request timed out after {timeout}s")
            raise TimeoutError(f"LLM request exceeded {timeout} second timeout")

    async def generate_long_response(
        self,
        system_prompt: str,
        context: str,
        vendor: str = _VENDOR_AZURE,
        max_tokens: int = 16000,
        timeout: int = 600,
    ) -> str:
        return await self.generate_response(
            system_prompt, context, vendor=vendor, max_tokens=max_tokens, timeout=timeout
        )
