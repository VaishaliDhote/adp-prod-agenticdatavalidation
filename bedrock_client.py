# ============================================================================
# REUSABLE AS-IS - no rework required. Async Bedrock (Claude) converse client.
# ============================================================================
"""
bedrock_client.py
=================
Async client for the AI Gateway Bedrock (Claude) converse endpoint.

Calls: POST {BEDROCK_BASE_URL}/model/{model_name}/converse
"""

import json
import ssl
import aiohttp
from urllib.parse import quote
from typing import Any, Dict, List


class BedrockClient:
    DEFAULT_TEMPERATURE = 0.0

    def __init__(
        self,
        endpoint: str,
        model_name: str,
        access_token: str,
        ssl_cert_path: str,
        temperature: float = DEFAULT_TEMPERATURE,
    ):
        self.endpoint    = endpoint.rstrip("/")
        self.model_name  = model_name
        self.access_token = access_token
        self.ssl_context = ssl.create_default_context(cafile=ssl_cert_path) if ssl_cert_path else None
        self.temperature = temperature

    async def generate(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 4000,
    ) -> str:
        """
        Call the Bedrock converse endpoint and return the response text.

        Args:
            system_prompt: System instruction for the model.
            user_message:  User content / context.
            max_tokens:    Maximum tokens in the response.

        Returns:
            Response text string.
        """
        print(f"[BedrockClient] Preparing request to Bedrock converse endpoint with model '{self.model_name}'")
        # AI Gateway requires the `bedrock/` provider prefix; URL-encode the
        # model id because it contains a colon (e.g. ...-v1:0-pgo) which some
        # proxies fail to route correctly when left raw.
        encoded_model = quote(self.model_name, safe="")
        url = f"{self.endpoint}/model/{encoded_model}/converse"
        print(f"[BedrockClient] POST {url}")
        body = {
            "system": [{"text": system_prompt}],
            "messages": [{"role": "user", "content": [{"text": user_message}]}],
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": self.temperature,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                data=json.dumps(body),
                ssl=self.ssl_context,
            ) as resp:
                if resp.status != 200:
                    error = await resp.json()
                    raise Exception(f"Bedrock API error {resp.status}: {json.dumps(error)}")
                response = await resp.json()
        response_text = response["output"]["message"]["content"][0]["text"].strip()
        print(f"[BedrockClient] Received response from Bedrock {response_text}")
        return response_text
